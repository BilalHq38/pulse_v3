from __future__ import annotations

from data_pipeline.bootstrap import ensure_pipeline_tables
from models.reference_data import ensure_global_roles
from services.conversation_engine_bootstrap import ensure_default_response_templates
from services.db_helpers import (
    ensure_conversation_ai_pause_schema,
    ensure_auth_security_primitives,
    ensure_default_llm_engine,
    ensure_embedding_vector_optimizations,
    ensure_super_admin_user,
)
from services.demo_seed import ensure_demo_accounts
from services.public_signup_service import ensure_pending_signup_primitives


async def bootstrap_roles(db) -> None:
    await ensure_global_roles(db)


async def bootstrap_auth_security(db) -> None:
    await ensure_auth_security_primitives(db)


async def bootstrap_signup_primitives(db) -> None:
    await ensure_pending_signup_primitives(db)


async def bootstrap_super_admin(db) -> None:
    await ensure_super_admin_user(db)


async def bootstrap_demo_accounts(db) -> None:
    await ensure_demo_accounts(db)


async def bootstrap_ai_runtime(db) -> None:
    await ensure_default_llm_engine(db)
    await ensure_embedding_vector_optimizations(db)
    await ensure_default_response_templates(db)


async def bootstrap_customer_runtime(db) -> None:
    await ensure_conversation_ai_pause_schema(db)


async def bootstrap_data_pipeline(db) -> None:
    await ensure_pipeline_tables(db)
