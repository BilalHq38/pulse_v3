"""
channel_layer — Unified omni-channel abstraction for Pulse Engine.

All inbound/outbound communication flows through this layer, ensuring
no channel-specific logic leaks into business services.
"""

from channel_layer.schemas import ChannelType, UnifiedMessage  # noqa: F401
from channel_layer.base import BaseChannelAdapter  # noqa: F401
from channel_layer.registry import ChannelAdapterRegistry  # noqa: F401
