from __future__ import annotations

from typing import Iterable


_CUSTOMER_SERVICE = "customer-service"
_PROVIDER_PURPOSES = ("meta-webhook", "qr-webhook", "meta-retry", "qr-retry", "provider-health")


def provider_queue_label(provider: str, purpose: str, *, service_name: str = _CUSTOMER_SERVICE) -> str:
    safe_service = str(service_name or _CUSTOMER_SERVICE).strip() or _CUSTOMER_SERVICE
    safe_provider = str(provider or "provider").strip().lower().replace("_", "-") or "provider"
    safe_purpose = str(purpose or "background").strip().lower().replace("_", "-") or "background"
    return f"{safe_service}.{safe_provider}-{safe_purpose}"


def provider_webhook_queue_label(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized in {"qr", "bridge", "whatsapp_web_bridge", "whatsapp-web-bridge"}:
        return provider_queue_label("qr", "webhook")
    return provider_queue_label("meta", "webhook")


def provider_retry_queue_label(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized in {"qr", "bridge", "whatsapp_web_bridge", "whatsapp-web-bridge"}:
        return provider_queue_label("qr", "retry")
    return provider_queue_label("meta", "retry")


def provider_worker_labels_for_service(service_name: str) -> tuple[str, ...]:
    if str(service_name or "").strip() != _CUSTOMER_SERVICE:
        return ()
    return tuple(f"{_CUSTOMER_SERVICE}.{purpose}" for purpose in _PROVIDER_PURPOSES)


def all_provider_queue_labels() -> Iterable[str]:
    return provider_worker_labels_for_service(_CUSTOMER_SERVICE)

