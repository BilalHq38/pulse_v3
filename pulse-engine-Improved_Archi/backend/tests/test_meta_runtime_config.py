import asyncio

from services.meta_service import (
    decrypt_meta_secret,
    get_meta_config,
    normalize_meta_config,
    sync_channel_settings_meta_config,
)


def test_normalize_meta_config_preserves_plaintext_token_on_second_pass():
    first = {
        "id": "meta-1",
        "company_id": "company-1",
        "channel": "whatsapp",
        "api_version": "21.0",
        "access_token": "EAATOKEN",
        "phone_number_id": "phone-1",
    }

    normalized = normalize_meta_config(first, include_secrets=True)
    second = normalize_meta_config(normalized, include_secrets=True)

    assert second["access_token"] == "EAATOKEN"
    assert second["access_token_configured"] is True
    assert second["api_version"] == "v21.0"


class _RuntimeConfigDb:
    def __init__(self, *, tenant_row=None, channel_row=None):
        self.tenant_row = tenant_row
        self.channel_row = channel_row

    async def fetchrow(self, query, *args):
        if "FROM tenant_meta_config" in query:
            return self.tenant_row
        if "FROM channel_settings" in query:
            return self.channel_row
        return None


def test_get_meta_config_uses_channel_settings_when_tenant_config_missing():
    asyncio.run(_run_get_meta_config_channel_settings_fallback())


async def _run_get_meta_config_channel_settings_fallback():
    db = _RuntimeConfigDb(
        channel_row={
            "id": "settings-1",
            "company_id": "company-1",
            "channel": "whatsapp",
            "enabled": True,
            "access_token": "EAATOKEN",
            "phone_number_id": "phone-1",
            "page_id": "waba-1",
            "verify_token": "verify-1",
        }
    )

    config = await get_meta_config(db, "company-1", channel="whatsapp", include_secrets=True)

    assert config["runtime_config_source"] == "channel_settings"
    assert config["access_token"] == "EAATOKEN"
    assert config["phone_number_id"] == "phone-1"
    assert config["business_account_id"] == "waba-1"


def test_get_meta_config_merges_missing_tenant_fields_from_channel_settings():
    asyncio.run(_run_get_meta_config_merges_channel_settings())


async def _run_get_meta_config_merges_channel_settings():
    db = _RuntimeConfigDb(
        tenant_row={
            "id": "meta-1",
            "company_id": "company-1",
            "channel": "whatsapp",
            "config_name": "default",
            "api_version": "v21.0",
            "access_token_enc": "",
            "phone_number_id": "",
            "business_account_id": "",
            "is_active": True,
        },
        channel_row={
            "id": "settings-1",
            "company_id": "company-1",
            "channel": "whatsapp",
            "enabled": True,
            "access_token": "EAATOKEN",
            "phone_number_id": "phone-1",
            "page_id": "waba-1",
        },
    )

    config = await get_meta_config(db, "company-1", channel="whatsapp", include_secrets=True)

    assert config["id"] == "meta-1"
    assert config["access_token"] == "EAATOKEN"
    assert config["phone_number_id"] == "phone-1"
    assert config["runtime_config_source"] == "tenant_meta_config+channel_settings"


class _SyncConfigDb:
    def __init__(self):
        self.executed = []

    async def fetchrow(self, *_args):
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return None


def test_channel_settings_sync_writes_encrypted_tenant_meta_config(monkeypatch):
    asyncio.run(_run_channel_settings_sync_writes_encrypted_config(monkeypatch))


async def _run_channel_settings_sync_writes_encrypted_config(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "unit-test-secret")
    db = _SyncConfigDb()

    await sync_channel_settings_meta_config(
        db,
        "company-1",
        {
            "id": "settings-1",
            "channel": "whatsapp",
            "enabled": True,
            "access_token": "EAATOKEN",
            "phone_number_id": "phone-1",
            "page_id": "waba-1",
            "verify_token": "verify-1",
        },
    )

    insert = next(item for item in db.executed if item[0].startswith("INSERT INTO tenant_meta_config"))
    args = insert[1]
    assert args[2] == "whatsapp"
    assert args[3] == "settings"
    assert decrypt_meta_secret(args[5]) == "EAATOKEN"
    assert args[6] == "OKEN"
    assert args[9] == "phone-1"
    assert args[10] == "waba-1"
    assert args[12] is True
    assert args[13] is True
