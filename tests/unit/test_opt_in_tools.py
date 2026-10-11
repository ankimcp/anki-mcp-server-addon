"""Unit tests for the opt-in-tools mechanism in tool_decorator.py (issue #74).

Mirrors ``test_destructive_tools.py`` -- opt_in=True gates a tool behind the
``enabled_opt_in_tools`` config allow-list the same way destructive=True gates
behind ``enabled_destructive_tools``. The two mechanisms share their
validation machinery (``_validate_enabled_gate_tools`` in tool_decorator.py),
so this file exercises the same shapes through the opt_in-specific entry
points (``validate_enabled_opt_in_tools`` and ``register_tools``'s
``enabled_opt_in_tools`` parameter).

Key difference from destructive: opt_in=True does NOT require write=True (no
Tool.__init__ guard), since opt-in tools aren't necessarily dangerous -- see
TestOptInWriteIndependence below.

IMPORTANT fixture note: same as test_destructive_tools.py -- fake meta dicts
that flow through register_tools or validate_enabled_opt_in_tools MUST include
both "destructive" and "opt_in" keys.
"""
from __future__ import annotations

import pytest

from anki_mcp_server.tool_decorator import (
    Tool,
    _registry,
    register_tools,
    validate_enabled_opt_in_tools,
)


# ---------------------------------------------------------------------------
# Test doubles (same shape as test_destructive_tools.py's _MockMCP)
# ---------------------------------------------------------------------------


class _MockMCP:
    def __init__(self) -> None:
        self.registered: list[dict] = []

    def tool(self, *, description, title=None, annotations=None):
        def decorator(fn):
            self.registered.append(
                {
                    "name": getattr(fn, "__name__", None),
                    "description": description,
                    "annotations": getattr(fn, "__annotations__", {}),
                    "fn": fn,
                }
            )
            return fn

        return decorator

    def names(self) -> set[str]:
        return {entry["name"] for entry in self.registered}

    def by_name(self, name: str) -> dict | None:
        for entry in self.registered:
            if entry["name"] == name:
                return entry
        return None


async def _call_main_thread(name, kwargs):  # pragma: no cover - never awaited
    return {}


@pytest.fixture
def patch_registry(monkeypatch):
    saved = dict(_registry)
    _registry.clear()

    def _install(entries: dict[str, dict]) -> None:
        _registry.clear()
        _registry.update(entries)

    yield _install

    _registry.clear()
    _registry.update(saved)


def _single_meta(name: str, *, opt_in: bool) -> dict:
    def handler() -> dict:
        return {}

    return {
        "name": name,
        "description": f"{name} description",
        "title": name,
        "original": handler,
        "write": True,
        "destructive": False,
        "opt_in": opt_in,
        "destructive_hint": True,
        "idempotent_hint": None,
        "open_world_hint": False,
    }


# ===========================================================================
# register_tools -- opt-in gating
# ===========================================================================


class TestRegisterToolsWholeTool:
    def test_opt_in_tool_hidden_when_not_opted_in(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_opt_in_tools=[])
        assert "gui_answer_card" not in mcp.names()

    def test_opt_in_tool_hidden_with_none_opt_in(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)  # no allow-list passed
        assert "gui_answer_card" not in mcp.names()

    def test_opt_in_tool_revealed_when_opted_in(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_opt_in_tools=["gui_answer_card"]
        )
        assert "gui_answer_card" in mcp.names()

    def test_non_opt_in_tool_always_present(self, patch_registry):
        patch_registry({"sync": _single_meta("sync", opt_in=False)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_opt_in_tools=[])
        assert "sync" in mcp.names()

    def test_opted_in_but_also_disabled_is_hidden(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["gui_answer_card"],
            enabled_opt_in_tools=["gui_answer_card"],
        )
        assert "gui_answer_card" not in mcp.names()

    def test_whole_tool_opt_in_is_exact_match_only(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_opt_in_tools=["other_tool"]
        )
        assert "gui_answer_card" not in mcp.names()

    def test_opt_in_and_destructive_gates_are_independent(self, patch_registry):
        """A tool marked both destructive and opt_in needs BOTH opt-ins."""

        def handler() -> dict:
            return {}

        patch_registry(
            {
                "danger": {
                    "name": "danger",
                    "description": "danger description",
                    "title": "Danger",
                    "original": handler,
                    "write": True,
                    "destructive": True,
                    "opt_in": True,
                    "destructive_hint": True,
                    "idempotent_hint": None,
                    "open_world_hint": False,
                }
            }
        )
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_destructive_tools=["danger"],
            enabled_opt_in_tools=[],
        )
        assert "danger" not in mcp.names(), "opt_in not satisfied -- still hidden"

        mcp2 = _MockMCP()
        register_tools(
            mcp2,
            _call_main_thread,
            enabled_destructive_tools=[],
            enabled_opt_in_tools=["danger"],
        )
        assert "danger" not in mcp2.names(), "destructive not satisfied -- still hidden"

        mcp3 = _MockMCP()
        register_tools(
            mcp3,
            _call_main_thread,
            enabled_destructive_tools=["danger"],
            enabled_opt_in_tools=["danger"],
        )
        assert "danger" in mcp3.names(), "both satisfied -- revealed"


# ===========================================================================
# validate_enabled_opt_in_tools
# ===========================================================================


class TestValidateEnabledOptInTools:
    @pytest.fixture
    def opt_in_registry(self, patch_registry):
        patch_registry(
            {
                "gui_answer_card": _single_meta("gui_answer_card", opt_in=True),
                "sync": _single_meta("sync", opt_in=False),
            }
        )

    def test_empty_list_no_warnings(self, opt_in_registry):
        assert validate_enabled_opt_in_tools([]) == []

    def test_nonexistent_tool_warns(self, opt_in_registry):
        warnings = validate_enabled_opt_in_tools(["nope"])
        assert len(warnings) == 1
        assert "'nope'" in warnings[0]
        assert "enabled_opt_in_tools" in warnings[0]
        assert "typo" in warnings[0]

    def test_tool_action_entry_warns(self, opt_in_registry):
        warnings = validate_enabled_opt_in_tools(["gui_answer_card:ghost"])
        assert len(warnings) == 1
        assert "no longer supported" in warnings[0]

    def test_real_but_not_opt_in_tool_is_noop_warning(self, opt_in_registry):
        warnings = validate_enabled_opt_in_tools(["sync"])
        assert len(warnings) == 1
        assert "no-op" in warnings[0]
        assert "'sync'" in warnings[0]

    def test_correct_opt_in_tool_no_warning(self, opt_in_registry):
        assert validate_enabled_opt_in_tools(["gui_answer_card"]) == []


# ===========================================================================
# opt_in does NOT require write=True (unlike destructive)
# ===========================================================================


class TestOptInWriteIndependence:
    def test_opt_in_without_write_is_allowed(self, patch_registry):
        patch_registry({})

        @Tool("read_only_opt_in", "desc", title="Read Only Opt In", write=False, opt_in=True)
        def read_only_opt_in() -> dict:
            return {}

        assert _registry["read_only_opt_in"]["opt_in"] is True
        assert _registry["read_only_opt_in"]["write"] is False

    def test_default_non_opt_in_ok(self, patch_registry):
        patch_registry({})

        @Tool("plain_opt_in_check", "desc", title="Plain Opt In Check")
        def plain_opt_in_check() -> dict:
            return {}

        assert _registry["plain_opt_in_check"]["opt_in"] is False
