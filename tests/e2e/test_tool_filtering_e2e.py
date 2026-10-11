"""E2E tests for tool filtering (disabled_tools + enabled_destructive_tools).

These tests run against a filtered container (port 3142) with the following
disabled_tools config:
    - "sync"                 (tool disabled by its name)
    - "card_management:bury" (deprecated alias for card_bury -- exercises the
                              TOOL_ALIASES path for the retired multi-action
                              tool names)
    - "card_unbury"          (tool disabled by its name)

The filtered config ALSO opts a destructive tool in via:
    enabled_destructive_tools: ["model_field_remove"]
so the otherwise-hidden ``model_field_remove`` tool is revealed here (the
REVEAL half of the destructive hide/reveal coverage; the HIDE half lives in
test_destructive_tools_e2e.py against the default server).

That list does not name ``change_note_type``, so this suite is also the HIDE
half for that destructive tool: it must be absent from tools/list here (it is
opted in on the default server instead -- see test_change_note_type.py).

Run with:
    MCP_SERVER_URL=http://localhost:3142 pytest tests/e2e/test_tool_filtering_e2e.py -v
"""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool, list_tools


# card_* tools that should remain after filtering card_bury + card_unbury out
_EXPECTED_ENABLED_CARD_TOOLS = {
    "card_reposition",
    "card_change_deck",
    "card_suspend",
    "card_unsuspend",
    "card_set_flag",
    "card_set_due_date",
    "card_forget",
}


def _tool_names() -> set[str]:
    return {t["name"] for t in list_tools()}


def _create_card(deck_prefix: str) -> int:
    uid = unique_id()
    deck_name = f"E2E::{deck_prefix}{uid}"
    call_tool("create_deck", {"deck_name": deck_name})
    note_result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {
            "Front": f"Question {uid}",
            "Back": f"Answer {uid}",
        }
    })
    notes_info = call_tool("notes_info", {"notes": [note_result["note_id"]]})
    return notes_info["notes"][0]["cards"][0]


class TestDisabledTools:
    """Tools disabled by name and through a deprecated alias."""

    def test_disabled_tool_not_in_list(self):
        """sync should NOT appear in tools/list when disabled."""
        assert "sync" not in _tool_names()

    def test_alias_disabled_tool_not_in_list(self):
        """'card_management:bury' resolves to card_bury, which is hidden."""
        assert "card_bury" not in _tool_names()

    def test_new_name_disabled_tool_not_in_list(self):
        assert "card_unbury" not in _tool_names()

    def test_retired_tool_name_not_in_list(self):
        assert "card_management" not in _tool_names()

    def test_other_card_tools_still_present(self):
        names = _tool_names()
        missing = _EXPECTED_ENABLED_CARD_TOOLS - names
        assert not missing, f"Expected card tools missing: {sorted(missing)}"

    def test_enabled_tools_still_present(self):
        """Core tools that are NOT disabled should still appear."""
        names = _tool_names()
        for name in ("find_notes", "add_note", "list_decks"):
            assert name in names

    def test_disabled_tool_rejected(self):
        """Calling the hidden card_bury fails: it is not a listed tool."""
        card_id = _create_card("FilterBury")
        with pytest.raises(RuntimeError, match="tool_not_found"):
            call_tool("card_bury", {"card_ids": [card_id]})

    def test_enabled_tool_still_works(self):
        """card_suspend is untouched by the filter and still works."""
        card_id = _create_card("FilterSuspend")
        result = call_tool("card_suspend", {"card_ids": [card_id]})

        assert result.get("isError") is not True
        assert result["suspended_count"] == 1


class TestEnabledDestructiveTool:
    """REVEAL half: opting in via enabled_destructive_tools exposes the tool.

    The filtered config sets enabled_destructive_tools: ["model_field_remove"],
    so model_field_remove -- hidden by default -- is listed here.
    """

    def test_destructive_tool_revealed(self):
        names = _tool_names()
        assert "model_field_remove" in names, (
            "model_field_remove should be revealed via enabled_destructive_tools"
        )
        for name in ("model_field_add", "model_field_rename", "model_field_reposition"):
            assert name in names


class TestWholeToolDestructiveNotOptedIn:
    """HIDE half for a destructive tool: change_note_type.

    ``change_note_type`` is declared ``@Tool(..., destructive=True)``, so it is
    withheld from tools/list unless the operator names it in
    enabled_destructive_tools. The filtered config opts in only
    ``model_field_remove``, so change_note_type must be absent here.

    The REVEAL half runs in the default suite (port 3141), whose config does opt
    it in; see test_change_note_type.py.
    """

    def test_change_note_type_hidden(self):
        """The tool is absent because this config does not opt it in."""
        tool_names = _tool_names()
        assert "change_note_type" not in tool_names, (
            "change_note_type is destructive and must stay hidden unless "
            "enabled_destructive_tools lists it. Found: "
            f"{sorted(tool_names)}"
        )
