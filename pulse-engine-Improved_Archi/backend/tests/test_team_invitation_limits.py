import pytest
from fastapi import HTTPException

from services.billing_helpers import (
    TEAM_MEMBER_LIMIT_REACHED_MESSAGE,
    USER_LIMIT_REACHED_CODE,
    assert_workspace_seat_available,
)


class _SeatLimitDb:
    async def execute(self, *_args):
        return None

    async def fetchrow(self, *_args):
        return {"plan_code": "free", "max_users": 1}

    async def fetchval(self, query, *_args):
        if "FROM users" in query:
            return 1
        if "FROM invitations" in query:
            return 0
        return 0


@pytest.mark.asyncio
async def test_workspace_seat_limit_returns_frontend_safe_message():
    with pytest.raises(HTTPException) as exc:
        await assert_workspace_seat_available(_SeatLimitDb(), "company-1")

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == USER_LIMIT_REACHED_CODE
    assert exc.value.detail["message"] == TEAM_MEMBER_LIMIT_REACHED_MESSAGE
