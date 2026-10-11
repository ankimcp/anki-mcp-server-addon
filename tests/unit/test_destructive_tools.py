"""Unit tests for the destructive-tools opt-in mechanism in tool_decorator.py.

Covers GitHub issue #46: high-risk tools ship hidden by default and must be
explicitly opted in via the ``enabled_destructive_tools`` config allow-list.

These tests exercise the pure-Python gating logic and require no Anki/aqt --
just the standard library. A ``_MockMCP`` recorder whose ``.tool()`` captures
what got registered lets us assert on which tools reached ``tools/list``.

IMPORTANT fixture note: the real ``_registry`` meta dicts carry
``"destructive"`` and ``"opt_in"`` keys. Any fake meta dict that flows through
``register_tools`` or ``validate_enabled_destructive_tools`` here MUST include
both, because those code paths read ``meta["destructive"]`` and
``meta["opt_in"]`` directly.
"""
from __future__ import annotations

import pytest

from anki_mcp_server.tool_decorator import (
    Tool,
    _registry,
    register_tools,
    validate_disabled_tools,
    validate_enabled_destructive_tools,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _MockMCP:
    """Recorder MCP whose .tool() captures (description, wrapper) per tool.

    ``register_tools`` -> ``_make_mcp_tool`` calls
    ``mcp.tool(title=..., description=..., annotations=...)`` and applies the
    returned decorator to the async wrapper. We capture the description and
    wrapper so tests can assert which tools registered.
    """

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

    # --- assertion helpers -------------------------------------------------

    def names(self) -> set[str]:
        return {entry["name"] for entry in self.registered}

    def by_name(self, name: str) -> dict | None:
        for entry in self.registered:
            if entry["name"] == name:
                return entry
        return None


async def _call_main_thread(name, kwargs):  # pragma: no cover - never awaited
    return {}


# ---------------------------------------------------------------------------
# Registry-mutation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_registry(monkeypatch):
    """Provide a callable that swaps _registry for a caller-built dict.

    Saves/restores the real registry around the test so import-time tool
    registration from the package is not clobbered.
    """
    saved = dict(_registry)
    _registry.clear()

    def _install(entries: dict[str, dict]) -> None:
        _registry.clear()
        _registry.update(entries)

    yield _install

    _registry.clear()
    _registry.update(saved)


def _single_meta(name: str, *, destructive: bool) -> dict:
    """Registry meta for a tool."""

    def handler() -> dict:
        return {}

    return {
        "name": name,
        "description": f"{name} description",
        "title": name,
        "original": handler,
        "write": True,
        "destructive": destructive,
        "opt_in": False,
        "destructive_hint": True,
        "idempotent_hint": None,
        "open_world_hint": False,
    }


# ===========================================================================
# register_tools -- destructive gating
# ===========================================================================


class TestRegisterToolsWholeTool:
    """Destructive gate via register_tools()."""

    def test_destructive_tool_hidden_when_not_opted_in(self, patch_registry):
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_destructive_tools=[])
        assert "delete_decks" not in mcp.names()

    def test_destructive_tool_hidden_with_none_opt_in(self, patch_registry):
        """enabled_destructive_tools=None behaves like an empty allow-list."""
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)  # no allow-list passed
        assert "delete_decks" not in mcp.names()

    def test_destructive_tool_revealed_when_opted_in(self, patch_registry):
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_destructive_tools=["delete_decks"]
        )
        assert "delete_decks" in mcp.names()

    def test_non_destructive_tool_always_present(self, patch_registry):
        patch_registry({"sync": _single_meta("sync", destructive=False)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_destructive_tools=[])
        assert "sync" in mcp.names()

    def test_opted_in_but_also_disabled_is_hidden(self, patch_registry):
        """disabled_tools wins even over an opted-in destructive tool."""
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["delete_decks"],
            enabled_destructive_tools=["delete_decks"],
        )
        assert "delete_decks" not in mcp.names()

    def test_whole_tool_opt_in_is_exact_match_only(self, patch_registry):
        """Opting in 'other_tool' does not reveal 'delete_decks'."""
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_destructive_tools=["other_tool"]
        )
        assert "delete_decks" not in mcp.names()

    def test_tool_action_entry_reveals_nothing(self, patch_registry):
        """A non-alias "tool:action" entry is not an opt-in for that tool."""
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_destructive_tools=["delete_decks:all"]
        )
        assert "delete_decks" not in mcp.names()


