"""
services/email_service.py
─────────────────────────
Brevo (primary) + SMTP (fallback) email sending.
HTML template renderer for Pulse Engine transactional emails.
No DB access — pure email I/O.
"""

import asyncio
import base64
import html
import json
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException
from shared.config import is_production

logger = logging.getLogger(__name__)

# Root of the repository.
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env", override=False)


# ── Logo helper ────────────────────────────────────────────────────────────────
def get_platform_logo_data_uri() -> str:
    """
    Try to load logo from common locations.
    Returns a data-URI string or empty string if not found.
    """
    candidates = [
        _ROOT / "static" / "logo.jpeg",
        _ROOT / "static" / "logo.png",
        _ROOT / "assets" / "logo.jpeg",
        _ROOT / "assets" / "logo.png",
        _ROOT / "logo.jpeg",
        _ROOT / "logo.png",
        # Legacy path from old project structure
        _ROOT.parent / "frontend" / "src" / "components" / "logo_mail.jpeg",
    ]
    for path in candidates:
        if path.exists():
            try:
                ext = path.suffix.lower().strip(".")
                mime = "jpeg" if ext in ("jpg", "jpeg") else ext
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return f"data:image/{mime};base64,{encoded}"
            except OSError:
                continue
    return ""


# ── HTML email template ────────────────────────────────────────────────────────
def render_platform_email_html(
    title: str,
    intro: str,
    body_lines: List[str],
    cta_label: str = "",
    cta_url: str = "",
    footer_note: str = "",
    accent: str = "#2563eb",
    highlight_label: str = "",
    highlight_value: str = "",
) -> str:
    safe_title = html.escape(title)
    safe_intro = html.escape(intro)
    body_html = "".join(
        f'<p style="margin:0 0 12px;color:#475569;font-size:15px;line-height:1.7;">{html.escape(line)}</p>'
        for line in body_lines
        if line
    )
    logo_uri = get_platform_logo_data_uri()
    logo_html = (
        (
            f'<span style="display:inline-flex;align-items:center;justify-content:center;'
            f"padding:8px 12px;border-radius:14px;background:#ffffff;"
            f'border:1px solid rgba(148,163,184,0.18);box-shadow:0 6px 18px rgba(15,23,42,0.08);">'
            f'<img src="{logo_uri}" alt="Pulse Engine" style="display:block;width:48px;height:auto;" /></span>'
        )
        if logo_uri
        else ""
    )

    highlight_html = (
        (
            f'<div style="margin:22px 0 18px;padding:18px 20px;border-radius:18px;background:#ffffff;'
            f'border:1px solid {accent}33;">'
            f'<div style="color:{accent};font-size:12px;font-weight:800;letter-spacing:0.08em;'
            f'text-transform:uppercase;margin-bottom:10px;">{html.escape(highlight_label)}</div>'
            f'<div style="padding:14px 16px;border-radius:14px;background:{accent}10;'
            f"color:#0f172a;font-size:22px;font-weight:800;"
            f"font-family:'SFMono-Regular',Consolas,monospace;"
            f'letter-spacing:0.16em;text-align:center;">{html.escape(highlight_value)}</div></div>'
        )
        if (highlight_label or highlight_value)
        else ""
    )

    cta_html = (
        (
            f'<div style="margin:24px 0 18px;">'
            f'<a href="{html.escape(cta_url, quote=True)}" '
            f'style="display:inline-block;background:{accent};color:#ffffff;text-decoration:none;'
            f'padding:12px 22px;border-radius:12px;font-weight:700;font-size:14px;">'
            f"{html.escape(cta_label)}</a></div>"
        )
        if (cta_label and cta_url)
        else ""
    )

    footer_html = (
        (f'<p style="margin:18px 0 0;color:#94a3b8;font-size:12px;line-height:1.6;">{html.escape(footer_note)}</p>')
        if footer_note
        else ""
    )

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f8fafc;font-family:system-ui,-apple-system,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:32px 18px;">
    <div style="background:linear-gradient(180deg,#f8fbff 0%,#f8fafc 100%);border:1px solid #dbeafe;
        border-radius:28px;padding:26px 28px 20px;box-shadow:0 18px 45px rgba(15,23,42,0.08);">
      <div style="display:inline-flex;align-items:center;gap:12px;margin-bottom:20px;">
        {logo_html}
        <div>
          <div style="font-size:19px;font-weight:800;color:#0f172a;letter-spacing:-0.02em;">Pulse Engine</div>
          <div style="font-size:12px;color:#64748b;font-weight:700;letter-spacing:0.08em;
              text-transform:uppercase;">CRM</div>
        </div>
      </div>
      <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:22px;padding:30px;">
        <div style="display:inline-flex;align-items:center;padding:7px 12px;border-radius:999px;
            background:#eff6ff;color:{accent};font-size:11px;font-weight:800;letter-spacing:0.08em;
            text-transform:uppercase;margin-bottom:14px;">Account Update</div>
        <h1 style="margin:0 0 14px;color:#0f172a;font-size:30px;line-height:1.1;
            letter-spacing:-0.04em;">{safe_title}</h1>
        <div style="margin:0 0 22px;padding:18px;border-radius:18px;background:{accent}12;border:1px solid {accent}33;">
          <p style="margin:0;color:#0f172a;font-size:16px;line-height:1.75;font-weight:600;">{safe_intro}</p>
        </div>
        {body_html}
        {highlight_html}
        {cta_html}
        {footer_html}
      </div>
    </div>
    <p style="margin:14px 6px 0;color:#94a3b8;font-size:12px;text-align:center;">
      Pulse Engine • Secure account notifications
    </p>
  </div>
