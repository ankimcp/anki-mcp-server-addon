"""Unit test configuration.

Bootstraps vendored pydantic + on-demand pydantic_core (same mechanism the
addon uses at runtime) and stubs out ``aqt`` plus the heavy parts of
``anki_mcp_server.__init__`` so that pure-Python modules can be imported
without a running Anki instance.

This conftest runs *before* any test module imports, so we can inject stubs
into ``sys.modules`` before the package ``__init__`` triggers Anki-specific code.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


class _StubModule(types.ModuleType):
    """Module stub that returns MagicMock for any attribute access.

    Handles ``from aqt.qt import QTimer`` and similar imports by returning
    a fresh MagicMock for any name that isn't explicitly set.
    """

    def __getattr__(self, name: str) -> MagicMock:
        return MagicMock()


# ---------------------------------------------------------------------------
# 1. Bootstrap vendored pydantic + pydantic_core for tests
#
# Mirror __init__.py's vendor-path prepend so vendored pydantic 2.x wins, then
# call the pure-logic loader to fetch pydantic_core from PyPI (cached under
# anki_mcp_server/_cache/ across runs).
#
# We load dependency_loader.py as a standalone module to avoid triggering
# anki_mcp_server/__init__.py, which would otherwise call the Qt-coupled
# ensure_pydantic_core wrapper.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VENDOR_SHARED = _REPO_ROOT / "anki_mcp_server" / "vendor" / "shared"
if _VENDOR_SHARED.exists() and str(_VENDOR_SHARED) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARED))

_loader_path = _REPO_ROOT / "anki_mcp_server" / "dependency_loader.py"
_spec = importlib.util.spec_from_file_location("_anki_mcp_loader", _loader_path)
_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_loader)

_bootstrap_errors: list[str] = []

if not _loader._ensure_pydantic_core_with_callbacks(on_error=_bootstrap_errors.append):
    reason = "; ".join(_bootstrap_errors) if _bootstrap_errors else "(no reason reported)"
    raise RuntimeError(
        f"Failed to bootstrap pydantic_core for unit tests: {reason}"
    )

# ---------------------------------------------------------------------------
# 2. Stub aqt (Anki's Qt wrapper) -- must come before any anki_mcp_server import
# ---------------------------------------------------------------------------
if "aqt" not in sys.modules:
    aqt_stub = _StubModule("aqt")
    aqt_stub.mw = None  # type: ignore[attr-defined]
    sys.modules["aqt"] = aqt_stub

    for submod in ("qt", "gui_hooks", "utils", "addons"):
        mod = _StubModule(f"aqt.{submod}")
        sys.modules[f"aqt.{submod}"] = mod

# ---------------------------------------------------------------------------
# 3. Stub anki_mcp_server.primitives at the BOUNDARY (auto-discovery entry point).
#
# WHY this lives here instead of per-test or as a fixture: importing
# ``mcp_server.py`` triggers ``from .primitives import register_all_*`` at
# module load time. ``primitives/__init__.py`` re-exports
# ``register_all_{tools,resources,prompts}``, each of which is wired to
# ``pkgutil.walk_packages`` auto-discovery that imports *every* tool module
# under ``primitives.essential.tools`` and ``primitives.gui.tools``.
#
# Today most of those tool modules import ``aqt``/``anki`` lazily inside
# their handlers — so the auto-discovery happens to succeed against the
# ``_StubModule`` above. But that is incidental: the moment somebody adds a
# tool with a top-level ``from anki.collection import AddNoteRequest`` (or
# similar), unit-test collection breaks with a cryptic ImportError, even
# though the tests in scope here never exercise that tool.
#
# Stubbing the boundary (``anki_mcp_server.primitives``) instead of chasing
# individual Anki APIs decouples this test infrastructure from the tool
# inventory. New tools "just work" — they're invisible to the unit tests
# that don't need them. Tests that DO want real primitives (e.g.
# ``test_in_memory_transport.py``) don't import ``mcp_server`` and so don't
# touch this stub at all.
# ---------------------------------------------------------------------------
if "anki_mcp_server.primitives" not in sys.modules:
    primitives_stub = types.ModuleType("anki_mcp_server.primitives")
    primitives_stub.register_all_tools = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    primitives_stub.register_all_resources = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    primitives_stub.register_all_prompts = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    sys.modules["anki_mcp_server.primitives"] = primitives_stub
