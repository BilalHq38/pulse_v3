import os
import socket
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from sqlalchemy import delete, select


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


if not _database_reachable(TEST_DATABASE_URL):
    pytestmark = pytest.mark.skip(reason="Identity split regression test requires a reachable PostgreSQL DATABASE_URL")


from services.identity_service.app import server as identity_server  # noqa: E402
from services.identity_service.app.db.database import AsyncSessionLocal  # noqa: E402
from services.identity_service.app.db.models import IdentityMapping, ProfileMergeHistory, UnifiedCustomer  # noqa: E402
from services.identity_service.app.server import InternalIdentitySplitRequest  # noqa: E402
from services.identity_service.app.services import identity as identity_service  # noqa: E402


@pytest.mark.asyncio
async def test_identity_split_compat_resolves_pulse_customer_id_and_moves_mapping(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(identity_server, "enforce_rate_limit", _noop)
    monkeypatch.setattr(identity_service, "delete_keys", _noop)
    monkeypatch.setattr(identity_service, "_emit_event_safe", _noop)

    tenant_id = f"split-compat-{uuid.uuid4()}"
    pulse_platform_user_id = f"pulse-{uuid.uuid4()}"
    whatsapp_platform_user_id = f"whatsapp-{uuid.uuid4()}"

    async with AsyncSessionLocal() as db:
        source_customer = UnifiedCustomer(
            tenant_id=tenant_id,
            profile_confidence=0.92,
            primary_name="Split Compat Source",
            signal_profile={"test_case": "identity_split_compat"},
        )
        db.add(source_customer)
        await db.flush()

        pulse_mapping = IdentityMapping(
            tenant_id=tenant_id,
            customer_id=source_customer.customer_id,
            platform="pulse_customer",
            platform_user_id=pulse_platform_user_id,
            name="Pulse CRM Member",
            confidence=1.0,
            is_primary_platform=True,
        )
        whatsapp_mapping = IdentityMapping(
            tenant_id=tenant_id,
            customer_id=source_customer.customer_id,
            platform="whatsapp",
            platform_user_id=whatsapp_platform_user_id,
            name="WhatsApp Member",
            confidence=0.88,
            is_primary_platform=False,
        )
        db.add_all([pulse_mapping, whatsapp_mapping])
        await db.commit()

        source_customer_id = str(source_customer.customer_id)
        pulse_mapping_id = str(pulse_mapping.mapping_id)
        whatsapp_mapping_id = str(whatsapp_mapping.mapping_id)

        try:
            result = await identity_server.identity_split_compat(
                InternalIdentitySplitRequest(
                    profile_id=source_customer_id,
                    customer_id=pulse_platform_user_id,
                    mapping_ids=[],
                    fingerprint_ids=[],
                    split_reason="regression_test",
                ),
                _admin=True,
                tenant=SimpleNamespace(tenant_id=tenant_id),
                db=db,
            )

            assert result["split"] is True
            assert result["moved_mapping_count"] == 1
            assert result["moved_mapping_ids"] == [pulse_mapping_id]
            assert result["source_customer_id"] == source_customer_id
            assert result["new_customer_id"] != source_customer_id

            mapping_rows = (
                (
                    await db.execute(
                        select(IdentityMapping)
                        .where(IdentityMapping.tenant_id == tenant_id)
                        .order_by(IdentityMapping.platform.asc())
                    )
                )
                .scalars()
                .all()
            )
            mapping_lookup = {str(row.mapping_id): row for row in mapping_rows}

            assert str(mapping_lookup[pulse_mapping_id].customer_id) == result["new_customer_id"]
            assert str(mapping_lookup[whatsapp_mapping_id].customer_id) == source_customer_id

            customer_ids = {
                str(row.customer_id)
                for row in (
                    (
                        await db.execute(
                            select(UnifiedCustomer).where(UnifiedCustomer.tenant_id == tenant_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            }
            assert source_customer_id in customer_ids
            assert result["new_customer_id"] in customer_ids

            history_rows = (
                (
                    await db.execute(
                        select(ProfileMergeHistory)
                        .where(ProfileMergeHistory.tenant_id == tenant_id)
                        .order_by(ProfileMergeHistory.merged_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(history_rows) == 1
            assert str(history_rows[0].source_customer_id) == source_customer_id
            assert str(history_rows[0].target_customer_id) == result["new_customer_id"]
            assert history_rows[0].merge_reason == "split:regression_test"
        finally:
            await db.execute(delete(IdentityMapping).where(IdentityMapping.tenant_id == tenant_id))
            await db.execute(delete(ProfileMergeHistory).where(ProfileMergeHistory.tenant_id == tenant_id))
            await db.execute(delete(UnifiedCustomer).where(UnifiedCustomer.tenant_id == tenant_id))
            await db.commit()
