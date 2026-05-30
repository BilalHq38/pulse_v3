"""
channel_layer/adapters/email.py — Email channel adapter.

Supports:
  - IMAP ingestion (polling incoming emails)
  - SMTP/Brevo sending (outbound via existing email_service.py)
  - Subject, sender, body parsing into UnifiedMessage
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import hashlib
import hmac as hmac_module
import imaplib
import logging
import os
import re
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from fastapi import Request

from channel_layer.base import BaseChannelAdapter
from channel_layer.schemas import (
    AdapterHealthStatus,
    ChannelType,
    MessageDirection,
    SendResult,
    UnifiedAttachment,
    UnifiedMessage,
)
from core.utils import make_id

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(html_text: str) -> str:
    """Convert HTML to plain text by stripping tags."""
    text = _HTML_TAG_RE.sub(" ", html_text or "")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _parse_email_body(msg: email.message.EmailMessage) -> tuple[str, list[dict]]:
    """
    Extract plain text body and attachments from an email.Message.
    Returns (body_text, attachments_list).
    """
    body = ""
    attachments: list[dict] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in disposition:
                attachments.append(
                    {
                        "name": part.get_filename() or "attachment",
                        "mime_type": content_type,
                        "size": len(part.get_payload(decode=True) or b""),
                    }
                )
            elif content_type == "text/plain" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace").strip()
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = _strip_html(payload.decode(charset, errors="replace")).strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            content_type = msg.get_content_type()
            decoded = payload.decode(charset, errors="replace").strip()
            body = _strip_html(decoded) if content_type == "text/html" else decoded

    return body, attachments


class EmailAdapter(BaseChannelAdapter):
    """
    Email channel adapter supporting IMAP ingestion and SMTP/Brevo sending.

    Inbound:
        - Polls IMAP mailbox for new emails
        - Parses subject, sender, body, attachments
        - Converts to UnifiedMessage

    Outbound:
        - Sends via existing email_service.py (SMTP or Brevo)
    """

    channel_type = ChannelType.EMAIL

    def _get_tenant_imap_config(self, credentials: dict) -> dict:
        """Get IMAP config from tenant credentials or env vars."""
        imap_user = (
            (credentials.get("imap_user") or "").strip()
            or (credentials.get("email_address") or "").strip()
            or os.environ.get("IMAP_USER", "").strip()
        )
        imap_pass = (
            (credentials.get("imap_password") or "").strip()
            or (credentials.get("imap_pass_enc") or "").strip()
            or os.environ.get("IMAP_PASS", "").strip()
        )
        try:
            imap_port = int(credentials.get("imap_port") or os.environ.get("IMAP_PORT", "993") or "993")
        except (TypeError, ValueError):
            imap_port = 993
        return {
            "host": (credentials.get("imap_host") or "").strip() or os.environ.get("IMAP_HOST", "").strip(),
            "port": imap_port,
            "user": imap_user,
            "password": imap_pass,
            "use_ssl": credentials.get("imap_use_ssl", True),
            "folder": credentials.get("imap_folder", "INBOX"),
        }

    async def poll_inbox(
        self,
        db,
        tenant_id: str,
        *,
        limit: int = 20,
        mark_as_read: bool = True,
    ) -> list[UnifiedMessage]:
        """
        Poll an IMAP mailbox for unread emails and return as UnifiedMessages.

        This should be called by a background worker, not a webhook endpoint.
        """
        # Load per-tenant IMAP credentials
        credentials: dict[str, Any] = {}
        try:
            row = await db.fetchrow(
                "SELECT * FROM channel_settings WHERE company_id=$1 AND channel='email' AND enabled=TRUE "
                "AND COALESCE(email_receive_enabled, TRUE) = TRUE LIMIT 1",
                tenant_id,
            )
            if row:
                credentials = dict(row)
        except Exception as exc:
            logger.warning("Failed to load email credentials for tenant %s: %s", tenant_id, exc)

        config = self._get_tenant_imap_config(credentials)
        if not config["host"] or not config["user"]:
            logger.debug("IMAP not configured for tenant %s", tenant_id)
            return []

        messages: list[UnifiedMessage] = []
        try:
            raw_messages = await asyncio.to_thread(
                self._fetch_imap_messages,
                config,
                limit=limit,
                mark_as_read=mark_as_read,
            )
            for raw_msg in raw_messages:
                try:
                    unified = self._parse_raw_email(raw_msg, tenant_id)
                    messages.append(unified)
                except Exception as exc:
                    logger.warning("Failed to parse email: %s", exc)
        except Exception as exc:
            logger.error("IMAP poll failed for tenant %s: %s", tenant_id, exc)

        return messages

    def _fetch_imap_messages(
        self,
        config: dict,
        *,
        limit: int = 20,
        mark_as_read: bool = True,
    ) -> list[email.message.EmailMessage]:
        """Synchronous IMAP fetch — run in thread pool."""
        messages: list[email.message.EmailMessage] = []
        connection: imaplib.IMAP4_SSL | imaplib.IMAP4 | None = None

        try:
            if config.get("use_ssl", True):
                connection = imaplib.IMAP4_SSL(config["host"], config["port"])
            else:
                connection = imaplib.IMAP4(config["host"], config["port"])

            connection.login(config["user"], config["password"])
            connection.select(config.get("folder", "INBOX"))

            status, data = connection.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return messages

            msg_ids = data[0].split()[-limit:]  # Most recent first

            for msg_id in msg_ids:
                status, msg_data = connection.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0]
                if isinstance(raw_email, tuple) and len(raw_email) >= 2:
                    msg = email.message_from_bytes(
                        raw_email[1],
                        policy=email.policy.default,
                    )
                    messages.append(msg)

                    if mark_as_read:
                        connection.store(msg_id, "+FLAGS", "\\Seen")

        except Exception as exc:
            logger.error("IMAP connection error: %s", exc)
        finally:
            if connection:
                try:
                    connection.close()
                    connection.logout()
                except Exception:
                    pass

        return messages

    def _parse_raw_email(
        self,
        msg: email.message.EmailMessage,
        tenant_id: str,
    ) -> UnifiedMessage:
        """Convert a parsed email.Message into a UnifiedMessage."""
        sender_name, sender_email = parseaddr(str(msg.get("From", "")))
        subject = str(msg.get("Subject", "")).strip()
        message_id_header = str(msg.get("Message-ID", "")).strip()

        # Parse date
        timestamp = datetime.now(timezone.utc)
        date_header = msg.get("Date")
        if date_header:
            try:
                timestamp = parsedate_to_datetime(str(date_header))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except Exception:
                pass

        body, raw_attachments = _parse_email_body(msg)

        attachments = [
            UnifiedAttachment(
                type="document",
                name=att.get("name", ""),
                mime_type=att.get("mime_type", ""),
                size=att.get("size", 0),
            )
            for att in raw_attachments
        ]

        # Reply detection
        reply_to = ""
        in_reply_to = str(msg.get("In-Reply-To", "")).strip()
        if in_reply_to:
            reply_to = in_reply_to

        mid_norm = message_id_header.strip().strip("<>")[:800]
        if mid_norm:
            stable_message_id = mid_norm
        else:
            fingerprint = f"{tenant_id}|{sender_email}|{subject}|{timestamp.isoformat()}".encode("utf-8", errors="replace")
            stable_message_id = "imap:" + hashlib.sha256(fingerprint).hexdigest()

        return UnifiedMessage(
            message_id=stable_message_id,
            tenant_id=tenant_id,
            user_id=sender_email,
            external_user_id=sender_email,
            channel_type=ChannelType.EMAIL,
            direction=MessageDirection.INBOUND,
            content=body,
            subject=subject,
            timestamp=timestamp,
            metadata={
                "sender_name": sender_name,
                "sender_email": sender_email,
                "message_id_header": message_id_header,
                "to": str(msg.get("To", "")).strip(),
                "cc": str(msg.get("Cc", "")).strip(),
            },
            attachments=attachments,
            reply_to_message_id=reply_to,
        )

    async def receive_message(
        self,
        payload: dict,
        db,
        tenant_id: str,
    ) -> UnifiedMessage:
        """
        Receive a single email from a webhook or polling result.

        For webhook-based email (e.g., SendGrid inbound parse, Mailgun),
        the payload is the parsed request body.
        """
        timestamp = datetime.now(timezone.utc)
        raw_date = payload.get("date") or payload.get("timestamp") or ""
        if raw_date:
            try:
                if isinstance(raw_date, (int, float)):
                    ts_val = int(raw_date)
                    if ts_val > 10_000_000_000:
                        ts_val = ts_val // 1000
                    timestamp = datetime.fromtimestamp(ts_val, tz=timezone.utc)
                else:
                    timestamp = parsedate_to_datetime(str(raw_date))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
            except Exception:
                pass

        return UnifiedMessage(
            message_id=payload.get("message_id") or make_id(),
            tenant_id=tenant_id,
            user_id=payload.get("from_email") or payload.get("sender", ""),
            external_user_id=payload.get("from_email") or payload.get("sender", ""),
            channel_type=ChannelType.EMAIL,
            direction=MessageDirection.INBOUND,
            content=payload.get("body") or payload.get("text") or "",
            subject=payload.get("subject", ""),
            timestamp=timestamp,
            metadata={
                "sender_name": payload.get("from_name", ""),
                "sender_email": payload.get("from_email", ""),
                "to": payload.get("to", ""),
                "cc": payload.get("cc", ""),
            },
        )

    async def send_message(
        self,
        message: UnifiedMessage,
        db,
    ) -> SendResult:
        """Send using tenant channel_settings (Brevo or SMTP)."""
        from services.email_service import send_tenant_email_async

        to_email = message.external_user_id or message.metadata.get("to_email", "")
        if not to_email or "@" not in to_email:
            return SendResult(
                success=False,
                error="Valid recipient email address is required",
                channel_type=ChannelType.EMAIL,
            )

        subject = message.subject or ""
        tenant_id = (message.tenant_id or "").strip()
        if not tenant_id:
            return SendResult(
                success=False,
                error="Missing tenant for outbound email",
                channel_type=ChannelType.EMAIL,
            )

        if message.attachments:
            logger.warning(
                "email_adapter_attachments_not_sent count=%d to=%s tenant=%s attachment_urls=%s",
                len(message.attachments),
                to_email,
                tenant_id,
                [a.url for a in message.attachments if a.url],
            )
        try:
            await send_tenant_email_async(
                db,
                tenant_id,
                to_email=to_email,
                subject=subject,
                body=message.content,
                html_body=str(message.metadata.get("html_body") or ""),
            )
            return SendResult(
                success=True,
                channel_type=ChannelType.EMAIL,
            )
        except Exception as exc:
            logger.error("Email send failed to %s: %s", to_email, exc)
            return SendResult(
                success=False,
                error=str(exc),
                channel_type=ChannelType.EMAIL,
            )

    async def validate_webhook(
        self,
        request: Request,
        db,
        raw_body: bytes,
    ) -> bool:
        """
        Validate email webhook (SendGrid/Mailgun inbound parse).
        Basic implementation — extend for specific provider verification.
        """
        expected_secret = (os.environ.get("EMAIL_WEBHOOK_SECRET") or "").strip()
        if not expected_secret:
            from shared.config import is_production

            return not is_production()

        provided = (request.headers.get("X-Webhook-Secret") or "").strip()
        return bool(provided) and hmac_module.compare_digest(provided, expected_secret)

    async def health_check(self) -> AdapterHealthStatus:
        """Check if SMTP/IMAP is configured."""
        from shared.config import is_production

        smtp_ok = bool(os.environ.get("SMTP_HOST", "").strip() or os.environ.get("BREVO_API_KEY", "").strip())
        imap_ok = bool(os.environ.get("IMAP_HOST", "").strip())

        return AdapterHealthStatus(
            adapter="EmailAdapter",
            channel_type=ChannelType.EMAIL,
            healthy=smtp_ok or not is_production(),
            detail=f"smtp={'ok' if smtp_ok else 'unconfigured'} imap={'ok' if imap_ok else 'unconfigured'}",
        )
