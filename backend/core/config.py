import os

# ─── WHATSAPP ─────────────────────────────────────────────────
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")

# ─── AI ───────────────────────────────────────────────────────
AI_CONFIDENCE_THRESHOLD_DEFAULT = float(os.environ.get("AI_CONFIDENCE_THRESHOLD", "0.25"))

# ─── OAUTH / SESSION ──────────────────────────────────────────
OAUTH_STATE_TTL_MINUTES = int(os.environ.get("OAUTH_STATE_TTL_MINUTES", "10"))
OAUTH_SESSION_TTL_DAYS = int(os.environ.get("OAUTH_SESSION_TTL_DAYS", "7"))

# ─── EMAIL VERIFICATION ───────────────────────────────────────
EMAIL_VERIFICATION_TTL_MINUTES = int(os.environ.get("EMAIL_VERIFICATION_TTL_MINUTES", "15"))
VERIFICATION_RESEND_COOLDOWN_SECONDS = int(os.environ.get("VERIFICATION_RESEND_COOLDOWN_SECONDS", "120"))
VERIFICATION_RESEND_MAX_ATTEMPTS = int(os.environ.get("VERIFICATION_RESEND_MAX_ATTEMPTS", "2"))
VERIFICATION_RESEND_LOCK_MINUTES = int(os.environ.get("VERIFICATION_RESEND_LOCK_MINUTES", "60"))

# ─── PASSWORD / ACCOUNT ───────────────────────────────────────
PASSWORD_RESET_TTL_MINUTES = int(os.environ.get("PASSWORD_RESET_TTL_MINUTES", "15"))
ACCOUNT_DELETION_TTL_MINUTES = int(os.environ.get("ACCOUNT_DELETION_TTL_MINUTES", "10"))

# ─── CORS ─────────────────────────────────────────────────────
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "")
CORS_ORIGIN_REGEX = r"http://(localhost|127\.0\.0\.1)(:\d+)?"
DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# ─── MISC ─────────────────────────────────────────────────────
ONBOARDING_REMINDER_DELAY_MINUTES = int(os.environ.get("ONBOARDING_REMINDER_DELAY_MINUTES", "15"))
