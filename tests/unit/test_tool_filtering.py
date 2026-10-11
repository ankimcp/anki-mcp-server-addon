"""Unit tests for tool filtering helpers in tool_decorator.py.

These tests exercise the pure-Python filtering logic used by the
disabled_tools config feature. They do NOT require Anki or aqt -- just
the standard library.
"""
from __future__ import annotations

import pytest

from anki_mcp_server.tool_decorator import (
    _make_mcp_tool,
    _registry,
    _validate_tool_entries,
    register_tools,
    validate_disabled_tools,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _MockMCP:
    """Recorder whose .tool() captures what got registered."""

    def __init__(self) -> None:
        self.registered: dict[str, dict] = {}

    def tool(self, *, description, title=None, annotations=None):
        def decorator(fn):
            self.registered[fn.__name__] = {
                "description": description,
                "title": title,
                "fn": fn,
            }
            return fn

        return decorator


async def _call_main_thread(name, kwargs):  # pragma: no cover - never awaited
    return {}


def _meta(name: str) -> dict:
    def handler(card_ids: list[int]) -> dict:
        return {}

    return {
        "name": name,
        "description": f"{name} description",
        "title": name,
        "original": handler,
        "write": True,
        "destructive": False,
        "opt_in": False,
        "destructive_hint": True,
        "idempotent_hint": None,
        "open_world_hint": False,
    }


@pytest.fixture
def fake_registry():
    """Populate _registry with two plain tools for validation tests."""
    saved = dict(_registry)
    _registry.clear()
    _registry["sync"] = _meta("sync")
    _registry["card_bury"] = _meta("card_bury")

    yield

    _registry.clear()
    _registry.update(saved)


# ===========================================================================
# _validate_tool_entries
# ===========================================================================


class TestValidateToolEntries:
    def test_empty_returns_no_warnings(self, fake_registry):
        assert _validate_tool_entries([]) == []

    def test_valid_tool_returns_no_warnings(self, fake_registry):
        assert _validate_tool_entries(["sync", "card_bury"]) == []

    def test_unknown_tool_returns_warning(self, fake_registry):
        warnings = _validate_tool_entries(["nonexistent"])
        assert len(warnings) == 1
        assert warnings[0].startswith("disabled_tools: 'nonexistent'")
        assert "typo" in warnings[0]

    def test_tool_action_entry_matches_nothing(self, fake_registry):
        """A non-alias "tool:action" entry matches nothing and says so."""
        warnings = _validate_tool_entries(["sync:rebuild"])
        assert len(warnings) == 1
        assert "'sync:rebuild'" in warnings[0]
        assert "no longer supported" in warnings[0]

    def test_duplicates_warn_once(self, fake_registry):
        assert len(_validate_tool_entries(["nope", "nope"])) == 1

    def test_warnings_are_sorted(self, fake_registry):
        warnings = _validate_tool_entries(["zzz", "aaa"])
        assert len(warnings) == 2
        assert "'aaa'" in warnings[0]
        assert "'zzz'" in warnings[1]

    def test_config_key_changes_prefix_only(self, fake_registry):
        default = _validate_tool_entries(["nonexistent"])
        custom = _validate_tool_entries(
            ["nonexistent"], config_key="enabled_destructive_tools"
        )
        assert default[0].replace("disabled_tools", "enabled_destructive_tools") == custom[0]


# ===========================================================================
# validate_disabled_tools (public API)
# ===========================================================================


class TestValidateDisabledTools:
    def test_empty_list_returns_no_warnings(self, fake_registry):
        assert validate_disabled_tools([]) == []

    def test_valid_entries_return_no_warnings(self, fake_registry):
        assert validate_disabled_tools(["sync", "card_bury"]) == []

    def test_typo_in_tool_name(self, fake_registry):
        warnings = validate_disabled_tools(["syncc"])
        assert len(warnings) == 1
        assert "'syncc'" in warnings[0]

    def test_mixed_valid_and_invalid(self, fake_registry):
        warnings = validate_disabled_tools(["sync", "nonexistent", "sync:something"])
        assert len(warnings) == 2

    def test_action_on_plain_tool_matches_nothing(self, fake_registry):
        warnings = validate_disabled_tools(["sync:something"])
        assert len(warnings) == 1
        assert warnings[0].startswith("disabled_tools:")
        assert "no longer supported" in warnings[0]


# ===========================================================================
# register_tools -- disabled_tools filtering
# ===========================================================================


class TestRegisterToolsDisabled:
    def test_disabled_tool_is_skipped(self, fake_registry):
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, disabled_tools=["sync"])
        assert set(mcp.registered) == {"card_bury"}

    def test_no_disabled_registers_everything(self, fake_registry):
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)
        assert set(mcp.registered) == {"sync", "card_bury"}

    def test_tool_action_entry_disables_nothing(self, fake_registry, capsys):
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, disabled_tools=["card_bury:x"])
        assert set(mcp.registered) == {"sync", "card_bury"}
        assert "'card_bury:x'" in capsys.readouterr().out


# ===========================================================================
# _make_mcp_tool
# ===========================================================================


class TestMakeMcpTool:
    def test_registers_description_title_and_signature(self):
        meta = _meta("card_bury")
        mcp = _MockMCP()
        _make_mcp_tool(mcp, _call_main_thread, "card_bury", meta)

        entry = mcp.registered["card_bury"]
        assert entry["description"] == "card_bury description"
        assert entry["title"] == "card_bury"
        assert list(entry["fn"].__signature__.parameters) == ["card_ids"]
