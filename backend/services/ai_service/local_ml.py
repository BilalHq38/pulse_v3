"""Compatibility alias for the conversation-engine local ML module.

The implementation lives in services.conversation_engine.local_ml. Keeping this
module name mapped to the same module object preserves old imports and test
monkeypatches without maintaining a second ONNX loader.
"""
from __future__ import annotations

import sys
from importlib import import_module

_module = import_module("services.conversation_engine.local_ml")
sys.modules[__name__] = _module
