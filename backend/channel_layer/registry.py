"""
channel_layer/registry.py — Central registry for channel adapters.

Business services use this registry to route messages to the correct adapter
without knowing which adapters exist or how they work.
"""

from __future__ import annotations

import logging
from typing import Iterator

from channel_layer.base import BaseChannelAdapter
from channel_layer.schemas import ChannelType

logger = logging.getLogger(__name__)


class ChannelAdapterRegistry:
    """
    Thread-safe registry mapping ChannelType → BaseChannelAdapter.

    Usage:
        registry = ChannelAdapterRegistry()
        registry.register(WhatsAppAdapter())
        registry.register(EmailAdapter())

        adapter = registry.get(ChannelType.WHATSAPP)
        message = await adapter.receive_message(payload, db, tenant_id)
    """

    def __init__(self) -> None:
        self._adapters: dict[ChannelType, BaseChannelAdapter] = {}

    def register(self, adapter: BaseChannelAdapter) -> None:
        """Register an adapter for its channel type."""
        if adapter.channel_type in self._adapters:
            logger.warning(
                "Overwriting adapter for %s: %s → %s",
                adapter.channel_type.value,
                self._adapters[adapter.channel_type],
                adapter,
            )
        self._adapters[adapter.channel_type] = adapter
        logger.info(
            "Registered channel adapter: %s → %s",
            adapter.channel_type.value,
            adapter.__class__.__name__,
        )

    def get(self, channel_type: ChannelType) -> BaseChannelAdapter:
        """
        Retrieve the adapter for a given channel type.

        Raises:
            KeyError: If no adapter is registered for this channel type.
        """
        adapter = self._adapters.get(channel_type)
        if adapter is None:
            raise KeyError(
                f"No adapter registered for channel: {channel_type.value}. "
                f"Available: {[ct.value for ct in self._adapters]}"
            )
        return adapter

    def get_or_none(self, channel_type: ChannelType) -> BaseChannelAdapter | None:
        """Retrieve the adapter or None if not registered."""
        return self._adapters.get(channel_type)

    def has(self, channel_type: ChannelType) -> bool:
        """Check if an adapter is registered for this channel type."""
        return channel_type in self._adapters

    def all_adapters(self) -> Iterator[BaseChannelAdapter]:
        """Iterate over all registered adapters."""
        yield from self._adapters.values()

    @property
    def supported_channels(self) -> list[str]:
        """List of all supported channel type values."""
        return sorted(ct.value for ct in self._adapters)

    async def health_check_all(self) -> list[dict]:
        """Run health checks on all registered adapters."""
        results = []
        for adapter in self._adapters.values():
            try:
                status = await adapter.health_check()
                results.append(status.model_dump())
            except Exception as exc:
                results.append(
                    {
                        "adapter": adapter.__class__.__name__,
                        "channel_type": adapter.channel_type.value,
                        "healthy": False,
                        "detail": str(exc),
                    }
                )
        return results

    def __repr__(self) -> str:
        entries = ", ".join(f"{ct.value}={a.__class__.__name__}" for ct, a in self._adapters.items())
        return f"<ChannelAdapterRegistry [{entries}]>"
