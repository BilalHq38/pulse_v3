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
from email.utils import formataddr
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


def _email_send_timeout_seconds(default: float = 60.0) -> float:
    try:
        return max(10.0, float(os.environ.get("EMAIL_SEND_TIMEOUT_SECONDS", default)))
    except (TypeError, ValueError):
        return default


def _email_http_timeout() -> httpx.Timeout:
    read_timeout = _email_send_timeout_seconds()
    return httpx.Timeout(read_timeout, connect=3.0, read=read_timeout, write=10.0, pool=5.0)


def _email_header_text(value: str, default: str = "") -> str:
    cleaned = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    return cleaned or default


def _tenant_sender_name(company_name: str, fallback: str = "") -> str:
    base = _email_header_text(company_name) or _email_header_text(fallback) or "Business"
    return f"Message from {base}"


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
        with smtplib.SMTP(smtp_host, smtp_port, timeout=_email_send_timeout_seconds()) as server:
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

    if not api_key:
        logger.error("Brevo email delivery is not configured: missing BREVO_API_KEY.")
        raise HTTPException(status_code=500, detail="Email delivery is not configured.")
    if not sender_email:
        logger.error("Brevo email delivery is not configured: missing sender email.")
        raise HTTPException(status_code=500, detail="Email delivery is not configured.")

    try:
        asyncio.run(
            _send_brevo_async(
                api_key=api_key,
                sender_email=sender_email,
                sender_name=sender_name,
                to_email=to_email,
                subject=subject,
                body=body,
                html_body=html_body,
            )
        )
    except httpx.HTTPError:
        logger.exception("Brevo email request failed for %s", to_email)
        raise HTTPException(status_code=500, detail="Email delivery failed.")


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
    logger.error(
        "No email provider configured — email NOT sent to %s. "
        "Set BREVO_API_KEY or SMTP_HOST to enable email delivery. Preview written to %s.",
        to_email,
        preview_path,
    )


async def _send_brevo_async(
    *,
    api_key: str,
    sender_email: str,
    sender_name: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: str = "",
    label: str = "Brevo",
) -> None:
    api_url = os.environ.get("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email").strip()
    payload = {
        "sender": {"name": sender_name or "Pulse Engine", "email": sender_email},
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
    async with httpx.AsyncClient(timeout=_email_http_timeout()) as client:
        resp = await client.post(api_url, json=payload, headers=headers)
    if resp.status_code >= 400:
        raw_error = (resp.text or "")[:300]
        logger.error(
            "%s email send failed for %s with status %s: %s",
            label,
            to_email,
            resp.status_code,
            raw_error,
        )
        # 4xx = client/config error (bad API key, unverified sender, invalid recipient)
        # 5xx = Brevo-side server error — both surface as 502 Bad Gateway so the
        # caller knows the issue is upstream, not an internal bug.
        user_msg = "Email delivery failed."
        if resp.status_code == 401:
            user_msg = "Email delivery failed: invalid API key. Check your Brevo API key in channel settings."
        elif resp.status_code == 403:
            user_msg = "Email delivery failed: sender address not verified on Brevo. Verify your sender email in Brevo dashboard."
        elif resp.status_code == 400:
            user_msg = f"Email delivery failed: {raw_error[:120]}"
        raise HTTPException(status_code=502, detail=user_msg)
    logger.info("%s email sent to %s", label, to_email)


async def _run_email_with_retries(
    operation,
    *,
    label: str,
    raise_on_failure: bool = False,
    attempts: int = 3,
) -> bool:
    last_exc: Exception | None = None
    max_attempts = max(1, int(attempts or 1))
    for attempt in range(max_attempts):
        try:
            await operation()
            return True
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                logger.error("Email failed after %s attempts label=%s error=%s", max_attempts, label, exc)
                break
            await asyncio.sleep(2**attempt)
    if raise_on_failure and last_exc:
        raise last_exc
    return False


async def send_email_async(
    to_email: str,
    subject: str,
    body: str,
    html_body: str = "",
    *,
    raise_on_failure: bool = False,
    retry_attempts: int = 3,
) -> bool:
    async def _send() -> None:
        brevo_key = os.environ.get("BREVO_API_KEY", "").strip()
        if brevo_key:
            sender_email = os.environ.get("BREVO_SENDER_EMAIL", "").strip() or os.environ.get("SMTP_FROM", "").strip()
            sender_name = os.environ.get("BREVO_SENDER_NAME", "Pulse Engine").strip() or "Pulse Engine"
            if not sender_email:
                raise HTTPException(status_code=500, detail="Email delivery is not configured.")
            await _send_brevo_async(
                api_key=brevo_key,
                sender_email=sender_email,
                sender_name=sender_name,
                to_email=to_email,
                subject=subject,
                body=body,
                html_body=html_body,
            )
            return
        await asyncio.to_thread(send_email, to_email, subject, body, html_body)

    return await _run_email_with_retries(
        _send,
        label="platform",
        raise_on_failure=raise_on_failure,
        attempts=retry_attempts,
    )


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
    try:
        asyncio.run(
            _send_brevo_async(
                api_key=api_key,
                sender_email=sender_email,
                sender_name=sender_name,
                to_email=to_email,
                subject=subject,
                body=body,
                html_body=html_body,
                label="Tenant Brevo",
            )
        )
    except httpx.HTTPError:
        logger.exception("Tenant Brevo request failed for %s", to_email)
        raise HTTPException(status_code=500, detail="Email delivery failed.") from None


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
    sender_name: str = "",
) -> None:
    msg = EmailMessage()
    clean_sender_name = _email_header_text(sender_name)
    msg["From"] = formataddr((clean_sender_name, smtp_from)) if clean_sender_name else smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=_email_send_timeout_seconds()) as server:
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


