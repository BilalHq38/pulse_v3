"""routers/ai.py — AI, MCP, Social endpoints using PostgreSQL."""

import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from services.ai_service.facade import (
    analyze_sentiment,
    build_sentiment_gate,
    classify_intent,
    get_provider_runtime_info,
    validate_live_engine,
)
from services.ai_service.model_catalog import GEMINI_PROVIDER_KEYS, validate_model_selection
from services.ai_service.response_generator import clear_engine_cache
from services.conversation_engine import TurnRequest, run_turn as engine_run_turn
from core.utils import make_id, now_ts, normalize_reference_key
from services.db_helpers import (
    enrich_llm_engine,
    ensure_company_settings_row,
    ensure_default_llm_engine,
    get_current_user_flexible,
    r,
    require_roles,
    resolve_active_llm_engine,
    rs,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _db(req):
    return req.app.state.db


async def _enable_company_ai_if_agent_active(db, company_id: str, should_enable: bool) -> None:
    if not should_enable or not str(company_id or "").strip():
        return
    settings = await ensure_company_settings_row(db, company_id)
    await db.execute(
        "UPDATE company_settings SET ai_enabled=TRUE,updated_at=NOW() WHERE id=$1",
        settings["id"],
    )


LLM_ENGINE_FIELDS = {
    "model_name",
    "provider",
    "api_endpoint",
    "temperature",
    "max_tokens",
    "version",
    "is_active",
}
AI_AGENT_FIELDS = {
    "agent_type",
    "llm_id",
    "mcp_server_id",
    "api_key_ref",
    "provider",
    "version",
    "is_active",
}
MCP_SERVER_FIELDS = {"endpoint", "status", "region"}
SOCIAL_ACCOUNT_FIELDS = {
    "platform",
    "account_handle",
    "access_token_ref",
    "page_id",
    "app_id",
    "phone_number_id",
    "is_active",
}
WEBHOOK_HANDLER_FIELDS = {
    "client_id",
    "platform",
    "webhook_url",
    "verification_token_ref",
    "is_active",
}
SOCIAL_POST_FIELDS = {
    "account_id",
    "platform",
    "post_type",
    "content",
    "post_url",
    "engagement_count",
    "comments_count",
    "sentiment",
    "posted_at",
}
LLM_PROVIDER_KEYS = {*GEMINI_PROVIDER_KEYS, "openai", "anthropic"}
LLM_PROVIDER_ERROR = "provider must be openai, anthropic, gemini, gemini_api, or vertex_ai"


def _filter_update_fields(body: dict, allowed: set[str]) -> dict:
    return {k: v for k, v in body.items() if k in allowed}


def _validate_llm_model_or_400(provider: str, model_name: str) -> None:
    try:
        validate_model_selection(provider, model_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _invalidate_llm_engine_cache(company_id: str, reason: str) -> None:
    clear_engine_cache(company_id, reason=reason)


async def _load_agent_runtime_fields(db, agent: dict, company_id: str) -> dict:
    data = dict(agent or {})
    llm_id = str(data.get("llm_id") or "").strip()
    if llm_id:
        engine = r(
            await db.fetchrow(
                "SELECT provider,model_name,temperature,max_tokens FROM llm_engines "
                "WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
                llm_id,
                company_id,
            )
        )
        if engine:
            data["provider"] = data.get("provider") or engine.get("provider", "")
            data["model_name"] = engine.get("model_name", "")
            data["llm_temperature"] = engine.get("temperature")
            data["llm_max_tokens"] = engine.get("max_tokens")
    return data


@router.post("/ai/sentiment")
async def ai_sentiment(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    text = body.get("text", "")
    sentiment = await analyze_sentiment(text, db=db, company_id=cu.get("company_id", ""))
    return build_sentiment_gate(text, sentiment)


@router.post("/ai/classify")
async def ai_classify(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    return await classify_intent(body.get("text", ""), db=db, company_id=cu.get("company_id", ""))


@router.get("/ai/llm-engines")
async def list_llm_engines(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    selected = await resolve_active_llm_engine(db, cid)
    selected_id = selected.get("id", "")
    return [
        enrich_llm_engine(engine, selected_id=selected_id)
        for engine in rs(
            await db.fetch(
                "SELECT * FROM llm_engines WHERE is_active=TRUE AND (company_id='' OR company_id=$1) "
                "ORDER BY CASE WHEN company_id='' THEN 0 ELSE 1 END, provider, model_name",
                cid,
            )
        )
    ]


@router.post("/ai/llm-engines")
async def create_llm_engine(request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    cid = (current_user.get("company_id", "") or "").strip()
    body = await request.json()
    model_name = str(body.get("model_name", "") or "").strip()
    provider = normalize_reference_key(body.get("provider", ""))
    if not model_name:
        raise HTTPException(400, "model_name is required")
    if provider not in LLM_PROVIDER_KEYS:
        raise HTTPException(400, LLM_PROVIDER_ERROR)
    _validate_llm_model_or_400(provider, model_name)
    eid = make_id()
    await db.execute(
        "INSERT INTO llm_engines(id,company_id,model_name,provider,api_endpoint,temperature,max_tokens,is_active,version,last_updated,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW(),NOW())",  # noqa: E501
        eid,
        cid,
        model_name,
        provider,
        (body.get("api_endpoint", "") or "").strip(),
        float(body.get("temperature", 0.7)),
        int(body.get("max_tokens", 2048)),
        bool(body.get("is_active", True)),
        (body.get("version", "current") or "current").strip(),
    )
    _invalidate_llm_engine_cache(cid, "settings_update")
    return r(await db.fetchrow("SELECT * FROM llm_engines WHERE id=$1", eid))


@router.put("/ai/llm-engines/{llm_id}")
async def update_llm_engine(llm_id: str, request: Request):
    db = _db(request)
    await require_roles(request, ["admin", "super_admin"])
    body = _filter_update_fields(await request.json(), LLM_ENGINE_FIELDS)
    body.pop("_id", None)
    if "provider" in body:
        body["provider"] = normalize_reference_key(body["provider"])
        if body["provider"] not in LLM_PROVIDER_KEYS:
            raise HTTPException(400, LLM_PROVIDER_ERROR)
    current = r(await db.fetchrow("SELECT provider,model_name,company_id FROM llm_engines WHERE id=$1 LIMIT 1", llm_id))
    if not current:
        raise HTTPException(404, "LLM engine not found")
    final_provider = normalize_reference_key(body.get("provider", current.get("provider", "")))
    final_model = str(body.get("model_name", current.get("model_name", "")) or "").strip()
    _validate_llm_model_or_400(final_provider, final_model)
    if not body:
        raise HTTPException(400, "No valid fields provided")
    body["updated_at"] = now_ts()
    body["last_updated"] = now_ts()
    set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
    await db.execute(f"UPDATE llm_engines SET {set_parts} WHERE id=$1", llm_id, *body.values())
    _invalidate_llm_engine_cache(str(current.get("company_id") or ""), "settings_update")
    return r(await db.fetchrow("SELECT * FROM llm_engines WHERE id=$1", llm_id))


@router.post("/ai/llm-engines/{llm_id}/select")
async def select_llm_engine(llm_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    engine = r(
        await db.fetchrow(
            "SELECT * FROM llm_engines WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
            llm_id,
            cid,
        )
    )
    if not engine:
        raise HTTPException(404, "LLM engine not found")
    ready, reason = get_provider_runtime_info(engine.get("provider", ""))
    if not ready:
        raise HTTPException(400, reason)
    try:
        validate_live_engine(engine, require_vision=False)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    settings = await ensure_company_settings_row(db, cid)
    await db.execute(
        "UPDATE company_settings SET active_llm_engine_id=$1,updated_at=NOW() WHERE id=$2",
        llm_id,
        settings["id"],
    )
    _invalidate_llm_engine_cache(cid, "settings_update")
    return {"ok": True, "engine": enrich_llm_engine(engine, selected_id=llm_id)}


@router.delete("/ai/llm-engines/{llm_id}")
async def delete_llm_engine(llm_id: str, request: Request):
    db = _db(request)
    await require_roles(request, ["admin", "super_admin"])
    res = await db.execute("DELETE FROM llm_engines WHERE id=$1", llm_id)
    if res == "DELETE 0":
        raise HTTPException(404, "LLM engine not found")
    _invalidate_llm_engine_cache("", "settings_update")
    return {"ok": True}


@router.get("/ai/agents")
async def list_ai_agents(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    agents = rs(
        await db.fetch(
            "SELECT * FROM ai_agents WHERE company_id=$1 ORDER BY registered_at DESC",
            cid,
        )
    )
    for agent in agents:
        agent.update(await _load_agent_runtime_fields(db, agent, cid))
        agent["configuration"] = dict(
            r
            for r in await db.fetch(
                "SELECT config_key,config_val FROM ai_agent_config WHERE agent_id=$1",
                agent["id"],
            )
        )
    return agents


@router.post("/ai/agents")
async def create_ai_agent(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    body = await request.json()
    if not any(key in body for key in {"agent_type", "llm_id", "mcp_server_id", "api_key_ref", "provider", "version", "is_active", "configuration"}):
        raise HTTPException(400, "Provide at least one AI agent field")
    cid = cu.get("company_id", "")
    llm_id = body.get("llm_id") or (await ensure_default_llm_engine(db)).get("id", "")
    if llm_id and not await db.fetchrow(
        "SELECT id FROM llm_engines WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
        llm_id,
        cid,
    ):
        raise HTTPException(404, "LLM engine not found")
    agent_id = make_id()
    await db.execute(
        "INSERT INTO ai_agents(id,company_id,llm_id,mcp_server_id,agent_type,api_key_ref,provider,version,is_active,registered_at,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW(),NOW())",  # noqa: E501
        agent_id,
        cid,
        llm_id,
        body.get("mcp_server_id", ""),
        body.get("agent_type", "support"),
        body.get("api_key_ref", ""),
        body.get("provider", ""),
        body.get("version", "current"),
        bool(body.get("is_active", True)),
    )
    await _enable_company_ai_if_agent_active(db, cid, bool(body.get("is_active", True)))
    cfg = body.get("configuration", {})
    if cfg:
        await db.executemany(
            "INSERT INTO ai_agent_config(agent_id,config_key,config_val) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            [(agent_id, k, str(v)) for k, v in cfg.items()],
        )
    agent = r(await db.fetchrow("SELECT * FROM ai_agents WHERE id=$1", agent_id))
    return await _load_agent_runtime_fields(db, agent, cid)


@router.put("/ai/agents/{agent_id}")
async def update_ai_agent(agent_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    body = await request.json()
    cfg = body.pop("configuration", None)
    body = _filter_update_fields(body, AI_AGENT_FIELDS)
    body.pop("_id", None)
    if not body and cfg is None:
        raise HTTPException(400, "No valid fields provided")
    if body.get("llm_id") and not await db.fetchrow(
        "SELECT id FROM llm_engines WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
        body["llm_id"],
        cid,
    ):
        raise HTTPException(404, "LLM engine not found")
    if body:
        body["updated_at"] = now_ts()
        set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
        await db.execute(
            f"UPDATE ai_agents SET {set_parts} WHERE id=$1 AND company_id=${len(body) + 2}",
            agent_id,
            *body.values(),
            cid,
        )
        await _enable_company_ai_if_agent_active(db, cid, bool(body.get("is_active", False)))
    if cfg is not None:
        await db.execute("DELETE FROM ai_agent_config WHERE agent_id=$1", agent_id)
        if cfg:
            await db.executemany(
                "INSERT INTO ai_agent_config(agent_id,config_key,config_val) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                [(agent_id, k, str(v)) for k, v in cfg.items()],
            )
    agent = r(await db.fetchrow("SELECT * FROM ai_agents WHERE id=$1 AND company_id=$2", agent_id, cid))
    if not agent:
        raise HTTPException(404, "AI agent not found")
    agent["configuration"] = {
        row["config_key"]: row["config_val"]
        for row in await db.fetch(
            "SELECT config_key,config_val FROM ai_agent_config WHERE agent_id=$1",
            agent_id,
        )
    }
    return await _load_agent_runtime_fields(db, agent, cid)


@router.delete("/ai/agents/{agent_id}")
async def delete_ai_agent(agent_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    res = await db.execute("DELETE FROM ai_agents WHERE id=$1 AND company_id=$2", agent_id, cid)
    if res == "DELETE 0":
        raise HTTPException(404, "AI agent not found")
    return {"ok": True}


@router.get("/ai/sessions")
async def list_ai_sessions(
    request: Request, convo_id: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if convo_id:
        return rs(
            await db.fetch(
                "SELECT * FROM ai_sessions WHERE company_id=$1 AND convo_id=$2 ORDER BY created_at DESC LIMIT $3",
                cid,
                convo_id,
                limit,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM ai_sessions WHERE company_id=$1 ORDER BY created_at DESC LIMIT $2",
            cid,
            limit,
        )
    )


@router.get("/ai/training-data")
async def list_training_data(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    return rs(
        await db.fetch(
            "SELECT * FROM training_data WHERE company_id=$1 ORDER BY created_at DESC LIMIT 200",
            cid,
        )
    )


@router.post("/ai/training-data")
async def create_training_data(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    body = await request.json()
    tid = make_id()
    await db.execute(
        "INSERT INTO training_data(id,company_id,input_text,output_text,data_category,created_at,updated_at) VALUES($1,$2,$3,$4,$5,NOW(),NOW())",  # noqa: E501
        tid,
        cu.get("company_id", ""),
        body.get("input_text", ""),
        body.get("output_text", ""),
        body.get("data_category", "general"),
    )
    return r(await db.fetchrow("SELECT * FROM training_data WHERE id=$1", tid))


@router.get("/ai/context-memory")
async def list_context_memory(request: Request, entity_id: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if entity_id:
        return rs(
            await db.fetch(
                "SELECT * FROM context_memories WHERE company_id=$1 AND entity_id=$2 ORDER BY created_at DESC LIMIT 200",  # noqa: E501
                cid,
                entity_id,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM context_memories WHERE company_id=$1 ORDER BY created_at DESC LIMIT 200",
            cid,
        )
    )


@router.post("/ai/context-memory")
async def create_context_memory(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    mid = make_id()
    await db.execute(
        "INSERT INTO context_memories(id,company_id,convo_id,entity_id,entity_type,memory_content,memory_type,relevance_score,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW(),NOW())",  # noqa: E501
        mid,
        cu.get("company_id", ""),
        body.get("convo_id", ""),
        body.get("entity_id", ""),
        body.get("entity_type", "customer"),
        body.get("memory_content", ""),
        body.get("memory_type", "summary"),
        float(body.get("relevance_score", 0.5)),
    )
    return r(await db.fetchrow("SELECT * FROM context_memories WHERE id=$1", mid))


@router.get("/ai/sentiment-records")
async def list_sentiment_records(request: Request, entity_id: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if entity_id:
        return rs(
            await db.fetch(
                "SELECT * FROM sentiment_analyses WHERE company_id=$1 AND entity_id=$2 ORDER BY analyzed_at DESC LIMIT 200",  # noqa: E501
                cid,
                entity_id,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM sentiment_analyses WHERE company_id=$1 ORDER BY analyzed_at DESC LIMIT 200",
            cid,
        )
    )


# MCP
@router.get("/mcp/servers")
async def list_mcp_servers(request: Request):
    db = _db(request)
    await require_roles(request, ["admin", "super_admin"])
    servers = rs(await db.fetch("SELECT * FROM mcp_servers ORDER BY last_heartbeat DESC LIMIT 50"))
    for s in servers:
        caps = rs(
            await db.fetch(
                "SELECT cap_key,cap_value FROM mcp_server_capabilities WHERE server_id=$1",
                s["id"],
            )
        )
        s["capabilities"] = {c["cap_key"]: c["cap_value"] for c in caps}
    return servers


@router.post("/mcp/servers")
async def create_mcp_server(request: Request):
    db = _db(request)
    await require_roles(request, ["admin", "super_admin"])
    body = await request.json()
    endpoint = str(body.get("endpoint", "") or "").strip()
    if not endpoint:
        raise HTTPException(400, "endpoint is required")
    sid = make_id()
    await db.execute(
        "INSERT INTO mcp_servers(id,endpoint,status,region,last_heartbeat,created_at,updated_at) VALUES($1,$2,$3,$4,NOW(),NOW(),NOW())",  # noqa: E501
        sid,
        endpoint,
        body.get("status", "active"),
        body.get("region", ""),
    )
    if body.get("capabilities"):
        await db.executemany(
            "INSERT INTO mcp_server_capabilities(server_id,cap_key,cap_value) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            [(sid, k, str(v)) for k, v in body["capabilities"].items()],
        )
    return r(await db.fetchrow("SELECT * FROM mcp_servers WHERE id=$1", sid))


@router.put("/mcp/servers/{server_id}")
async def update_mcp_server(server_id: str, request: Request):
    db = _db(request)
    await require_roles(request, ["admin", "super_admin"])
    body = await request.json()
    caps = body.pop("capabilities", None)
    body = _filter_update_fields(body, MCP_SERVER_FIELDS)
    body.pop("_id", None)
    if not body and caps is None:
        raise HTTPException(400, "No valid fields provided")
    if body:
        body["updated_at"] = now_ts()
        set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
        await db.execute(f"UPDATE mcp_servers SET {set_parts} WHERE id=$1", server_id, *body.values())
    if caps is not None:
        await db.execute("DELETE FROM mcp_server_capabilities WHERE server_id=$1", server_id)
        if caps:
            await db.executemany(
                "INSERT INTO mcp_server_capabilities(server_id,cap_key,cap_value) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",  # noqa: E501
                [(server_id, k, str(v)) for k, v in caps.items()],
            )
    server = r(await db.fetchrow("SELECT * FROM mcp_servers WHERE id=$1", server_id))
    if not server:
        raise HTTPException(404, "MCP server not found")
    server["capabilities"] = {
        row["cap_key"]: row["cap_value"]
        for row in await db.fetch(
            "SELECT cap_key,cap_value FROM mcp_server_capabilities WHERE server_id=$1",
            server_id,
        )
    }
    return server


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str, request: Request):
    db = _db(request)
    await require_roles(request, ["admin", "super_admin"])
    res = await db.execute("DELETE FROM mcp_servers WHERE id=$1", server_id)
    if res == "DELETE 0":
        raise HTTPException(404, "MCP server not found")
    return {"ok": True}


@router.get("/mcp/clients")
async def list_mcp_clients(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    clients = rs(
        await db.fetch(
            "SELECT * FROM mcp_clients WHERE company_id=$1 ORDER BY last_connected DESC",
            cid,
        )
    )
    for c in clients:
        c["configuration"] = dict(
            r
            for r in await db.fetch(
                "SELECT config_key,config_val FROM mcp_client_config WHERE client_id=$1",
                c["id"],
            )
        )
    return clients


@router.post("/mcp/clients")
async def create_mcp_client(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    body = await request.json()
    cid_new = make_id()
    await db.execute(
        "INSERT INTO mcp_clients(id,server_id,company_id,user_id,client_type,client_name,platform,version,last_connected,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW(),NOW(),NOW())",  # noqa: E501
        cid_new,
        body.get("server_id", ""),
        cu.get("company_id", ""),
        body.get("user_id", cu.get("sub", "")),
        body.get("client_type", "internal"),
        body.get("client_name", ""),
        body.get("platform", ""),
        body.get("version", "v1"),
    )
    if body.get("configuration"):
        await db.executemany(
            "INSERT INTO mcp_client_config(client_id,config_key,config_val) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            [(cid_new, k, str(v)) for k, v in body["configuration"].items()],
        )
    client = r(await db.fetchrow("SELECT * FROM mcp_clients WHERE id=$1", cid_new))
    if client:
        client["configuration"] = {
            row["config_key"]: row["config_val"]
            for row in await db.fetch(
                "SELECT config_key,config_val FROM mcp_client_config WHERE client_id=$1",
                cid_new,
            )
        }
    return client


@router.put("/mcp/clients/{client_id}")
async def update_mcp_client(client_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    body = await request.json()
    cfg = body.pop("configuration", None)
    allowed_fields = {"server_id", "client_type", "client_name", "platform", "version"}
    body = {k: v for k, v in body.items() if k in allowed_fields}
    body.pop("_id", None)
    if not body and cfg is None:
        raise HTTPException(400, "No valid fields provided")
    if body:
        body["updated_at"] = now_ts()
        set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
        await db.execute(
            f"UPDATE mcp_clients SET {set_parts} WHERE id=$1 AND company_id=${len(body) + 2}",
            client_id,
            *body.values(),
            cid,
        )
    if cfg is not None:
        await db.execute("DELETE FROM mcp_client_config WHERE client_id=$1", client_id)
        if cfg:
            await db.executemany(
                "INSERT INTO mcp_client_config(client_id,config_key,config_val) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",  # noqa: E501
                [(client_id, k, str(v)) for k, v in cfg.items()],
            )
    client = r(await db.fetchrow("SELECT * FROM mcp_clients WHERE id=$1 AND company_id=$2", client_id, cid))
    if not client:
        raise HTTPException(404, "MCP client not found")
    client["configuration"] = {
        row["config_key"]: row["config_val"]
        for row in await db.fetch(
            "SELECT config_key,config_val FROM mcp_client_config WHERE client_id=$1",
            client_id,
        )
    }
    return client


@router.delete("/mcp/clients/{client_id}")
async def delete_mcp_client(client_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    res = await db.execute("DELETE FROM mcp_clients WHERE id=$1 AND company_id=$2", client_id, cid)
    if res == "DELETE 0":
        raise HTTPException(404, "MCP client not found")
    return {"ok": True}


@router.post("/mcp/servers/{server_id}/heartbeat")
async def mcp_server_heartbeat(server_id: str, request: Request):
    db = _db(request)
    await require_roles(request, ["admin", "super_admin"])
    body = await request.json()
    await db.execute(
        "UPDATE mcp_servers SET last_heartbeat=NOW(),status=$1,updated_at=NOW() WHERE id=$2",
        body.get("status", "active"),
        server_id,
    )
    if body.get("capabilities"):
        await db.execute("DELETE FROM mcp_server_capabilities WHERE server_id=$1", server_id)
        await db.executemany(
            "INSERT INTO mcp_server_capabilities(server_id,cap_key,cap_value) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            [(server_id, k, str(v)) for k, v in body["capabilities"].items()],
        )
    server = r(await db.fetchrow("SELECT * FROM mcp_servers WHERE id=$1", server_id))
    if not server:
        raise HTTPException(404, "MCP server not found")
    server["capabilities"] = {
        row["cap_key"]: row["cap_value"]
        for row in await db.fetch(
            "SELECT cap_key,cap_value FROM mcp_server_capabilities WHERE server_id=$1",
            server_id,
        )
    }
    return server


@router.get("/mcp/webhook-events")
async def list_webhook_events(
    request: Request, handler_id: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if handler_id:
        return rs(
            await db.fetch(
                "SELECT * FROM webhook_events WHERE company_id=$1 AND handler_id=$2 ORDER BY received_at DESC LIMIT $3",
                cid,
                handler_id,
                limit,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM webhook_events WHERE company_id=$1 ORDER BY received_at DESC LIMIT $2",
            cid,
            limit,
        )
    )


# Social
@router.get("/social/accounts")
async def list_social_accounts(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    return rs(await db.fetch("SELECT * FROM social_accounts WHERE company_id=$1 ORDER BY platform", cid))


@router.post("/social/accounts")
async def create_social_account(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    body = await request.json()
    platform = (body.get("platform", "") or "").strip().lower()
    if platform not in {"whatsapp", "instagram", "facebook"}:
        raise HTTPException(400, "platform must be whatsapp, instagram, or facebook")
    if not any(
        str(body.get(field, "") or "").strip()
        for field in ("account_handle", "page_id", "phone_number_id")
    ):
        raise HTTPException(400, "Provide at least one account identifier")
    said = make_id()
    await db.execute(
        "INSERT INTO social_accounts(id,company_id,platform,account_handle,access_token_ref,page_id,app_id,phone_number_id,is_active,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",  # noqa: E501
        said,
        cu.get("company_id", ""),
        platform,
        (body.get("account_handle", "") or "").strip(),
        body.get("access_token_ref", ""),
        body.get("page_id", ""),
        body.get("app_id", ""),
        body.get("phone_number_id", ""),
        bool(body.get("is_active", True)),
    )
    return r(await db.fetchrow("SELECT * FROM social_accounts WHERE id=$1", said))


@router.put("/social/accounts/{account_id}")
async def update_social_account(account_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    body = _filter_update_fields(await request.json(), SOCIAL_ACCOUNT_FIELDS)
    body.pop("_id", None)
    if "platform" in body:
        body["platform"] = (body.get("platform", "") or "").strip().lower()
    if not body:
        raise HTTPException(400, "No valid fields provided")
    body["updated_at"] = now_ts()
    set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
    await db.execute(
        f"UPDATE social_accounts SET {set_parts} WHERE id=$1 AND company_id=${len(body) + 2}",
        account_id,
        *body.values(),
        cid,
    )
    account = r(
        await db.fetchrow(
            "SELECT * FROM social_accounts WHERE id=$1 AND company_id=$2",
            account_id,
            cid,
        )
    )
    if not account:
        raise HTTPException(404, "Social account not found")
    return account


@router.delete("/social/accounts/{account_id}")
async def delete_social_account(account_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    res = await db.execute("DELETE FROM social_accounts WHERE id=$1 AND company_id=$2", account_id, cid)
    if res == "DELETE 0":
        raise HTTPException(404, "Social account not found")
    return {"ok": True}


@router.get("/social/posts")
async def list_social_posts(
    request: Request, account_id: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if account_id:
        return rs(
            await db.fetch(
                "SELECT * FROM social_posts WHERE company_id=$1 AND account_id=$2 ORDER BY posted_at DESC LIMIT $3",
                cid,
                account_id,
                limit,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM social_posts WHERE company_id=$1 ORDER BY posted_at DESC LIMIT $2",
            cid,
            limit,
        )
    )


@router.post("/social/posts")
async def create_social_post(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    body = _filter_update_fields(await request.json(), SOCIAL_POST_FIELDS)
    pid = make_id()
    await db.execute(
        "INSERT INTO social_posts(id,company_id,account_id,platform,post_type,content,post_url,engagement_count,comments_count,sentiment,posted_at,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())",  # noqa: E501
        pid,
        cid,
        body.get("account_id", ""),
        body.get("platform", ""),
        body.get("post_type", "text"),
        body.get("content", ""),
        body.get("post_url", ""),
        int(body.get("engagement_count", 0)),
        int(body.get("comments_count", 0)),
        float(body.get("sentiment", 0) or 0),
        body.get("posted_at") or now_ts(),
    )
    return r(await db.fetchrow("SELECT * FROM social_posts WHERE id=$1", pid))


@router.get("/webhooks/handlers")
async def list_webhook_handlers(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    return rs(
        await db.fetch(
            "SELECT * FROM webhook_handlers WHERE company_id=$1 ORDER BY created_at DESC LIMIT 100",
            cid,
        )
    )


@router.post("/webhooks/handlers")
async def create_webhook_handler(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    body = _filter_update_fields(await request.json(), WEBHOOK_HANDLER_FIELDS)
    hid = make_id()
    await db.execute(
        "INSERT INTO webhook_handlers(id,company_id,client_id,platform,webhook_url,verification_token_ref,is_active,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,NOW(),NOW())",  # noqa: E501
        hid,
        cid,
        body.get("client_id", ""),
        body.get("platform", ""),
        body.get("webhook_url", ""),
        body.get("verification_token_ref", ""),
        bool(body.get("is_active", True)),
    )
    return r(await db.fetchrow("SELECT * FROM webhook_handlers WHERE id=$1", hid))


@router.put("/webhooks/handlers/{handler_id}")
async def update_webhook_handler(handler_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    body = _filter_update_fields(await request.json(), WEBHOOK_HANDLER_FIELDS)
    body.pop("_id", None)
    if not body:
        raise HTTPException(400, "No valid fields provided")
    body["updated_at"] = now_ts()
    set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
    await db.execute(
        f"UPDATE webhook_handlers SET {set_parts} WHERE id=$1 AND company_id=${len(body) + 2}",
        handler_id,
        *body.values(),
        cid,
    )
    handler = r(
        await db.fetchrow(
            "SELECT * FROM webhook_handlers WHERE id=$1 AND company_id=$2",
            handler_id,
            cid,
        )
    )
    if not handler:
        raise HTTPException(404, "Webhook handler not found")
    return handler


@router.delete("/webhooks/handlers/{handler_id}")
async def delete_webhook_handler(handler_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    cid = cu.get("company_id", "")
    res = await db.execute("DELETE FROM webhook_handlers WHERE id=$1 AND company_id=$2", handler_id, cid)
    if res == "DELETE 0":
        raise HTTPException(404, "Webhook handler not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Wave 3 — conversation engine endpoints.
# ---------------------------------------------------------------------------


class AiChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)
    user_message: str = Field(..., min_length=1, max_length=4000)
    customer_id: str = Field(default="", max_length=120)
    mode: str = Field(default="reactive", pattern=r"^(reactive|proactive)$")


@router.post("/ai/chat")
async def ai_chat(payload: AiChatRequest, request: Request):
    """Primary entry point for the new conversation engine."""
    db = _db(request)
    cu = await get_current_user_flexible(request)
    company_id = (cu.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "company_id_required")
    turn_request = TurnRequest(
        session_id=payload.session_id,
        company_id=company_id,
        user_message=payload.user_message,
        customer_id=payload.customer_id,
        mode=payload.mode,  # type: ignore[arg-type]
    )
    result = await engine_run_turn(db, turn_request)
    return {
        "answer": result.answer,
        "session_id": result.session_id,
        "turn_id": result.turn_id,
        "sources_used": list(result.sources_used),
        "product_links": [
            {"product_id": pl.product_id, "url": pl.url, "name": pl.name, "image_url": pl.image_url}
            for pl in result.product_links
        ],
        "tokens_used": {
            "prompt": result.tokens_used.prompt,
            "completion": result.tokens_used.completion,
            "total": result.tokens_used.total,
        },
        "active_template": result.active_template,
        "confidence": result.confidence,
        "error": result.error or None,
    }


@router.get("/ai/sessions/{session_id}/turns")
async def list_ai_turns(session_id: str, request: Request, limit: int = Query(default=50, ge=1, le=200)):
    """Inbox / debug view of the conversation engine turn log for a session."""
    db = _db(request)
    cu = await get_current_user_flexible(request)
    company_id = (cu.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "company_id_required")
    rows = await db.fetch(
        "SELECT id, turn_index, user_message, ai_response, sources_used, product_links, "
        " confidence, active_template, token_usage, mode, created_at "
        "FROM ai_conversation_turns WHERE company_id = $1 AND session_id = $2 "
        "ORDER BY turn_index ASC LIMIT $3",
        company_id,
        session_id,
        limit,
    )
    out: list[dict] = []
    for row in rows or []:
        record = dict(row)
        # JSONB columns come back as strings or dicts depending on the driver
        # config; normalise to Python objects for the API response.
        for jsonb_field in ("sources_used", "product_links", "token_usage"):
            value = record.get(jsonb_field)
            if isinstance(value, str):
                try:
                    record[jsonb_field] = json.loads(value)
                except json.JSONDecodeError:
                    record[jsonb_field] = []
        out.append(record)
    return rs(out)


@router.get("/ai/templates")
async def list_response_templates(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    company_id = (cu.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "company_id_required")
    # Self-heal: companies that pre-date the conversation engine, or that were
    # created between startups, won't have the default templates yet. Seed them
    # idempotently before returning the list so the Response Style page never
    # renders empty on first hit.
    from services.conversation_engine_bootstrap import ensure_company_response_templates

    try:
        await ensure_company_response_templates(db, company_id)
    except Exception:
        pass  # Seeding failure must never block the list response
    return rs(
        await db.fetch(
            "SELECT id, name, style_prompt, is_default, created_at, updated_at "
            "FROM response_templates WHERE company_id = $1 ORDER BY is_default DESC, name ASC",
            company_id,
        )
    )


@router.post("/ai/templates/{template_id}/set-default")
async def set_default_response_template(template_id: str, request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin", "super_admin"])
    company_id = (cu.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(400, "company_id_required")
    target = await db.fetchrow(
        "SELECT id FROM response_templates WHERE id = $1 AND company_id = $2",
        template_id,
        company_id,
    )
    if not target:
        raise HTTPException(404, "template_not_found")
    # Flip the default in a single transaction so the uq_response_templates_one_default
    # partial index is never violated mid-update.
    async with db.transaction():
        await db.execute(
            "UPDATE response_templates SET is_default = FALSE, updated_at = NOW() "
            "WHERE company_id = $1 AND id <> $2 AND is_default = TRUE",
            company_id,
            template_id,
        )
        await db.execute(
            "UPDATE response_templates SET is_default = TRUE, updated_at = NOW() WHERE id = $1",
            template_id,
        )
    return {"ok": True, "id": template_id}
