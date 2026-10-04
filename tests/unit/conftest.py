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

# Bootstrap rpds the same way. We no longer vendor rpds (issue #54) — at runtime
# the addon's ensure_rpds() finds it from Anki's environment. In unit tests there
# is no Anki and aqt is stubbed (so the Qt-coupled download path can't run), so
# we pre-seed rpds headlessly here. This makes the fast-path `import rpds` inside
# anki_mcp_server.__init__'s ensure_rpds() a no-op, exactly like pydantic_core.
_rpds_errors: list[str] = []
if not _loader._ensure_rpds_with_callbacks(on_error=_rpds_errors.append):
    reason = "; ".join(_rpds_errors) if _rpds_errors else "(no reason reported)"
    raise RuntimeError(
        f"Failed to bootstrap rpds for unit tests: {reason}"
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


# ---------------------------------------------------------------------------
# 4. Fixtures for the async sync orchestration (_sync_runner.py)
#
# _sync_runner lives UNDER anki_mcp_server.primitives, which section 3 replaces
# with a non-package boundary stub (to suppress tool auto-discovery). So it
# cannot be imported via the normal dotted path. We load it directly from its
# file under its real dotted name; its relative imports (....handler_wrappers,
# ....sync_state) still resolve to the real top-level modules, and no
# walk_packages discovery runs.
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture(scope="session")
def sync_runner():
    """Load and return the real anki_mcp_server ..._sync_runner module."""
    import anki_mcp_server  # real top-level package (idempotent import)

    name = "anki_mcp_server.primitives.essential.tools._sync_runner"
    if name in sys.modules:
        return sys.modules[name]

    path = (
        Path(anki_mcp_server.__file__).parent
        / "primitives"
        / "essential"
        / "tools"
        / "_sync_runner.py"
    )
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def sync_tool(sync_runner):
    """Load and return the real ``sync`` tool module (with its @Tool ``sync``).

    Loaded exactly like ``sync_runner`` -- directly from file under its real
    dotted name -- so the tool's lazy ``from ._sync_runner import ...`` resolves
    the already-registered runner module straight from ``sys.modules`` (skipping
    the stubbed ``primitives`` parent-package chain). Depends on ``sync_runner``
    to guarantee that registration happened first.

    ``@Tool.__call__`` returns the original, unwrapped function, so
    ``sync_tool.sync`` is directly callable in tests (no error-handler wrapper).
    """
    import anki_mcp_server  # real top-level package (idempotent import)

    name = "anki_mcp_server.primitives.essential.tools.sync_tool"
    if name in sys.modules:
        return sys.modules[name]

    path = (
        Path(anki_mcp_server.__file__).parent
        / "primitives"
        / "essential"
        / "tools"
        / "sync_tool.py"
    )
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fresh_registry(sync_runner, monkeypatch):
    """Give each test an isolated registry singleton for _sync_runner.

    _sync_runner references its module-global ``registry``; swapping that global
    for a fresh instance isolates orchestration tests from one another and from
    the process-wide singleton.
    """
    from anki_mcp_server.sync_state import SyncJobRegistry

    reg = SyncJobRegistry()
    monkeypatch.setattr(sync_runner, "registry", reg)
    return reg


# ---------------------------------------------------------------------------
# 5. Fake Anki objects for driving _sync_runner orchestration deterministically.
#
# The FakeTaskman runs the background task SYNCHRONOUSLY and immediately invokes
# on_done with a resolved/failed future -- so a single start_sync()/resolve_sync()
# call drives the entire flow (worker -> on_done -> optional media monitor ->
# finalize) to completion in-line, with no real threads or sleeps.
# ---------------------------------------------------------------------------
import types as _types  # noqa: E402


class _FakeFuture:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeTaskman:
    """Synchronous taskman. ``fail_rib_on`` / ``fail_wp_on`` are sets of 1-based
    call indices at which the corresponding spawn should raise (to simulate a
    spawn failure)."""

    def __init__(self):
        self.rib_calls = 0
        self.wp_calls = 0
        self.fail_rib_on: set[int] = set()
        self.fail_wp_on: set[int] = set()
        self.wp_labels: list = []

    def _run(self, task, on_done):
        try:
            fut = _FakeFuture(result=task())
        except Exception as exc:  # noqa: BLE001
            fut = _FakeFuture(exc=exc)
        if on_done is not None:
            on_done(fut)

    def run_in_background(self, task, on_done=None, uses_collection=True):
        self.rib_calls += 1
        if self.rib_calls in self.fail_rib_on:
            raise RuntimeError("run_in_background spawn failed")
        self._run(task, on_done)

    def with_progress(
        self,
        task,
        on_done=None,
        parent=None,
        label=None,
        immediate=False,
        uses_collection=True,
        title="Anki",
    ):
        self.wp_calls += 1
        self.wp_labels.append(label)
        if self.wp_calls in self.fail_wp_on:
            raise RuntimeError("with_progress spawn failed")
        self._run(task, on_done)


class _FakeSyncOut:
    def __init__(self, required, server_media_usn=7):
        self.required = required
        self.server_media_usn = server_media_usn


class _FakeMediaStatus:
    def __init__(self, active):
        self.active = active


class _FakeCol:
    """Records every call in ``self.calls`` for ordering/assertion.

    ``gate_probe`` (if given) is invoked during media_sync_status() so a test can
    observe the gate state at media-polling time.
    """

    def __init__(
        self,
        required=0,
        server_media_usn=7,
        media_active_seq=None,
        sync_exc=None,
        transfer_exc=None,
        media_exc=None,
        gate_probe=None,
    ):
        self.db = object()  # open collection
        self._required = required
        self._server_media_usn = server_media_usn
        self._sync_exc = sync_exc
        self._transfer_exc = transfer_exc
        self._media_exc = media_exc
        self._media_active_seq = list(media_active_seq or [False])
        self._gate_probe = gate_probe
        self.calls: list = []

    def sync_collection(self, auth, sync_media):
        self.calls.append(("sync_collection", sync_media))
        if self._sync_exc is not None:
            raise self._sync_exc
        return _FakeSyncOut(self._required, self._server_media_usn)

    def sync_media(self, auth):  # must NEVER be called by the new design
        self.calls.append(("sync_media",))

    def media_sync_status(self):
        self.calls.append(("media_sync_status",))
        if self._gate_probe is not None:
            self._gate_probe()
        if self._media_exc is not None:
            raise self._media_exc
        active = self._media_active_seq.pop(0) if self._media_active_seq else False
        return _FakeMediaStatus(active)

    def close_for_full_sync(self):
        self.calls.append(("close_for_full_sync",))
        self.db = None

    def full_upload_or_download(self, *, auth, server_usn, upload):
        self.calls.append(("full_upload_or_download", server_usn, upload))
        if self._transfer_exc is not None:
            raise self._transfer_exc

    def _load_scheduler(self):
        self.calls.append(("_load_scheduler",))


class _FakePm:
    def __init__(self, auth="auth-token", media=True):
        self._auth = auth
        self._media = media

    def sync_auth(self):
        return self._auth

    def media_syncing_enabled(self):
        return self._media


class _FakeProgress:
    def __init__(self):
        self.finished = 0

    def finish(self):
        self.finished += 1


class _FakeMw:
    def __init__(self, col, taskman, pm):
        self.col = col
        self.taskman = taskman
        self.pm = pm
        self.progress = _FakeProgress()
        self.reopened = 0
        self.reset_calls = 0
        self.backups = 0

    def reopen(self, after_full_sync=False):
        self.reopened += 1
        self.col.db = object()

    def reset(self):
        self.reset_calls += 1

    def create_backup_now(self):
        self.backups += 1
        # Record on the shared call log so ordering vs close/transfer is testable.
        self.col.calls.append(("create_backup_now",))


@pytest.fixture()
def sync_fakes():
    """Namespace of fake Anki building blocks for _sync_runner tests."""
    return _types.SimpleNamespace(
        Future=_FakeFuture,
        Taskman=_FakeTaskman,
        Col=_FakeCol,
        Pm=_FakePm,
        Mw=_FakeMw,
    )


@pytest.fixture()
def install_mw(monkeypatch):
    """Install a fake ``mw`` on the (stubbed) aqt module for get_mw()."""

    def _install(mw):
        monkeypatch.setattr(sys.modules["aqt"], "mw", mw, raising=False)
        return mw

    return _install


# ---------------------------------------------------------------------------
# 6. The REAL primitives tree + a real FastMCP populated with every tool.
#
# Section 3 stubs ``anki_mcp_server.primitives`` so unrelated tests never
# import the tool modules. Tests that inspect the actual ``tools/list`` schema
# a client would see (or the real wrapped handlers in ``handler_registry``)
# need the OPPOSITE. ``_ensure_real_primitives`` discards the stub, imports
# the real ``anki_mcp_server.primitives`` package tree -- which runs the
# ``pkgutil.walk_packages`` auto-discovery of every ``@Tool`` (what
# ``mcp_server.py`` does at startup, minus uvicorn/Qt) -- and then puts the
# stub entries back so later test modules still see the lightweight boundary.
# It is a one-time detour, not a permanent swap.
#
# It runs from ``pytest_collection_modifyitems`` -- after every test module
# has been imported but before any fixture runs -- and only when a collected
# test needs ``real_mcp``/``tools_list``, so single-file runs of unrelated
# tests keep the stub-only boundary. Running it before any fixture matters:
# fixtures like ``sync_tool`` above load a @Tool module by file under its real
# dotted name, and a later real import would re-register that tool.
#
# ``aqt``/``anki`` are not imported at module scope by any tool file, so this
# works against the ``aqt`` stub from section 2 -- no real Anki needed.
# ---------------------------------------------------------------------------
_REAL_PRIMITIVES_FIXTURES = frozenset({"real_mcp", "tools_list"})
_real_primitives_tools: types.ModuleType | None = None


def _ensure_real_primitives() -> types.ModuleType:
    global _real_primitives_tools
    if _real_primitives_tools is not None:
        return _real_primitives_tools

    import importlib

    stubbed = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "anki_mcp_server.primitives"
        or name.startswith("anki_mcp_server.primitives.")
    }
    for name in stubbed:
        del sys.modules[name]

    _real_primitives_tools = importlib.import_module("anki_mcp_server.primitives.tools")

    for name, module in stubbed.items():
        sys.modules[name] = module

    return _real_primitives_tools


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    if any(_REAL_PRIMITIVES_FIXTURES & set(getattr(item, "fixturenames", ())) for item in items):
        _ensure_real_primitives()