</body>
</html>
"""


# ── Senders ────────────────────────────────────────────────────────────────────
def send_email_via_smtp(to_email: str, subject: str, body: str, html_body: str = "") -> None:
    """Send via SMTP (Gmail, Outlook, or any SMTP server)."""
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", os.environ.get("SMTP_PASSWORD", ""))
    smtp_from = os.environ.get("SMTP_FROM", smtp_user).strip()
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

    if not smtp_host or not smtp_from:
        logger.error("SMTP email delivery is not configured correctly.")
        raise HTTPException(
            status_code=500,
            detail="Email delivery is not configured.",
        )

    msg = EmailMessage()
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError):
        logger.exception("SMTP email send failed for %s", to_email)
        raise HTTPException(status_code=500, detail="Email delivery failed.")
    logger.info("SMTP email sent to %s", to_email)


def send_email_via_brevo(to_email: str, subject: str, body: str, html_body: str = "") -> None:
    """Send via Brevo (formerly Sendinblue) transactional email API."""
    api_key = os.environ.get("BREVO_API_KEY", "").strip()
    sender_email = os.environ.get("BREVO_SENDER_EMAIL", "").strip() or os.environ.get("SMTP_FROM", "").strip()
    sender_name = os.environ.get("BREVO_SENDER_NAME", "Pulse Engine").strip() or "Pulse Engine"
    api_url = os.environ.get("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email").strip()

    if not api_key:
        logger.error("Brevo email delivery is not configured: missing BREVO_API_KEY.")
        raise HTTPException(status_code=500, detail="Email delivery is not configured.")
    if not sender_email:
        logger.error("Brevo email delivery is not configured: missing sender email.")
        raise HTTPException(status_code=500, detail="Email delivery is not configured.")

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body,
    }
    if html_body:
        payload["htmlContent"] = html_body

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }
    try:
        resp = httpx.post(api_url, json=payload, headers=headers, timeout=20)
    except httpx.HTTPError:
        logger.exception("Brevo email request failed for %s", to_email)
        raise HTTPException(status_code=500, detail="Email delivery failed.")
    if resp.status_code >= 400:
        logger.error(
            "Brevo email send failed for %s with status %s: %s",
            to_email,
            resp.status_code,
            (resp.text or "")[:300],
        )
        raise HTTPException(status_code=500, detail="Email delivery failed.")
    logger.info("Brevo email sent to %s", to_email)


def send_email(to_email: str, subject: str, body: str, html_body: str = "") -> None:
    """
    Main entry point — routes to Brevo if BREVO_API_KEY is set, otherwise SMTP.
    Called by: db_helpers.py, routers/auth.py, routers/misc.py
    """
    brevo_key = os.environ.get("BREVO_API_KEY", "").strip()
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_from = os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip()
    if brevo_key:
        send_email_via_brevo(
            to_email=to_email,
            subject=subject,
            body=body,
            html_body=html_body,
        )
        return
    if smtp_host and smtp_from:
        send_email_via_smtp(
            to_email=to_email,
            subject=subject,
            body=body,
            html_body=html_body,
        )
        return
    if is_production():
        raise HTTPException(status_code=500, detail="Email delivery is not configured.")
    preview_dir = _ROOT / ".runtime-logs"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / "email-preview.log"
    preview_entry = {
        "to_email": to_email,
        "subject": subject,
        "body": body,
        "html_body": html_body,
    }
    with preview_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(preview_entry, ensure_ascii=True) + "\n")
    logger.warning(
        "Email provider is not configured; wrote preview email to %s",
        preview_path,
    )


async def send_email_async(to_email: str, subject: str, body: str, html_body: str = "") -> None:
    await asyncio.to_thread(send_email, to_email, subject, body, html_body)


def _send_brevo_tenant(
    *,
    api_key: str,
    sender_email: str,
    sender_name: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: str,
) -> None:
    api_url = os.environ.get("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email").strip()
    payload = {
        "sender": {"name": sender_name or "Business", "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body,
    }
    if html_body:
        payload["htmlContent"] = html_body
    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }
    try:
        resp = httpx.post(api_url, json=payload, headers=headers, timeout=20)
    except httpx.HTTPError:
        logger.exception("Tenant Brevo request failed for %s", to_email)
        raise HTTPException(status_code=500, detail="Email delivery failed.") from None
    if resp.status_code >= 400:
        logger.error(
            "Tenant Brevo send failed for %s status=%s body=%s",
            to_email,
            resp.status_code,
            (resp.text or "")[:300],
        )
        raise HTTPException(status_code=500, detail="Email delivery failed.")
    logger.info("Tenant Brevo email sent to %s", to_email)


def _send_smtp_tenant(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    smtp_from: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: str,
    use_tls: bool,
) -> None:
    msg = EmailMessage()
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError):
        logger.exception("Tenant SMTP send failed for %s", to_email)
        raise HTTPException(status_code=500, detail="Email delivery failed.") from None
    logger.info("Tenant SMTP email sent to %s", to_email)


def _send_tenant_email_from_credentials(creds: dict, to_email: str, subject: str, body: str, html_body: str) -> None:
    prov = str(creds.get("email_provider") or "smtp_imap").strip().lower()
    if prov == "brevo":
        api_key = str(creds.get("api_key") or "").strip()
        sender_email = str(creds.get("email_address") or creds.get("smtp_user") or "").strip()
        sender_name = str(creds.get("display_name") or "").strip() or "Business"
        if not api_key or not sender_email:
            raise HTTPException(
                status_code=400,
                detail="Configure Brevo API key and sender email on the Email channel before sending.",
            )
        _send_brevo_tenant(
            api_key=api_key,
            sender_email=sender_email,
            sender_name=sender_name,
            to_email=to_email,
            subject=subject,
            body=body,
            html_body=html_body,
        )
        return

    smtp_host = str(creds.get("smtp_host") or "").strip()
    try:
        smtp_port = int(creds.get("smtp_port") or 587)
    except (TypeError, ValueError):
        smtp_port = 587
    smtp_user = str(creds.get("smtp_user") or "").strip()
    smtp_pass = str(creds.get("smtp_pass_enc") or "").strip()
    smtp_from = str(creds.get("email_address") or smtp_user or "").strip()
    if not smtp_host or not smtp_from:
        raise HTTPException(
            status_code=400,
            detail="Configure SMTP host and from address (mailbox email) on the Email channel before sending.",
        )
    _send_smtp_tenant(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_pass=smtp_pass,
        smtp_from=smtp_from,
        to_email=to_email,
        subject=subject,
        body=body,
        html_body=html_body,
        use_tls=True,
    )


async def send_tenant_email_async(
    db,
    company_id: str,
    *,
    to_email: str,
    subject: str,
    body: str,
    html_body: str = "",
) -> None:
    """
    Send inbox/tenant email using channel_settings for company_id (Brevo or SMTP).
    """
    company_id = (company_id or "").strip()
    to_email = (to_email or "").strip()
    if not company_id or not to_email:
        raise HTTPException(status_code=400, detail="Company and recipient are required for tenant email.")

    row = await db.fetchrow(
        "SELECT * FROM channel_settings WHERE company_id=$1 AND channel='email' AND enabled=TRUE LIMIT 1",
        company_id,
    )
    if not row:
        raise HTTPException(status_code=400, detail="Email channel is not enabled for this workspace.")
    creds = dict(row)
    if creds.get("email_send_enabled") is False:
        raise HTTPException(status_code=400, detail="Email sending is disabled for this channel.")

    await asyncio.to_thread(_send_tenant_email_from_credentials, creds, to_email, subject, body, html_body)