def _send_tenant_email_from_credentials(
    creds: dict,
    to_email: str,
    subject: str,
    body: str,
    html_body: str,
    sender_name: str = "",
) -> None:
    prov = str(creds.get("email_provider") or "smtp_imap").strip().lower()
    clean_sender_name = _email_header_text(sender_name) or _tenant_sender_name("", str(creds.get("display_name") or ""))
    if prov == "brevo":
        api_key = str(creds.get("api_key") or "").strip()
        sender_email = str(creds.get("email_address") or creds.get("smtp_user") or "").strip()
        if not api_key or not sender_email:
            raise HTTPException(
                status_code=400,
                detail="Email channel is not configured.",
            )
        _send_brevo_tenant(
            api_key=api_key,
            sender_email=sender_email,
            sender_name=clean_sender_name,
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
            detail="Email channel is not configured.",
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
        sender_name=clean_sender_name,
    )


def render_onboarding_email_html(
    user_name: str,
    company_name: str,
    login_url: str,
) -> str:
    """
    Render a professional onboarding welcome email for newly approved users.
    Uses inline styles only; mobile-responsive with max-width 600px.
    """
    accent = "#2563eb"
    safe_name = html.escape(str(user_name or "there"))
    safe_company = html.escape(str(company_name or "your company"))
    safe_login_url = html.escape(str(login_url or ""), quote=True)

    logo_uri = get_platform_logo_data_uri()
    logo_html = (
        f'<img src="{logo_uri}" alt="Pulse Engine" '
        f'style="display:block;width:44px;height:auto;" />'
        if logo_uri
        else ""
    )

    steps = [
        ("1", "Company Settings", "Configure your company profile and core preferences."),
        ("2", "Profile Settings", "Update your personal account details and notifications."),
        ("3", "Business Information", "Enter your business address, tax info, and legal details."),
        ("4", "Branding", "Upload your logo, set brand colors, and customize your appearance."),
        ("5", "AI Configuration", "Connect your AI provider and tune response settings."),
        ("6", "Knowledge Base", "Add FAQs, policies, and documents to train your AI assistant."),
        ("7", "Products", "Add your products or services with images, prices, and descriptions."),
        ("8", "FAQs", "Create a quick-access FAQ list for your customers and AI."),
        ("9", "Channel Configuration", "Connect WhatsApp, email, webchat, and other channels."),
    ]

    steps_html = ""
    for num, title, desc in steps:
        steps_html += (
            f'<div style="display:flex;align-items:flex-start;gap:14px;'
            f'margin-bottom:14px;padding:14px 16px;border-radius:14px;'
            f'background:#f8fafc;border:1px solid #e2e8f0;">'
            f'<div style="flex-shrink:0;width:32px;height:32px;border-radius:50%;'
            f'background:{accent};color:#fff;font-size:14px;font-weight:800;'
            f'display:flex;align-items:center;justify-content:center;'
            f'line-height:32px;text-align:center;">{num}</div>'
            f'<div style="flex:1;">'
            f'<div style="font-size:14px;font-weight:700;color:#0f172a;margin-bottom:2px;">'
            f'{html.escape(title)}</div>'
            f'<div style="font-size:13px;color:#64748b;line-height:1.5;">{html.escape(desc)}</div>'
            f'</div></div>'
        )

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f8fafc;font-family:system-ui,-apple-system,sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:32px 16px;">
    <div style="background:linear-gradient(180deg,#f0f7ff 0%,#f8fafc 100%);
        border:1px solid #bfdbfe;border-radius:28px;padding:28px 28px 24px;
        box-shadow:0 18px 45px rgba(15,23,42,0.09);">

      <!-- Header / Logo -->
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px;">
        {logo_html}
        <div>
          <div style="font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-0.02em;">Pulse Engine</div>
          <div style="font-size:11px;color:#64748b;font-weight:700;letter-spacing:0.08em;
              text-transform:uppercase;">CRM Platform</div>
        </div>
      </div>

      <!-- Main card -->
      <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:22px;padding:30px;">

        <!-- Badge -->
        <div style="display:inline-flex;align-items:center;padding:6px 12px;border-radius:999px;
            background:#eff6ff;color:{accent};font-size:11px;font-weight:800;
            letter-spacing:0.08em;text-transform:uppercase;margin-bottom:18px;">
          Welcome Aboard
        </div>

        <!-- Title -->
        <h1 style="margin:0 0 6px;color:#0f172a;font-size:28px;line-height:1.15;
            letter-spacing:-0.03em;">
          Hello, {safe_name}!
        </h1>
        <p style="margin:0 0 20px;color:#475569;font-size:15px;line-height:1.7;">
          Your account for <strong>{safe_company}</strong> has been approved and is now active.
          You're all set to get started with Pulse Engine CRM.
        </p>

        <!-- Intro highlight -->
        <div style="padding:18px 20px;border-radius:16px;background:{accent}0d;
            border:1px solid {accent}33;margin-bottom:24px;">
          <p style="margin:0;color:#1e40af;font-size:15px;font-weight:600;line-height:1.6;">
            Complete the onboarding steps below to configure your workspace.
            Once all steps are done and channels are connected, your platform will be
            fully operational and ready to engage with customers.
          </p>
        </div>

        <!-- Onboarding steps -->
        <div style="margin-bottom:24px;">
          <div style="font-size:13px;font-weight:800;color:#64748b;letter-spacing:0.06em;
              text-transform:uppercase;margin-bottom:14px;">Onboarding Checklist</div>
          {steps_html}
        </div>

        <!-- Completion note -->
        <div style="padding:14px 18px;border-radius:14px;background:#f0fdf4;
            border:1px solid #bbf7d0;margin-bottom:24px;">
          <p style="margin:0;color:#166534;font-size:14px;font-weight:600;line-height:1.6;">
            Once all 9 steps are configured and your channels are connected,
            your Pulse Engine workspace will be fully operational.
          </p>
        </div>

        <!-- CTA -->
        <div style="margin-bottom:8px;">
          <a href="{safe_login_url}"
             style="display:inline-block;background:{accent};color:#ffffff;
                    text-decoration:none;padding:13px 26px;border-radius:12px;
                    font-weight:700;font-size:15px;letter-spacing:0.01em;">
            Go to Dashboard
          </a>
        </div>

        <p style="margin:18px 0 0;color:#94a3b8;font-size:12px;line-height:1.6;">
          If you did not expect this email or have questions, please contact our support team.
        </p>
      </div>
    </div>

    <p style="margin:14px 6px 0;color:#94a3b8;font-size:12px;text-align:center;">
      Pulse Engine &bull; Secure account notifications
    </p>
  </div>
