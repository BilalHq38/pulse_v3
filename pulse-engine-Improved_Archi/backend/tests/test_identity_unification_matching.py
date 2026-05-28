import os
import socket
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from sqlalchemy import delete


TEST_DATABASE_URL = (
    os.environ.get("IDENTITY_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or ""
).strip()

if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def _database_reachable(database_url: str) -> bool:
    if not database_url:
        return False
    parsed = urlparse(database_url)
    host = parsed.hostname
    port = int(parsed.port or 5432)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


requires_database = pytest.mark.skipif(
    not _database_reachable(TEST_DATABASE_URL),
    reason="Identity unification matching endpoint test requires a reachable PostgreSQL DATABASE_URL",
)


from services.identity_service.app import server as identity_server  # noqa: E402
from services.identity_service.app.db.database import AsyncSessionLocal  # noqa: E402
from services.identity_service.app.db.models import IdentityMapping, ResolutionAuditLog, ReviewQueue, UnifiedCustomer  # noqa: E402
from services.identity_service.app.db.schemas import ResolveRequest  # noqa: E402
from services.identity_service.app.services import identity as identity_service  # noqa: E402


@pytest.mark.asyncio
async def test_score_candidate_uses_phone_email_avatar_name_description_and_company():
    tenant_id = f"signals-{uuid.uuid4()}"
    candidate_id = uuid.uuid4()
    candidate = UnifiedCustomer(
        customer_id=candidate_id,
        tenant_id=tenant_id,
        primary_name="Amina Khan",
        signal_profile={
            "profile_picture_url": "https://cdn.example.test/avatar/amina.jpg",
            "profile_picture_phash": "ff00",
            "description": "custom furniture and home decor buyer",
            "company_name": "Home Sweet Home",
        },
    )
    candidate.mappings = [
        IdentityMapping(
            tenant_id=tenant_id,
            customer_id=candidate_id,
            platform="whatsapp",
            platform_user_id="923001234567",
            phone="+92 300 1234567",
            email="amina.khan+sales@gmail.com",
            name="Amina Khan",
            confidence=0.8,
        )
    ]

    scored = await identity_service._score_candidate(
        None,
        tenant_id,
        candidate,
        ResolveRequest(
            platform="instagram",
            platform_user_id="ig-amina",
            consent_token="test",
            phone_number="03001234567",
            email_address="aminakhan@gmail.com",
            full_name="Amina Khan",
            profile_picture_url="https://cdn.example.test/avatar/amina.jpg",
            profile_picture_phash="ff00",
            description="custom furniture and home decor buyer",
            company_name="Home Sweet Home",
        ),
        phone_hash=None,
        email_hash=None,
        fingerprint_hash=None,
    )

    assert scored.score >= 700
    assert {"phone", "email", "name", "profile_picture", "description", "company"}.issubset(
        set(scored.independent_signals)
    )
    assert scored.breakdown["phone_normalized"] >= 450
    assert scored.breakdown["email_normalized"] >= 450


@pytest.mark.asyncio
@requires_database
async def test_auto_detect_endpoint_returns_company_scoped_candidates_with_match_fields(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(identity_server, "enforce_rate_limit", _noop)

    tenant_id = f"unify-{uuid.uuid4()}"
    other_tenant_id = f"unify-other-{uuid.uuid4()}"

    async with AsyncSessionLocal() as db:
        source = UnifiedCustomer(
            tenant_id=tenant_id,
            profile_confidence=0.7,
            primary_name="Amina Khan",
            primary_phone_hash="phone-shared",
            primary_email_hash="email-shared",
            signal_profile={
                "profile_picture_url": "https://cdn.example.test/avatar/amina.jpg",
                "profile_picture_phash": "ff00",
                "description": "custom furniture and home decor buyer",
                "company_name": "Home Sweet Home",
            },
        )
        candidate = UnifiedCustomer(
            tenant_id=tenant_id,
            profile_confidence=0.72,
            primary_name="Amina Khan",
            primary_phone_hash="phone-shared",
            primary_email_hash="email-shared",
            signal_profile={
                "profile_picture_url": "https://cdn.example.test/avatar/amina.jpg",
                "profile_picture_phash": "ff00",
                "description": "custom furniture and home decor buyer",
                "company_name": "Home Sweet Home",
            },
        )
        other = UnifiedCustomer(
            tenant_id=other_tenant_id,
            profile_confidence=0.95,
            primary_name="Amina Khan",
            primary_phone_hash="phone-shared",
            primary_email_hash="email-shared",
            signal_profile={"company_name": "Other Tenant"},
        )
        db.add_all([source, candidate, other])
        await db.flush()

        db.add_all(
            [
                IdentityMapping(
                    tenant_id=tenant_id,
                    customer_id=source.customer_id,
                    platform="whatsapp",
                    platform_user_id=f"wa-{uuid.uuid4()}",
                    phone="+923001234567",
                    email="amina@example.test",
                    name="Amina Khan",
                    confidence=0.8,
                    is_primary_platform=True,
                ),
                IdentityMapping(
                    tenant_id=tenant_id,
                    customer_id=candidate.customer_id,
                    platform="instagram",
                    platform_user_id=f"ig-{uuid.uuid4()}",
                    phone="03001234567",
                    email="amina@example.test",
                    name="Amina Khan",
                    confidence=0.8,
                    is_primary_platform=True,
                ),
                IdentityMapping(
                    tenant_id=other_tenant_id,
                    customer_id=other.customer_id,
                    platform="facebook",
                    platform_user_id=f"fb-{uuid.uuid4()}",
                    phone="+923001234567",
                    email="amina@example.test",
                    name="Amina Khan",
                    confidence=0.8,
                    is_primary_platform=True,
                ),
            ]
        )
        await db.commit()

        try:
            auto_detect = await identity_server.identity_auto_detect_compat(
                _admin=True,
                tenant=SimpleNamespace(tenant_id=tenant_id),
                db=db,
            )
            assert auto_detect["status"] == "ok"
            assert auto_detect["new_suggestions"] >= 1

            suggestions = await identity_server.identity_suggestions_compat(
                tenant=SimpleNamespace(tenant_id=tenant_id),
                db=db,
            )
            assert suggestions
            first = suggestions[0]
            scoped_ids = {first["candidate_a"]["customer_id"], first["candidate_b"]["customer_id"]}
            assert str(source.customer_id) in scoped_ids
            assert str(candidate.customer_id) in scoped_ids
            assert str(other.customer_id) not in scoped_ids
            assert first["match_score"] >= 0.6
            assert "phone" in first["matched_fields"]
            assert "email" in first["matched_fields"]
            assert first["candidate_a"]["avatar_url"] or first["candidate_b"]["avatar_url"]
            assert set(first["source_channels"]) >= {"whatsapp", "instagram"}
        finally:
            await db.execute(delete(ReviewQueue).where(ReviewQueue.tenant_id.in_([tenant_id, other_tenant_id])))
            await db.execute(delete(ResolutionAuditLog).where(ResolutionAuditLog.tenant_id.in_([tenant_id, other_tenant_id])))
            await db.execute(delete(IdentityMapping).where(IdentityMapping.tenant_id.in_([tenant_id, other_tenant_id])))
            await db.execute(delete(UnifiedCustomer).where(UnifiedCustomer.tenant_id.in_([tenant_id, other_tenant_id])))
            await db.commit()
