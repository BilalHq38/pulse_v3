from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DomainEvent:
    topic: str
    company_id: str
    payload: dict[str, Any]
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EventBus:
    async def publish(self, event: DomainEvent) -> None:
        raise NotImplementedError


class LoggingEventBus(EventBus):
    async def publish(self, event: DomainEvent) -> None:
        logger.info("domain_event topic=%s company_id=%s", event.topic, event.company_id)
