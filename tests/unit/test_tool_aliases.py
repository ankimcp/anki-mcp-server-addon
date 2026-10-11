"""Unit tests for the config aliases of the retired multi-action tools.

card_management, tag_management, filtered_deck and model_fields were split
into one tool per action. ``TOOL_ALIASES`` in tool_decorator.py keeps old
``disabled_tools`` / ``enabled_destructive_tools`` / ``enabled_opt_in_tools``
entries working: "old_tool:action" behaves like the new tool's name, and a
whole-tool "old_tool" in disabled_tools behaves like listing its whole group.

Pure-Python, no Anki/aqt. Mirrors the fake-registry style of
``test_tool_filtering.py`` / ``test_destructive_tools.py``: the registry is
swapped for single-action fakes carrying every meta key ``register_tools``
reads, and a ``_MockMCP`` records what reached ``tools/list``.
"""
from __future__ import annotations

import pytest

from anki_mcp_server import tool_decorator
from anki_mcp_server.tool_decorator import (
    TOOL_ALIASES,
    _GROUP_ALIASES,
    _registry,
    _resolve_aliases,
    register_tools,
    validate_disabled_tools,
    validate_enabled_destructive_tools,
    validate_enabled_opt_in_tools,
)

_CARD_TOOLS = (
    "card_reposition",
    "card_change_deck",
    "card_bury",
    "card_unbury",
    "card_suspend",
    "card_unsuspend",
    "card_set_flag",
    "card_set_due_date",
    "card_forget",
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _MockMCP:
    """Recorder whose .tool() captures the name of every registered tool."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def tool(self, *, description, title=None, annotations=None):
        def decorator(fn):
            self.names.add(fn.__name__)
            return fn

        return decorator


async def _call_main_thread(name, kwargs):  # pragma: no cover - never awaited
    return {}


def _meta(name: str, *, destructive: bool = False, opt_in: bool = False) -> dict:
    def handler() -> dict:
        return {}

    return {
        "name": name,
        "description": f"{name} description",
        "title": name,
        "original": handler,
        "write": True,
        "destructive": destructive,
        "opt_in": opt_in,
        "destructive_hint": True,
        "idempotent_hint": None,
        "open_world_hint": False,
    }


@pytest.fixture(autouse=True)
def fresh_alias_reports(monkeypatch):
    """Each test starts with no deprecation warning printed yet."""
    monkeypatch.setattr(tool_decorator, "_reported_alias_warnings", set())


@pytest.fixture
def split_registry():
    """Every alias target as a registered tool (model_field_remove is
    destructive), plus one unrelated tool ("sync") and one opt-in tool.
    """
    saved = dict(_registry)
    _registry.clear()
    for name in TOOL_ALIASES.values():
        _registry[name] = _meta(name, destructive=name == "model_field_remove")
    _registry["sync"] = _meta("sync")
    _registry["gui_answer_card"] = _meta("gui_answer_card", opt_in=True)

    yield

    _registry.clear()
    _registry.update(saved)


# ===========================================================================
# The alias table
# ===========================================================================


class TestAliasTable:
    def test_every_target_is_unique(self):
        targets = list(TOOL_ALIASES.values())
        assert len(targets) == len(set(targets))

    def test_groups_are_derived_from_the_action_aliases(self):
        assert set(_GROUP_ALIASES) == {
            "card_management", "tag_management", "filtered_deck", "model_fields",
        }
        for group, targets in _GROUP_ALIASES.items():
            expected = tuple(
                new for old, new in TOOL_ALIASES.items()
                if old.startswith(f"{group}:")
            )
            assert targets == expected

    def test_no_target_reuses_a_retired_tool_name(self):
        assert not set(TOOL_ALIASES.values()) & set(_GROUP_ALIASES)

    def test_every_target_is_a_real_tool(self, tools_list):
        """An alias pointing at a tool that doesn't exist would silently
        disable/opt in nothing. tools_list opts in every gated tool, so
        model_field_remove is listed too."""
        names = {tool.name for tool in tools_list}
        assert set(TOOL_ALIASES.values()) <= names

    def test_retired_names_are_not_real_tools(self, tools_list):
        names = {tool.name for tool in tools_list}
        assert not set(_GROUP_ALIASES) & names


# ===========================================================================
# _resolve_aliases
# ===========================================================================


class TestResolveAliases:
    def test_action_alias_resolves_to_the_new_tool(self):
        res = _resolve_aliases(["card_management:bury"], "disabled_tools")
        assert res.plain == []
        assert res.replacements == ["card_bury"]

    def test_whole_tool_alias_expands_to_its_group(self):
        res = _resolve_aliases(["card_management"], "disabled_tools")
        assert res.plain == []
        assert res.replacements == list(_CARD_TOOLS)

    def test_whole_tool_alias_expands_to_nothing_in_an_allow_list(
        self, split_registry
    ):
        res = _resolve_aliases(
            ["model_fields"], "enabled_destructive_tools", gate_key="destructive"
        )
        assert res.replacements == []
        assert res.deprecations == [
            "enabled_destructive_tools: 'model_fields' is deprecated and has "
            "no effect (a whole-tool entry never opted in its actions) -- "
            "list 'model_field_remove' instead"
        ]

    def test_allow_list_warning_names_only_gated_tools(self, split_registry):
        """model_field_add/rename/reposition aren't destructive, so listing
        them would be a no-op -- the warning must not suggest them."""
        res = _resolve_aliases(
            ["model_fields"], "enabled_destructive_tools", gate_key="destructive"
        )
        for ungated in ("model_field_add", "model_field_rename", "model_field_reposition"):
            assert ungated not in res.deprecations[0]

    def test_allow_list_group_without_gated_tools_says_remove(self, split_registry):
        res = _resolve_aliases(
            ["card_management"], "enabled_opt_in_tools", gate_key="opt_in"
        )
        assert res.replacements == []
        assert res.deprecations == [
            "enabled_opt_in_tools: 'card_management' is deprecated and has no "
            "effect (none of its tools are opt-in) -- remove the entry"
        ]

    def test_new_names_pass_through_untouched(self):
        res = _resolve_aliases(
            ["card_bury", "sync", "unknown_thing"], "disabled_tools"
        )
        assert res.plain == ["card_bury", "sync", "unknown_thing"]
        assert res.replacements == []
        assert res.deprecations == []

    def test_deprecation_names_the_replacement(self):
        res = _resolve_aliases(["tag_management:get_tags"], "disabled_tools")
        assert res.deprecations == [
            "disabled_tools: 'tag_management:get_tags' is deprecated "
            "-- use 'tag_list' instead"
        ]

    def test_repeated_alias_reported_once(self):
        res = _resolve_aliases(
            ["card_management:bury", "card_management:bury"], "disabled_tools"
        )
        assert res.replacements == ["card_bury"]
        assert len(res.deprecations) == 1

    def test_overlapping_aliases_do_not_duplicate_replacements(self):
        res = _resolve_aliases(
            ["card_management:bury", "card_management"], "disabled_tools"
        )
        assert sorted(res.replacements) == sorted(_CARD_TOOLS)
        assert len(res.deprecations) == 2


# ===========================================================================
# Validation: aliases are valid, typos are still typos
# ===========================================================================


class TestValidateWithAliases:
    def test_action_alias_is_not_a_typo(self, split_registry):
        warnings = validate_disabled_tools(["card_management:bury"])
        assert len(warnings) == 1
        assert "deprecated" in warnings[0]
        assert "'card_bury'" in warnings[0]

    def test_whole_tool_alias_is_not_a_typo(self, split_registry):
        warnings = validate_disabled_tools(["tag_management"])
        assert len(warnings) == 1
        assert "deprecated" in warnings[0]

    def test_unknown_entry_still_warns_as_typo(self, split_registry):
        warnings = validate_disabled_tools(["card_managment:bury", "syncc"])
        assert len(warnings) == 2
        assert all("typo" in w for w in warnings)

    def test_unknown_action_of_retired_tool_warns_as_typo(self, split_registry):
        warnings = validate_disabled_tools(["card_management:ghost"])
        assert len(warnings) == 1
        assert "typo" in warnings[0]

    def test_unknown_action_of_retired_tool_in_allow_list(self, split_registry):
        warnings = validate_enabled_opt_in_tools(["card_management:ghost"])
        assert len(warnings) == 1
        assert "typo" in warnings[0]

        baseline = _MockMCP()
        register_tools(baseline, _call_main_thread)
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_opt_in_tools=["card_management:ghost"],
        )
        assert mcp.names == baseline.names
        assert "gui_answer_card" not in mcp.names

    def test_new_names_produce_no_warnings(self, split_registry):
        assert validate_disabled_tools(["card_bury", "sync"]) == []
        assert validate_enabled_destructive_tools(["model_field_remove"]) == []
        assert validate_enabled_opt_in_tools(["gui_answer_card"]) == []

    def test_destructive_alias_is_valid_and_not_a_no_op(self, split_registry):
        warnings = validate_enabled_destructive_tools(["model_fields:remove"])
        assert len(warnings) == 1
        assert "deprecated" in warnings[0]
        assert "'model_field_remove'" in warnings[0]

    def test_alias_to_an_ungated_tool_is_a_no_op(self, split_registry):
        warnings = validate_enabled_opt_in_tools(["card_management:bury"])
        assert len(warnings) == 2
        assert "deprecated" in warnings[0]
        assert "no-op" in warnings[1]
        assert "'card_bury'" in warnings[1]

    def test_whole_tool_alias_in_allow_list_warns_no_effect(self, split_registry):
        warnings = validate_enabled_destructive_tools(["model_fields"])
        assert len(warnings) == 1
        assert "has no effect" in warnings[0]


# ===========================================================================
# The real registry: which tools are gated, and how aliases open the gate
# ===========================================================================


class TestRealRegistryGates:
    """Against the real auto-discovered tools, not the fakes above -- so a
    tool that loses (or wrongly gains) its gate flag fails here. ``real_mcp``
    is requested only to load the real primitives into ``_registry``."""

    def test_destructive_set(self, real_mcp):
        assert {n for n, m in _registry.items() if m["destructive"]} == {
            "change_note_type", "model_field_remove",
        }

    def test_opt_in_set(self, real_mcp):
        assert {n for n, m in _registry.items() if m["opt_in"]} == {
            "gui_deck_review", "gui_answer_card",
        }

    def test_model_field_remove_hidden_by_default(self, real_mcp):
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)
        assert "model_field_remove" not in mcp.names
        assert "model_field_add" in mcp.names

    def test_whole_tool_alias_does_not_reveal_model_field_remove(self, real_mcp):
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_destructive_tools=["model_fields"],
        )
        assert "model_field_remove" not in mcp.names

    def test_action_alias_reveals_model_field_remove(self, real_mcp):
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread,
            enabled_destructive_tools=["model_fields:remove"],
        )
        assert "model_field_remove" in mcp.names


# ===========================================================================
# register_tools: aliases change what reaches tools/list
# ===========================================================================


class TestRegisterToolsWithAliases:
    def test_action_alias_disables_the_new_tool(self, split_registry):
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, disabled_tools=["card_management:bury"])
        assert "card_bury" not in mcp.names
        assert "card_unbury" in mcp.names

    def test_whole_tool_alias_disables_the_group(self, split_registry):
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, disabled_tools=["card_management"])
        assert not set(_CARD_TOOLS) & mcp.names
        assert "tag_add" in mcp.names

    def test_destructive_action_alias_reveals_the_new_tool(self, split_registry):
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread,
            enabled_destructive_tools=["model_fields:remove"],
        )
        assert "model_field_remove" in mcp.names

    def test_whole_tool_alias_in_allow_list_reveals_nothing(self, split_registry):
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_destructive_tools=["model_fields"],
        )
        assert "model_field_remove" not in mcp.names
        assert "model_field_add" in mcp.names

    def test_disabled_alias_wins_over_opted_in_new_name(self, split_registry):
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread,
            disabled_tools=["model_fields:remove"],
            enabled_destructive_tools=["model_field_remove"],
        )
        assert "model_field_remove" not in mcp.names

    def test_deprecation_printed_once(self, split_registry, capsys):
        for _ in range(2):
            register_tools(
                _MockMCP(), _call_main_thread,
                disabled_tools=["card_management:bury", "card_management:bury"],
            )
        out = capsys.readouterr().out
        assert out.count("'card_management:bury' is deprecated") == 1

    def test_same_alias_reported_per_config_key(self, split_registry, capsys):
        register_tools(
            _MockMCP(), _call_main_thread,
            disabled_tools=["model_fields:remove"],
            enabled_destructive_tools=["model_fields:remove"],
        )
        out = capsys.readouterr().out
        assert out.count("'model_fields:remove' is deprecated") == 2

    def test_aliases_print_no_typo_warning(self, split_registry, capsys):
        register_tools(
            _MockMCP(), _call_main_thread,
            disabled_tools=["card_management:bury", "tag_management"],
        )
        assert "typo" not in capsys.readouterr().out

    def test_typo_still_printed(self, split_registry, capsys):
        register_tools(_MockMCP(), _call_main_thread, disabled_tools=["syncc"])
        out = capsys.readouterr().out
        assert "'syncc'" in out
        assert "typo" in out
