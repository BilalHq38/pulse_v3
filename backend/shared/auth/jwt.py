from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
from dotenv import load_dotenv
from jose import JWTError, jwt

from shared.config import is_production, is_weak_secret

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env", override=False)
logger = logging.getLogger(__name__)

SECRET_KEY = (os.environ.get("JWT_SECRET") or os.environ.get("SECRET_KEY") or "").strip()
ALGORITHM = (os.environ.get("JWT_ALGORITHM", "HS256") or "HS256").strip().upper()
PRIVATE_KEY = os.environ.get("JWT_PRIVATE_KEY", "").strip()
PUBLIC_KEY = os.environ.get("JWT_PUBLIC_KEY", "").strip()
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "30"))


def _signing_key() -> str:
    if ALGORITHM.startswith("RS"):
        return PRIVATE_KEY
    return SECRET_KEY


def _verification_key() -> str:
    if ALGORITHM.startswith("RS"):
        return PUBLIC_KEY or PRIVATE_KEY
    return SECRET_KEY


def _normalize_company_id(data: dict) -> str:
    return str(data.get("cid") or data.get("company_id") or "").strip()


def _normalize_token_version(data: dict) -> int:
    try:
        return int(data.get("tv", data.get("token_version", 0)) or 0)
    except (TypeError, ValueError):
        return 0


def _validate_key_material() -> None:
    if ALGORITHM.startswith("RS"):
        if not PRIVATE_KEY or not _verification_key():
            raise ValueError("JWT RSA keys are not configured")
        return
    if not SECRET_KEY:
        raise ValueError("JWT secret is not configured")
    if len(SECRET_KEY.encode("utf-8")) < 32 or is_weak_secret(SECRET_KEY):
        if is_production():
            raise ValueError("JWT secret is too weak for production")
        logger.warning("JWT secret is weak; configure a 32+ character non-default secret")


def is_valid_company_id(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except Exception:
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _relaxed_billing_env() -> bool:
    """Mirror billing_helpers.relaxed_billing_env without importing it (avoids import cycles)."""
    for key in ("DEMO_MODE", "STRIPE_OPTIONAL"):
        raw = (os.environ.get(key, "") or "").strip().lower()
        if raw in ("1", "true", "yes", "on", "enabled"):
            return True
    return False


def _enrollment_claims(data: dict) -> tuple[int, int, str]:
    """JWT enrollment flags: onboarding done (ob), plan selected (pl), billing_status (bs)."""
    role = str(data.get("role", "")).strip().lower()
    if role == "super_admin":
        return 1, 1, "active"
    ob = 1 if bool(data.get("onboarding_completed")) else 0
    pl = 1 if bool(data.get("plan_selected")) else 0
    bs = str(data.get("billing_status") or "unpaid").strip().lower() or "unpaid"
    if bs not in ("trial", "active", "unpaid"):
        bs = "unpaid"
    return ob, pl, bs[:16]


def _verification_claim(data: dict) -> int:
    """ev: 1 = email verified (or bypassed for super_admin / demo-relaxed)."""
    role = str(data.get("role", "")).strip().lower()
    if role == "super_admin" or _relaxed_billing_env():
        return 1
    return 1 if bool(data.get("email_verified")) else 0


def _enterprise_invite_claim(data: dict) -> int:
    """ei: 1 = enterprise teammate invite gate satisfied (or not applicable / bypass)."""
    role = str(data.get("role", "")).strip().lower()
    if role == "super_admin" or _relaxed_billing_env():
        return 1
    if data.get("enterprise_invite_satisfied", True) is False:
        return 0
    return 1


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    _validate_key_material()
    company_id = _normalize_company_id(data)
    if not data.get("sub") or not data.get("role") or not company_id:
        raise ValueError("JWT payload must include sub, role, and cid")
    if not is_valid_company_id(company_id):
        raise ValueError("JWT cid must be a valid UUID")
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    ob, pl, bs = _enrollment_claims(data)
    ev = _verification_claim(data)
    ei = _enterprise_invite_claim(data)
    payload = {
        "sub": str(data["sub"]).strip(),
        "cid": company_id,
        "role": str(data["role"]).strip(),
        "tv": _normalize_token_version(data),
        "jti": str(data.get("jti") or uuid.uuid4()),
        "exp": expire,
        "type": "access",
        "ob": ob,
        "pl": pl,
        "bs": bs,
        "ev": ev,
        "ei": ei,
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def create_refresh_token(data: dict, *, token_id: str | None = None) -> str:
    _validate_key_material()
    company_id = _normalize_company_id(data)
    if not data.get("sub") or not data.get("role") or not company_id:
        raise ValueError("JWT payload must include sub, role, and cid")
    if not is_valid_company_id(company_id):
        raise ValueError("JWT cid must be a valid UUID")
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    ob, pl, bs = _enrollment_claims(data)
    ev = _verification_claim(data)
    ei = _enterprise_invite_claim(data)
    payload = {
        "sub": str(data["sub"]).strip(),
        "cid": company_id,
        "role": str(data["role"]).strip(),
        "tv": _normalize_token_version(data),
        "jti": str(token_id or data.get("jti") or uuid.uuid4()),
        "exp": expire,
        "type": "refresh",
        "ob": ob,
        "pl": pl,
        "bs": bs,
        "ev": ev,
        "ei": ei,
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, _verification_key(), algorithms=[ALGORITHM])
        return normalize_token_claims(payload)
    except JWTError:
        return None


def normalize_token_claims(payload: dict | None) -> dict | None:
    if not payload:
        return None
    company_id = _normalize_company_id(payload)
    normalized = {
        "sub": str(payload.get("sub") or "").strip(),
        "cid": company_id,
        "company_id": company_id,
        "role": str(payload.get("role") or "").strip(),
        "token_version": _normalize_token_version(payload),
        "tv": _normalize_token_version(payload),
        "jti": str(payload.get("jti") or "").strip(),
        "type": str(payload.get("type") or "").strip(),
    }
    if "ob" in payload:
        try:
            normalized["ob"] = int(payload.get("ob"))
        except (TypeError, ValueError):
            normalized["ob"] = 0
    if "pl" in payload:
        try:
            normalized["pl"] = int(payload.get("pl"))
        except (TypeError, ValueError):
            normalized["pl"] = 0
    if payload.get("bs") is not None:
        normalized["bs"] = str(payload.get("bs") or "")[:16]
    if "ev" in payload:
        try:
            normalized["ev"] = int(payload.get("ev"))
        except (TypeError, ValueError):
            normalized["ev"] = 1
    else:
        normalized["ev"] = 1
    if "ei" in payload:
        try:
            normalized["ei"] = int(payload.get("ei"))
        except (TypeError, ValueError):
            normalized["ei"] = 1
    else:
        normalized["ei"] = 1
    return normalized
