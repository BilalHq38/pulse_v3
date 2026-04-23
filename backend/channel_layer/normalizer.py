"""
channel_layer/normalizer.py — Message normalization pipeline.

Ensures all inbound messages pass through sanitization, enrichment,
and tenant resolution before reaching business services.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from channel_layer.schemas import ChannelType, UnifiedMessage

logger = logging.getLogger(__name__)

_EXCESSIVE_WHITESPACE = re.compile(r"\s{3,}")
_MAX_CONTENT_LENGTH = 10_000


def _normalize_phone_simple(value: str | None) -> str | None:
    """Quick local phone normalizer (last-10-digits strategy).

    Mirrors the identity service util so CRM customer lookups can find records
    regardless of whether they were stored with a trunk prefix, IDD prefix, or
    international format.
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    return digits[-10:] if len(digits) >= 10 else digits


def _normalize_email_simple(value: str | None) -> str | None:
    """Quick local email normalizer (lowercase + strip plus-addressing)."""
    if not value:
        return None
    email = value.strip().lower()
    if "@" not in email:
        return email or None
    local, _, domain = email.partition("@")
    local = local.split("+")[0]
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
    return f"{local}@{domain}" if local else None


class MessageNormalizer:
    """
    Pipeline that normalizes inbound UnifiedMessages before they reach
    business services.

    Steps:
        1. Sanitize content (trim, limit length)
        2. Ensure required fields
        3. Enrich metadata
        4. Resolve tenant identity
    """

    async def normalize(
        self,
        message: UnifiedMessage,
        db=None,
    ) -> UnifiedMessage:
        """Run the full normalization pipeline."""
        message = self._sanitize_content(message)
        message = self._ensure_required_fields(message)
        if db:
            message = await self._resolve_customer_identity(message, db)
        return message

    def _sanitize_content(self, message: UnifiedMessage) -> UnifiedMessage:
        """Clean and limit message content."""
        content = (message.content or "").strip()
        content = _EXCESSIVE_WHITESPACE.sub("  ", content)

        if len(content) > _MAX_CONTENT_LENGTH:
            content = content[:_MAX_CONTENT_LENGTH].rstrip() + "…"

        message.content = content

        # Sanitize subject for email
        if message.subject:
            message.subject = (message.subject or "").strip()[:500]

        return message

    def _ensure_required_fields(self, message: UnifiedMessage) -> UnifiedMessage:
        """Ensure critical fields are populated."""
        if not message.message_id:
            from core.utils import make_id

            message.message_id = make_id()

        if not message.timestamp:
            message.timestamp = datetime.now(timezone.utc)

        if not message.tenant_id:
            logger.warning(
                "UnifiedMessage %s has no tenant_id — this will fail downstream",
                message.message_id,
            )

        if not message.user_id:
            message.user_id = str(message.external_user_id or "").strip()
        if not message.external_user_id and message.user_id:
            message.external_user_id = str(message.user_id or "").strip()

        return message

    async def _resolve_customer_identity(
        self,
        message: UnifiedMessage,
        db,
    ) -> UnifiedMessage:
        """
        Attempt to resolve the external_user_id to an existing customer.
        Uses phone/email matching against the customers table.

        Result is cached in Redis for 30s per (tenant, channel, external_id)
        to prevent thundering-herd re-lookups for a chatty user.
        """
        if message.resolved_customer_id or not message.tenant_id:
            return message

        # Web chat: customer_id is often embedded in metadata by the widget
        if message.channel_type == ChannelType.WEB_CHAT:
            metadata_customer_id = str(message.metadata.get("customer_id") or "").strip()
            if metadata_customer_id:
                message.resolved_customer_id = metadata_customer_id
                return message

        external_id = (message.external_user_id or "").strip()
        if not external_id:
            return message

        # Short-TTL cache for identity resolution.
        cache = None
        cache_key = f"idres:{message.tenant_id}:{message.channel_type.value}:{external_id}"
        try:
            from shared.cache import get_cache_client

            cache = get_cache_client(namespace="identity")
            cached = await cache.get_json(cache_key)
            if isinstance(cached, dict) and cached.get("customer_id"):
                message.resolved_customer_id = str(cached["customer_id"])
                return message
        except Exception:
            cache = None

        async def _set_resolved(customer_id_value: str) -> UnifiedMessage:
            message.resolved_customer_id = str(customer_id_value)
            if cache is not None:
                try:
                    await cache.set_json(
                        cache_key,
                        {"customer_id": message.resolved_customer_id},
                        ttl_seconds=30,
                    )
                except Exception:
                    pass
            return message

        try:
            # Try phone match (WhatsApp, SMS) — try multiple normalized forms
            if message.channel_type == ChannelType.WHATSAPP:
                normalized_phone = _normalize_phone_simple(external_id)
                phone_candidates = list(dict.fromkeys([p for p in [external_id, normalized_phone] if p]))
                for phone_value in phone_candidates:
                    customer_id = await db.fetchval(
                        "SELECT id FROM customers WHERE company_id=$1 AND phone=$2 LIMIT 1",
                        message.tenant_id,
                        phone_value,
                    )
                    if customer_id:
                        return await _set_resolved(customer_id)

                # Last-10-digits fallback — handles stored numbers that include
                # country code / spaces / dashes while the webhook delivers the
                # raw E.164 form (or vice versa).
                import re as _re_norm

                digits = _re_norm.sub(r"\D", "", external_id)
                last10 = digits[-10:] if len(digits) >= 10 else digits
                if last10:
                    customer_id = await db.fetchval(
                        "SELECT id FROM customers "
                        "WHERE company_id=$1 "
                        "AND regexp_replace(phone, '\\D', '', 'g') LIKE $2 "
                        "LIMIT 1",
                        message.tenant_id,
                        f"%{last10}",
                    )
                    if customer_id:
                        return await _set_resolved(customer_id)

            # Try email match — try raw and normalized forms (case-insensitive)
            elif message.channel_type == ChannelType.EMAIL:
                normalized_email = _normalize_email_simple(external_id)
                email_candidates = list(dict.fromkeys([e for e in [external_id, normalized_email] if e]))
                for email_value in email_candidates:
                    customer_id = await db.fetchval(
                        "SELECT id FROM customers WHERE company_id=$1 AND LOWER(email)=$2 LIMIT 1",
                        message.tenant_id,
                        str(email_value).lower(),
                    )
                    if customer_id:
                        return await _set_resolved(customer_id)

            # Try social profile match (Instagram, Facebook)
            elif message.channel_type in (ChannelType.INSTAGRAM, ChannelType.FACEBOOK):
                customer_id = await db.fetchval(
                    "SELECT customer_id FROM customer_social_profiles WHERE platform=$1 AND profile_id=$2 LIMIT 1",
                    message.channel_type.value,
                    external_id,
                )
                if customer_id:
                    return await _set_resolved(customer_id)

        except Exception as exc:
            logger.warning(
                "Customer identity resolution failed channel=%s external_id=%s tenant=%s: %s",
                message.channel_type.value,
                external_id,
                message.tenant_id,
                exc,
            )

        return message
