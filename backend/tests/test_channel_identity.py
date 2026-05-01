import asyncio

import pytest

from channel_layer.adapters.whatsapp import WhatsAppAdapter
from channel_layer.channel_identity import (
    normalize_email,
    normalize_social_channel_id,
    normalize_whatsapp_phone,
    resolve_outbound_recipient,
)


def test_whatsapp_incoming_e164_sender_preserved():
    identity = normalize_whatsapp_phone("+92 300-1234567")

    assert identity.is_valid
    assert identity.canonical_value == "+923001234567"


def test_whatsapp_adapter_uses_provider_wa_id_when_from_missing():
    payload = {
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "182974364528890"},
                            "messages": [
                                {
                                    "id": "wamid.test",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                            "contacts": [
                                {
                                    "wa_id": "923001234567",
                                    "profile": {"name": "Sara"},
                                }
                            ],
                        }
                    }
                ],
            }
        ]
    }

    message = asyncio.run(WhatsAppAdapter().receive_message(payload, db=None, tenant_id="co"))

    assert message.external_user_id == "+923001234567"
    assert message.metadata["raw_wa_id"] == "923001234567"
    assert message.metadata["phone_number_id"] == "182974364528890"


def test_whatsapp_web_bridge_uses_real_sender_phone_not_lid_provider_id():
    payload = {
        "entry": [
            {
                "id": "whatsapp-web:scope",
                "changes": [
                    {
                        "value": {
                            "metadata": {
                                "source": "whatsapp_web_bridge",
                                "company_id": "co",
                                "bridge_user_id": "user-1",
                            },
                            "messages": [
                                {
                                    "id": "AC02BFA8012E57B30970D41BEB79EA1E",
                                    "from": "222436977021015",
                                    "type": "text",
                                    "text": {"body": "hi"},
                                    "web_bridge": {
                                        "raw_from": "222436977021015@lid",
                                        "msg_from": "222436977021015@lid",
                                        "msg_id_remote": "222436977021015@lid",
                                        "provider_sender_id": "222436977021015@lid",
                                        "sender_phone": "+923445563662",
                                        "sender_phone_digits": "923445563662",
                                        "selected_identity_source": "contact.number",
                                    },
                                }
                            ],
                            "contacts": [
                                {
                                    "wa_id": "923445563662",
                                    "profile": {"name": "Bilal"},
                                }
                            ],
                        }
                    }
                ],
            }
        ]
    }

    message = asyncio.run(WhatsAppAdapter().receive_message(payload, db=None, tenant_id="co"))

    assert message.external_user_id == "+923445563662"
    assert message.metadata["raw_sender_id"] != "222436977021015"
    assert message.metadata["provider_sender_id"] == "222436977021015@lid"
    assert message.metadata["identity_source"] == "whatsapp_web_bridge"


def test_whatsapp_web_bridge_invalid_provider_id_is_skipped_when_no_phone_exists():
    payload = {
        "entry": [
            {
                "id": "whatsapp-web:scope",
                "changes": [
                    {
                        "value": {
                            "metadata": {"source": "whatsapp_web_bridge", "company_id": "co"},
                            "messages": [
                                {
                                    "id": "AC02BFA8012E57B30970D41BEB79EA1E",
                                    "from": "222436977021015",
                                    "type": "text",
                                    "text": {"body": "hi"},
                                    "web_bridge": {
                                        "raw_from": "222436977021015@lid",
                                        "provider_sender_id": "222436977021015@lid",
                                    },
                                }
                            ],
                            "contacts": [{"wa_id": "222436977021015"}],
                        }
                    }
                ],
            }
        ]
    }

    message = asyncio.run(WhatsAppAdapter().receive_message(payload, db=None, tenant_id="co"))

    assert message.external_user_id == ""
    assert message.metadata["provider_sender_id"] == "222436977021015@lid"
    assert message.metadata["identity_reason"]


def test_whatsapp_invalid_sender_id_is_rejected():
    identity = normalize_whatsapp_phone("182974364528890")

    assert not identity.is_valid
    assert identity.canonical_value == ""


def test_outbound_whatsapp_uses_customer_phone_before_channel_id():
    identity = resolve_outbound_recipient(
        "whatsapp",
        {"channel_id": "182974364528890"},
        {"phone": "+923001234567"},
    )

    assert identity.is_valid
    assert identity.canonical_value == "+923001234567"


def test_outbound_whatsapp_rejects_provider_id_when_no_phone_exists():
    identity = resolve_outbound_recipient(
        "whatsapp",
        {"channel_id": "182974364528890"},
        {"phone": ""},
    )

    assert not identity.is_valid
    assert identity.reason == "missing_valid_whatsapp_phone"


def test_instagram_and_facebook_ids_are_not_phone_numbers():
    instagram = normalize_social_channel_id("17841400000000000", "instagram")
    facebook = normalize_social_channel_id("100012345678901", "facebook")

    assert instagram.is_valid
    assert facebook.is_valid
    assert normalize_whatsapp_phone(instagram.canonical_value).is_valid is False
    assert normalize_whatsapp_phone(facebook.canonical_value).is_valid is False


def test_facebook_instagram_do_not_fall_back_to_crm_customer_id():
    instagram = resolve_outbound_recipient(
        "instagram",
        {"channel_id": ""},
        {"id": "crm-customer-id", "channel_profile_id": ""},
    )
    facebook = resolve_outbound_recipient(
        "facebook",
        {"channel_id": ""},
        {"id": "crm-customer-id", "channel_profile_id": ""},
    )

    assert not instagram.is_valid
    assert not facebook.is_valid
    assert instagram.reason == "missing_channel_id"
    assert facebook.reason == "missing_channel_id"


def test_email_address_normalization_is_not_overwritten_by_channel_id():
    email = normalize_email(" Customer+sales@Example.com ")
    channel_id = normalize_social_channel_id("17841400000000000", "instagram")

    assert email.is_valid
    assert email.canonical_value == "customer+sales@example.com"
    assert channel_id.canonical_value != email.canonical_value