async def _noop_call_main_thread(*args, **kwargs) -> dict:
    """Tools are never invoked through FastMCP here -- only registered for listing."""
    return {}


def _all_whole_tool_gated(meta_key: str) -> list[str]:
    """Every registered tool whose WHOLE-TOOL gate flag (``meta_key``, one of
    ``"destructive"``/``"opt_in"``) is set -- derived from ``_registry`` so
    ``real_mcp`` can't silently stop covering a tool's schema just because a
    new gated primitive was added and nobody remembered to hand-list it.

    Per-action gated entries (e.g. model_fields:remove) are deliberately NOT
    included: opting in a single action of a multi-action tool wouldn't
    change anything observed here, since the tool still exposes one
    top-level "params" property regardless of which actions are enabled.
    """
    from anki_mcp_server.tool_decorator import _registry

    return [name for name, meta in _registry.items() if meta[meta_key]]


@pytest.fixture(scope="session")
def real_mcp():
    from mcp.server.fastmcp import FastMCP

    primitives_tools = _ensure_real_primitives()
    mcp = FastMCP("test-server")
    primitives_tools.register_all_tools(
        mcp,
        _noop_call_main_thread,
        disabled_tools=None,
        # Opt in every whole-tool-destructive / whole-tool-opt-in primitive
        # (see CLAUDE.md "Tool Filtering") so their schemas are covered too --
        # see _all_whole_tool_gated's docstring for why per-action entries
        # aren't derived. model_fields:remove is named explicitly so its
        # per-action description is on the wire for description scans; it
        # doesn't change the tool count.
        enabled_destructive_tools=[
            *_all_whole_tool_gated("destructive"),
            "model_fields:remove",
        ],
        enabled_opt_in_tools=_all_whole_tool_gated("opt_in"),
    )
    return mcp


@pytest.fixture(scope="session")
def tools_list(real_mcp):
    import asyncio

    from anki_mcp_server.tool_decorator import _registry

    result = asyncio.run(real_mcp.list_tools())
    # Compare against the decorator's own registry so a test can't pass
    # vacuously (e.g. if auto-discovery silently registered zero tools) and
    # doesn't need a hand-maintained floor that drifts as tools are added.
    # Every whole-tool-gated primitive is opted in by real_mcp, so every
    # registered tool should be listed -- counts should be equal, not just >=.
    assert len(result) == len(_registry), (
        f"Expected {len(_registry)} registered tools (from _registry), got "
        f"{len(result)} -- auto-discovery or destructive opt-in may be broken"
    )
    return result