# ===========================================================================
# validate_enabled_destructive_tools
# ===========================================================================


class TestValidateEnabledDestructiveTools:
    """Tests for validate_enabled_destructive_tools()."""

    @pytest.fixture
    def destructive_registry(self, patch_registry):
        """Registry with a destructive tool and a safe tool."""
        patch_registry(
            {
                "delete_decks": _single_meta("delete_decks", destructive=True),
                "sync": _single_meta("sync", destructive=False),
            }
        )

    def test_empty_list_no_warnings(self, destructive_registry):
        assert validate_enabled_destructive_tools([]) == []

    def test_nonexistent_tool_warns(self, destructive_registry):
        warnings = validate_enabled_destructive_tools(["nope"])
        assert len(warnings) == 1
        assert "'nope'" in warnings[0]
        assert "enabled_destructive_tools" in warnings[0]
        assert "typo" in warnings[0]

    def test_tool_action_entry_warns(self, destructive_registry):
        warnings = validate_enabled_destructive_tools(["delete_decks:ghost"])
        assert len(warnings) == 1
        assert "'delete_decks:ghost'" in warnings[0]
        assert "no longer supported" in warnings[0]

    def test_real_but_not_destructive_whole_tool_is_noop_warning(
        self, destructive_registry
    ):
        """sync is a real tool but not destructive -> opting it in is a no-op."""
        warnings = validate_enabled_destructive_tools(["sync"])
        assert len(warnings) == 1
        assert "no-op" in warnings[0]
        assert "'sync'" in warnings[0]

    def test_correct_destructive_tool_no_warning(self, destructive_registry):
        """delete_decks is hidden from tools/list by default, yet validation
        must still recognize it (it reads the registry, not tools/list)."""
        assert validate_enabled_destructive_tools(["delete_decks"]) == []

    def test_mixed_entries_accumulate_warnings(self, destructive_registry):
        warnings = validate_enabled_destructive_tools(
            [
                "delete_decks",  # valid destructive -> no warning
                "sync",          # no-op (not destructive)
                "nope",          # typo
            ]
        )
        assert len(warnings) == 2


# ===========================================================================
# Tool.__init__ destructive guard (ValueError)
# ===========================================================================


class TestDestructiveWriteGuard:
    """Constructing @Tool(destructive=True) without write=True must fail."""

    def test_destructive_without_write_raises(self):
        with pytest.raises(ValueError, match="destructive=True requires write=True"):
            Tool("bad_tool", "desc", title="Bad Tool", destructive=True)

    def test_destructive_with_write_ok(self, patch_registry):
        # patch_registry gives us a clean registry so registration succeeds.
        patch_registry({})

        @Tool("ok_destructive", "desc", title="OK Destructive", write=True, destructive=True)
        def ok_destructive() -> dict:
            return {}

        assert _registry["ok_destructive"]["destructive"] is True
        assert _registry["ok_destructive"]["write"] is True

    def test_default_non_destructive_ok(self, patch_registry):
        patch_registry({})

        @Tool("plain", "desc", title="Plain")
        def plain() -> dict:
            return {}

        assert _registry["plain"]["destructive"] is False


# ===========================================================================
# disabled_tools validation is unaffected by the destructive flag
# ===========================================================================


class TestDisabledToolsRegression:
    """validate_disabled_tools emits the "disabled_tools" prefix and accepts
    a destructive tool like any other registered tool."""

    @pytest.fixture
    def reg(self, patch_registry):
        patch_registry(
            {
                "sync": _single_meta("sync", destructive=False),
                "delete_decks": _single_meta("delete_decks", destructive=True),
            }
        )

    def test_unknown_entry_message(self, reg):
        warnings = validate_disabled_tools(["nonexistent"])
        assert len(warnings) == 1
        assert warnings[0].startswith("disabled_tools: 'nonexistent'")
        assert "typo" in warnings[0]

    def test_validate_disabled_tools_valid_entries_no_warnings(self, reg):
        assert validate_disabled_tools(["sync", "delete_decks"]) == []
