import os
import json
import re
import time
import uuid
from pathlib import Path

import pytest
import requests

# Auth/signup + onboarding/billing critical public flow tests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")


def _require_base_url() -> str:
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL is not set")
    return BASE_URL.rstrip("/")


def _api(path: str) -> str:
    return f"{_require_base_url()}/api{path}"


@pytest.fixture(scope="session")
def http_session():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="session")
def flow_state():
    return {}


def _extract_latest_verification_token(email: str) -> str:
    log_path = Path("/app/.runtime-logs/email-preview.log")
    pattern = re.compile(r"#token=([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
    email_lc = (email or "").strip().lower()

    for _ in range(8):
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in reversed(lines):
                if email_lc not in line.lower():
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    payload = {}
                body = str(payload.get("body") or "")
                html_body = str(payload.get("html_body") or "")
                for candidate in (body, html_body, line):
                    match = pattern.search(candidate)
                    if match:
                        return match.group(1).strip()
        time.sleep(1)
    return ""


def test_healthz_endpoint(http_session):
    response = http_session.get(_api("/healthz"), timeout=20)
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("status") == "ok"
    assert isinstance(payload, dict)


def test_signup_verify_onboarding_billing_flow(http_session, flow_state):
    email = f"fullflow_{uuid.uuid4().hex[:10]}@example.com"
    password = "FullFlow#123"
    register_payload = {
        "name": "Test Full Flow",
        "email": email,
        "password": password,
        "company_name": f"TEST Co {uuid.uuid4().hex[:4]}",
        "company_industry": "Technology",
        "plan_code": "pro",
        "timezone": "UTC",
    }

    register_response = http_session.post(_api("/auth/register"), json=register_payload, timeout=30)
    assert register_response.status_code == 200
    register_data = register_response.json()
    assert register_data.get("status") in {"account_created", "checkout_required"}
    assert register_data.get("email") == email

    # Current environment expects Stripe disabled and email verification required.
    assert register_data.get("billing_mode") in {"trial_no_payment", "trial_relaxed", None}
    ev = register_data.get("email_verification") or {}
    assert ev.get("required") is True
    assert ev.get("email") == email
    assert isinstance(ev.get("remaining_attempts"), int)

    verification_token = _extract_latest_verification_token(email)
    assert isinstance(verification_token, str) and len(verification_token) > 20

    verify_response = http_session.get(
        _api(f"/auth/verify-email?token={verification_token}"),
        timeout=30,
    )
    assert verify_response.status_code == 200
    verify_data = verify_response.json()
    assert isinstance(verify_data.get("token"), str) and len(verify_data.get("token", "")) > 20
    assert verify_data.get("user", {}).get("email") == email
    assert verify_data.get("user", {}).get("email_verified") is True
    assert verify_data.get("next_path") in {"/onboarding", "/dashboard"}

    # Idempotency check: calling verify again with same token should not fail.
    verify_repeat = http_session.get(
        _api(f"/auth/verify-email?token={verification_token}"),
        timeout=30,
    )
    assert verify_repeat.status_code == 200
    verify_repeat_data = verify_repeat.json()
    assert verify_repeat_data.get("user", {}).get("email") == email
    assert verify_repeat_data.get("user", {}).get("email_verified") is True

    auth_token = verify_data.get("token")
    auth_headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}

    onboarding_profile = {
        "company_name": "TEST Updated Company",
        "industry": "SaaS",
        "preferred_channels": ["email", "web_chat"],
    }
    onboarding_profile_response = http_session.patch(
        _api("/auth/onboarding/profile"),
        json=onboarding_profile,
        headers=auth_headers,
        timeout=30,
    )
    assert onboarding_profile_response.status_code == 200
    assert onboarding_profile_response.json().get("status") == "ok"

    onboarding_complete_response = http_session.put(
        _api("/auth/onboarding/complete"),
        json={},
        headers=auth_headers,
        timeout=30,
    )
    assert onboarding_complete_response.status_code == 200
    onboarding_complete_data = onboarding_complete_response.json()
    assert onboarding_complete_data.get("user", {}).get("onboarding_completed") is True

    billing_response = http_session.post(
        _api("/auth/billing/plan"),
        json={"mode": "free_trial"},
        headers={
            "Authorization": f"Bearer {onboarding_complete_data.get('token')}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    assert billing_response.status_code == 200
    billing_data = billing_response.json()
    assert billing_data.get("user", {}).get("plan_selected") is True
    assert billing_data.get("user", {}).get("onboarding_completed") is True
    assert billing_data.get("user", {}).get("billing_status") in {"trial", "active"}

    session_response = http_session.post(
        _api("/auth/session"),
        json={},
        headers={
            "Authorization": f"Bearer {billing_data.get('token')}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    assert session_response.status_code == 200
    session_data = session_response.json()
    assert session_data.get("user", {}).get("email") == email
    assert session_data.get("user", {}).get("plan_selected") is True

    flow_state["new_user_email"] = email
    flow_state["new_user_password"] = password


def test_stable_login_for_saved_admin_account(http_session):
    credentials = {
        "email": "fullflow1776957479@example.com",
        "password": "FullFlow#123",
        "workspace": "",
    }
    response = http_session.post(_api("/auth/login"), json=credentials, timeout=30)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data.get("token"), str) and len(data.get("token", "")) > 20
    assert data.get("user", {}).get("email") == credentials["email"]
    assert data.get("user", {}).get("email_verified") is True


def test_consolidated_sql_file_exists_and_non_empty():
    sql_path = Path("/app/backend/postgresql_consolidated.sql")
    schema_path = Path("/app/backend/sql_schema.sql")
    assert sql_path.exists() is True
    assert schema_path.exists() is True
    assert sql_path.stat().st_size > 1000
    assert schema_path.stat().st_size > 1000


def test_create_verified_onboarding_pending_user_for_ui(http_session):
    email = f"ui_onboarding_{uuid.uuid4().hex[:10]}@example.com"
    password = "FullFlow#123"
    register_payload = {
        "name": "UI Onboarding",
        "email": email,
        "password": password,
        "company_name": f"UI Pending {uuid.uuid4().hex[:4]}",
        "company_industry": "Technology",
        "plan_code": "pro",
        "timezone": "UTC",
    }

    register_response = http_session.post(_api("/auth/register"), json=register_payload, timeout=30)
    assert register_response.status_code == 200

    verification_token = _extract_latest_verification_token(email)
    assert isinstance(verification_token, str) and len(verification_token) > 20

    verify_response = http_session.get(
        _api(f"/auth/verify-email?token={verification_token}"),
        timeout=30,
    )
    assert verify_response.status_code == 200
    verify_data = verify_response.json()
    assert verify_data.get("user", {}).get("email") == email
    assert verify_data.get("user", {}).get("onboarding_completed") is False

    output = Path("/app/test_reports/ui_seed_credentials.json")
    output.write_text(
        json.dumps(
            {
                "email": email,
                "password": password,
                "status": "verified_onboarding_pending",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert output.exists() is True
