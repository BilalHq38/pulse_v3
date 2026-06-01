"""Compatibility alias for services.ai_runtime.llm_client."""
from __future__ import annotations

import sys
from importlib import import_module

_module = import_module("services.ai_runtime.llm_client")
sys.modules[__name__] = _module
