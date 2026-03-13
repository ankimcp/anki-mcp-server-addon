"""Unit test configuration.

Stubs out ``aqt`` and the heavy parts of ``anki_mcp_server.__init__`` so that
pure-Python modules (tool_decorator, handler_registry, handler_wrappers) can be
imported without a running Anki instance.

This conftest runs *before* any test module imports, so we can inject stubs
into ``sys.modules`` before the package ``__init__`` triggers Anki-specific code.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


class _StubModule(types.ModuleType):
    """Module stub that returns MagicMock for any attribute access.

    Handles ``from aqt.qt import QTimer`` and similar imports by returning
    a fresh MagicMock for any name that isn't explicitly set.
    """

    def __getattr__(self, name: str) -> MagicMock:
        return MagicMock()


# ---------------------------------------------------------------------------
# 1. Stub aqt (Anki's Qt wrapper) -- must come before any anki_mcp_server import
# ---------------------------------------------------------------------------
if "aqt" not in sys.modules:
    aqt_stub = _StubModule("aqt")
    aqt_stub.mw = None  # type: ignore[attr-defined]
    sys.modules["aqt"] = aqt_stub

    for submod in ("qt", "gui_hooks", "utils", "addons"):
        mod = _StubModule(f"aqt.{submod}")
        sys.modules[f"aqt.{submod}"] = mod

# ---------------------------------------------------------------------------
# 2. Pre-create anki_mcp_server.dependency_loader so __init__ skips the
#    pydantic_core download machinery. ensure_pydantic_core just returns True.
# ---------------------------------------------------------------------------
if "anki_mcp_server.dependency_loader" not in sys.modules:
    dep_loader = types.ModuleType("anki_mcp_server.dependency_loader")
    dep_loader.ensure_pydantic_core = lambda: True  # type: ignore[attr-defined]
    sys.modules["anki_mcp_server.dependency_loader"] = dep_loader
