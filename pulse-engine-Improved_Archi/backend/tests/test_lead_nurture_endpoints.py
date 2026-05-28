from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import leads

COMPANY_1 = "11111111-1111-1111-1111-111111111111"
COMPANY_2 = "22222222-2222-2222-2222-222222222222"


class FakeRequest:
    def __init__(self, db, body=None):
        self.app = SimpleNamespace(state=SimpleNamespace(db=db))
        self._body = body or {}

    async def json(self):
        return self._body


class FakeNurtureDb:
    def __init__(self, *, company_id=COMPANY_1, lead_exists=True, message_exists=True):
        self.company_id = company_id
        self.lead_exists = lead_exists
        self.message_exists = message_exists
        self.executed = []

    async def fetchval(self, query, *args):
        if "FROM leads" in query:
            lead_id, company_id = args[0], args[1]
            if lead_id == "lead-1" and company_id == self.company_id and self.lead_exists:
                return "lead-1"
        return None

    async def fetchrow(self, query, *args):
        if "FROM lead_nurture_messages" in query and self.message_exists:
            message_id, lead_id, company_id = args[0], args[1], args[2]
            if message_id == "message-1" and lead_id == "lead-1" and company_id == self.company_id:
                return {
                    "id": "message-1",
                    "lead_id": "lead-1",
                    "company_id": company_id,
                    "message": "Updated message",
                    "phase": "awareness",
                }
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if query.strip().upper().startswith("DELETE") and self.message_exists:
            return "DELETE 1"
        if query.strip().upper().startswith("DELETE"):
            return "DELETE 0"
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_update_nurture_message_is_company_scoped(monkeypatch):
    db = FakeNurtureDb(company_id=COMPANY_1)

    async def fake_current_user(_request):
        return {"company_id": COMPANY_1}

    monkeypatch.setattr(leads, "get_current_user_flexible", fake_current_user)

    async def fake_load_lead_details(_db, lead_id, company_id):
        return {"id": lead_id, "company_id": company_id, "nurture_messages": []}

    monkeypatch.setattr(leads, "_load_lead_details", fake_load_lead_details)

    result = await leads.update_lead_nurture_message(
        "lead-1",
        "message-1",
        FakeRequest(db, {"message": "Updated message", "phase": "awareness"}),
    )

    assert result["status"] == "updated"
    assert result["message"]["company_id"] == COMPANY_1
    assert db.executed


@pytest.mark.asyncio
async def test_update_nurture_message_rejects_wrong_company(monkeypatch):
    db = FakeNurtureDb(company_id=COMPANY_2)

    async def fake_current_user(_request):
        return {"company_id": COMPANY_1}

    monkeypatch.setattr(leads, "get_current_user_flexible", fake_current_user)

    with pytest.raises(HTTPException) as exc:
        await leads.update_lead_nurture_message(
            "lead-1",
            "message-1",
            FakeRequest(db, {"message": "Updated message"}),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_nurture_message_removes_only_company_scoped_message(monkeypatch):
    db = FakeNurtureDb(company_id=COMPANY_1)

    async def fake_current_user(_request):
        return {"company_id": COMPANY_1}

    monkeypatch.setattr(leads, "get_current_user_flexible", fake_current_user)

    async def fake_load_lead_details(_db, lead_id, company_id):
        return {"id": lead_id, "company_id": company_id, "nurture_messages": []}

    monkeypatch.setattr(leads, "_load_lead_details", fake_load_lead_details)

    result = await leads.delete_lead_nurture_message("lead-1", "message-1", FakeRequest(db))

    assert result["status"] == "deleted"
    assert result["message_id"] == "message-1"
    assert "company_id=$3" in db.executed[0][0]