</body>
</html>
"""


async def send_onboarding_email_async(
    db,
    *,
    user_id: str,
    user_name: str,
    user_email: str,
    company_name: str,
) -> bool:
    """
    Send a one-time onboarding welcome email when a user is approved by super admin.

    Idempotent: checks system_logs for action='onboarding_email_sent' with
    entity_id=user_id before sending.  Records a log entry on success.
    Never raises — any exception is logged and False is returned.
    """
    try:
        user_id = str(user_id or "").strip()
        user_email = str(user_email or "").strip()
        if not user_id or not user_email or "@" not in user_email:
            logger.warning(
                "send_onboarding_email_async skipped: invalid user_id=%s or email=%s",
                user_id,
                user_email,
            )
            return False

        # Idempotency check — look for a previous send record (no company context needed
        # because super_admin system_logs may have empty company_id).
        try:
            already_sent = await db.fetchval(
                "SELECT id FROM system_logs WHERE action='onboarding_email_sent' AND entity_id=$1 LIMIT 1",
                user_id,
            )
        except Exception as check_exc:
            logger.warning(
                "send_onboarding_email_async idempotency check failed user_id=%s error=%s",
                user_id,
                check_exc,
            )
            already_sent = None

        if already_sent:
            logger.info(
                "send_onboarding_email_async skipped — already sent user_id=%s",
                user_id,
            )
            return True

        from shared.config import frontend_url as _frontend_url

        login_url = _frontend_url().rstrip("/") + "/login"
        html_body = render_onboarding_email_html(
            user_name=user_name,
            company_name=company_name,
            login_url=login_url,
        )
        plain_body = (
            f"Welcome to Pulse Engine, {user_name or 'there'}!\n\n"
            f"Your account for {company_name or 'your company'} has been approved.\n"
            "Please log in to complete your onboarding:\n"
            f"{login_url}\n\n"
            "Onboarding steps: Company Settings, Profile Settings, Business Information, "
            "Branding, AI Configuration, Knowledge Base, Products, FAQs, Channel Configuration.\n\n"
            "Once all steps are complete and channels are connected, your platform is fully operational."
        )

        sent = await send_email_async(
            user_email,
            "Welcome to Pulse Engine — Your Account is Active",
            plain_body,
            html_body,
        )
        if not sent:
            logger.warning(
                "send_onboarding_email_async email delivery failed user_id=%s email=%s",
                user_id,
                user_email,
            )
            return False

        # Record the send so we never send it twice.
        try:
            from core.utils import make_id

            log_id = make_id()
            await db.execute(
                "INSERT INTO system_logs(id,user_id,company_id,action,entity_type,entity_id,created_at) "
                "VALUES($1,$2,'',$3,$4,$5,NOW())",
                log_id,
                user_id,
                "onboarding_email_sent",
                "user",
                user_id,
            )
        except Exception as log_exc:
            # Log failure is non-fatal — email was already sent successfully.
            logger.warning(
                "send_onboarding_email_async failed to record log user_id=%s error=%s",
                user_id,
                log_exc,
            )

        logger.info(
            "send_onboarding_email_async sent user_id=%s email=%s",
            user_id,
            user_email,
        )
        return True

    except Exception as exc:
        logger.exception(
            "send_onboarding_email_async unexpected error user_id=%s error=%s",
            user_id,
            exc,
        )
        return False


async def send_tenant_email_async(
    db,
    company_id: str,
    *,
    to_email: str,
    subject: str,
    body: str,
    html_body: str = "",
    raise_on_failure: bool = True,
) -> bool:
    """
    Send inbox/tenant email using channel_settings for company_id (Brevo or SMTP).
    """
    company_id = (company_id or "").strip()
    to_email = (to_email or "").strip()
    if not company_id or not to_email:
        raise HTTPException(status_code=400, detail="Company and recipient are required for tenant email.")

    from shared.database import company_context

    async with company_context(db, company_id):
        row = await db.fetchrow(
            "SELECT * FROM channel_settings WHERE company_id=$1 AND channel='email' AND enabled=TRUE LIMIT 1",
            company_id,
        )
        company_name = str(
            await db.fetchval("SELECT NULLIF(BTRIM(name), '') FROM companies WHERE id=$1 LIMIT 1", company_id) or ""
        )
    if not row:
        raise HTTPException(status_code=400, detail="Email channel is not configured.")
    creds = dict(row)
    if creds.get("email_send_enabled") is False:
        raise HTTPException(status_code=400, detail="Email channel is not configured.")
    sender_name = _tenant_sender_name(company_name, str(creds.get("display_name") or ""))
    subject = _email_header_text(subject) or sender_name

    async def _send() -> None:
        prov = str(creds.get("email_provider") or "smtp_imap").strip().lower()
        if prov == "brevo":
            api_key = str(creds.get("api_key") or "").strip()
            sender_email = str(creds.get("email_address") or creds.get("smtp_user") or "").strip()
            if not api_key or not sender_email:
                raise HTTPException(status_code=400, detail="Email channel is not configured.")
            await _send_brevo_async(
                api_key=api_key,
                sender_email=sender_email,
                sender_name=sender_name,
                to_email=to_email,
                subject=subject,
                body=body,
                html_body=html_body,
                label="Tenant Brevo",
            )
            return
        await asyncio.to_thread(
            _send_tenant_email_from_credentials,
            creds,
            to_email,
            subject,
            body,
            html_body,
            sender_name,
        )

    return await _run_email_with_retries(_send, label="tenant", raise_on_failure=raise_on_failure)
