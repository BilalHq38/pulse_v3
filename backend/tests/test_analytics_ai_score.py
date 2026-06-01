from types import SimpleNamespace

import pytest

from routers import analytics


class _FakeAnalyticsDb:
    def __init__(self) -> None:
        self.fetchval_queries: list[str] = []
        self.fetch_queries: list[str] = []

    async def fetchval(self, query, *args):
        self.fetchval_queries.append(query)
        if "AVG(ai_confidence) FROM conversations" in query:
            raise AssertionError("ai-score must not read ai_confidence from conversations")
        if "COUNT(*) FROM conversations" in query and "status=ANY" in query and "ai_handled=TRUE" not in query:
            return 3
        if "COUNT(*) FROM conversations" in query and "ai_handled=TRUE" in query and "status=ANY" in query:
            return 2
        if "status='resolved'" in query:
            return 1
        if "status='escalated'" in query:
            return 0
        if "AVG(m.ai_confidence)" in query:
            return 0.85
        return 0

    async def fetch(self, query, *args):
        self.fetch_queries.append(query)
        if "m.ai_confidence AS confidence" in query:
            return [
                {"confidence": 0.9, "status": "resolved"},
                {"confidence": 0.5, "status": "open"},
            ]
        return []


@pytest.mark.asyncio
async def test_ai_score_reads_confidence_from_messages_not_conversations(monkeypatch):
    async def _current_user(_request):
        return {"company_id": "company-1"}

    monkeypatch.setattr(analytics, "get_current_user_flexible", _current_user)
    db = _FakeAnalyticsDb()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))

    result = await analytics.analytics_ai_score(request)

    assert result["avg_confidence_pct"] == 85.0
    assert result["resolution_rate"] == 50.0
    assert result["ai_score"] == 71.0
    assert result["nature_breakdown"]["Excellent"] == 1
    assert result["nature_breakdown"]["Moderate"] == 1
    assert all("AVG(ai_confidence) FROM conversations" not in q for q in db.fetchval_queries)
