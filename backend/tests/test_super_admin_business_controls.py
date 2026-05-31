import pytest
from fastapi import HTTPException

from services import public_signup_service
from services.billing_helpers import get_plan
from services.db_helpers import build_user_payload
from routers import misc as misc_router_module
from shared.billing_guard import check_user_status_active
from shared.usage_guard import (
    CONVERSATION_LIMIT_REACHED_CODE,
    raise_conversation_limit_completed,
)


class _Url:
    def __init__(self, path):
        self.path = path


class _State:
    pass


class _App:
    pass


class _Request:
    def __init__(self, path, user_id="user-1", company_id="company-1"):
        self.url = _Url(path)
        self.state = _State()
        self.state.auth_context = {"sub": user_id, "company_id": company_id, "role": "admin"}
        self.app = _App()


class _StatusDb:
    def __init__(self, status):
        self.status = status

    async def fetchrow(self, *_args):
        return {
            "id": "user-1",
            "company_id": "company-1",
            "status": self.status,
            "role": "admin",
            "onboarding_completed": True,
            "plan_selected": True,
        }


@pytest.mark.asyncio
async def test_pending_user_cannot_access_protected_api():
    request = _Request("/api/conversations")
    request.app.state = _State()
    request.app.state.db = _StatusDb("pending_approval")

    with pytest.raises(HTTPException) as exc:
        await check_user_status_active(request)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ACCOUNT_PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_pending_user_can_continue_onboarding_api():
    request = _Request("/api/auth/onboarding/complete")
    request.app.state = _State()
    request.app.state.db = _StatusDb("pending_approval")

    await check_user_status_active(request)


@pytest.mark.asyncio
async def test_rejected_user_cannot_access_protected_api():
    request = _Request("/api/settings/company")
    request.app.state = _State()
    request.app.state.db = _StatusDb("rejected")

    with pytest.raises(HTTPException) as exc:
        await check_user_status_active(request)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ACCOUNT_REJECTED"


def test_auth_payload_exposes_account_status_for_frontend_routing():
    payload = build_user_payload(
        {
            "id": "user-1",
            "email": "new@example.com",
            "company_id": "00000000-0000-0000-0000-000000000123",
            "status": "pending_approval",
        }
    )

    assert payload["status"] == "pending_approval"
    assert payload["account_status"] == "pending_approval"


def test_public_signup_creates_active_user():
    source = public_signup_service._complete_pending_signup_workspace.__code__.co_consts
    assert any("public_signup_user_activated" in str(item) for item in source)


@pytest.mark.asyncio
async def test_register_status_passes_request_to_paid_signup_fallback(monkeypatch):
    async def noop(*_args, **_kwargs):
        return None

    pending = {
        "id": "pending-1",
        "stripe_checkout_session_id": "cs_test_123",
        "user_id": "",
        "status": "checkout_created",
        "payment_status": "pending",
        "email": "paid@example.com",
        "plan_code": "pro",
        "verification_error": "",
    }
    seen = {}

    async def fake_pending_by_session(_db, _session_id):
        return pending

    async def fake_try_finalize(db, request, pending_signup, session_id):
        seen["db"] = db
        seen["request"] = request
        seen["pending_signup"] = pending_signup
        seen["session_id"] = session_id

    monkeypatch.setattr(public_signup_service, "ensure_pending_signup_primitives", noop)
    monkeypatch.setattr(public_signup_service, "_expire_stale_pending_signups", noop)
    monkeypatch.setattr(public_signup_service, "_pending_signup_by_session", fake_pending_by_session)
    monkeypatch.setattr(public_signup_service, "_try_finalize_paid_signup_from_stripe", fake_try_finalize)

    request = _Request("/api/auth/register/status")
    db = object()
    result = await public_signup_service.get_public_registration_status(db, "cs_test_123", request)

    assert seen["db"] is db
    assert seen["request"] is request
    assert seen["pending_signup"] == pending
    assert seen["session_id"] == "cs_test_123"
    assert result["account_created"] is False


@pytest.mark.asyncio
async def test_register_status_waits_for_active_workspace_before_redirect(monkeypatch):
    async def noop(*_args, **_kwargs):
        return None

    pending = {
        "id": "pending-1",
        "stripe_checkout_session_id": "cs_test_123",
        "user_id": "user-1",
        "status": "account_created",
        "payment_status": "paid",
        "email": "paid@example.com",
        "plan_code": "pro",
        "verification_error": "",
    }
    user = {
        "id": "user-1",
        "company_id": "company-1",
        "status": "pending_approval",
        "email_verified": False,
    }

    class Db:
        async def fetchrow(self, query, *_args):
            if "SELECT * FROM users" in query:
                return user
            return None

    async def fake_pending_by_session(_db, _session_id):
        return pending

    monkeypatch.setattr(public_signup_service, "ensure_pending_signup_primitives", noop)
    monkeypatch.setattr(public_signup_service, "_expire_stale_pending_signups", noop)
    monkeypatch.setattr(public_signup_service, "_pending_signup_by_session", fake_pending_by_session)
    monkeypatch.setattr(public_signup_service, "_ensure_public_signup_workspace_records", noop)

    result = await public_signup_service.get_public_registration_status(Db(), "cs_test_123", _Request("/api/auth/register/status"))

    assert result["account_created"] is False
    assert result["setup_failed"] is True
    assert result["user_status"] == "pending_approval"
    assert "Contact support" in result["support_message"]


def test_starter_plan_alias_maps_to_free_plan():
    assert get_plan("starter")["code"] == "free"


def test_conversation_limit_error_is_structured():
    with pytest.raises(HTTPException) as exc:
        raise_conversation_limit_completed({"limit": 5, "used": 5})

    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == CONVERSATION_LIMIT_REACHED_CODE


@pytest.mark.asyncio
async def test_normal_user_cannot_view_visitor_tracking(monkeypatch):
    async def fake_current_user(_request):
        return {"sub": "user-1", "role": "admin", "company_id": "company-1"}

    async def fake_schema(_db):
        return None

    monkeypatch.setattr(misc_router_module, "get_current_user_flexible", fake_current_user)
    monkeypatch.setattr(misc_router_module, "_ensure_visitor_tracking_schema", fake_schema)
    request = _Request("/api/visitor/tracking")
    request.app.state = _State()
    request.app.state.db = object()

    with pytest.raises(HTTPException) as exc:
        await misc_router_module.list_visitor_tracking(request)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_do_not_track_visitor_request_is_skipped():
    class _Headers(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    class _Response:
        def set_cookie(self, *_args, **_kwargs):
            raise AssertionError("DNT requests should not set visitor cookies")

    request = _Request("/api/visitor/track")
    request.headers = _Headers({"dnt": "1"})
    request.json = lambda: {}

    result = await misc_router_module.track_visitor(request, _Response())

    assert result == {"ok": True, "skipped": True, "reason": "do_not_track"}
