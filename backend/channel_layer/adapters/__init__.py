"""
channel_layer/adapters/__init__.py — Adapter discovery and initialization.
"""

from channel_layer.adapters.chat_widget import ChatWidgetAdapter
from channel_layer.adapters.email import EmailAdapter
from channel_layer.adapters.facebook import FacebookAdapter
from channel_layer.adapters.instagram import InstagramAdapter
from channel_layer.adapters.whatsapp import WhatsAppAdapter

__all__ = [
    "ChatWidgetAdapter",
    "EmailAdapter",
    "FacebookAdapter",
    "InstagramAdapter",
    "WhatsAppAdapter",
]
