"""E2E tests for tool filtering (disabled_tools config).

These tests run against a filtered container (port 3142) with the following
disabled_tools config:
    - "sync"                   (whole tool disabled)
    - "card_management:bury"   (single action disabled)
    - "card_management:unbury" (single action disabled)

Run with:
    MCP_SERVER_URL=http://localhost:3142 pytest tests/e2e/test_tool_filtering_e2e.py -v
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


# Actions that should remain after filtering bury + unbury out
_EXPECTED_ENABLED_ACTIONS = {
    "reposition",
    "change_deck",
    "suspend",
    "unsuspend",
    "set_flag",
    "set_due_date",
    "forget_cards",
}


def _get_tool_by_name(tools: list[dict], name: str) -> dict | None:
    """Find a tool dict by name from list_tools() output."""
    for t in tools:
        if t["name"] == name:
            return t
    return None


def _get_schema_action_names(tool: dict) -> set[str]:
    """Extract action names from a multi-action tool's inputSchema.

    Looks in the JSON Schema for discriminated union variants. The schema
    uses ``oneOf`` with each variant containing an ``action`` property
    whose ``const`` or ``enum`` value identifies the action.
    """
    schema = tool.get("inputSchema", {})
    action_names: set[str] = set()

    # Navigate into the params property which holds the discriminated union
    params_schema = (
        schema.get("properties", {})
        .get("params", {})
    )

    # Pydantic may produce oneOf or anyOf depending on version
    variants = params_schema.get("oneOf", []) or params_schema.get("anyOf", [])
    for variant in variants:
        action_prop = variant.get("properties", {}).get("action", {})
        # Pydantic uses "const" for Literal with single value
        if "const" in action_prop:
            action_names.add(action_prop["const"])
        # Or "enum" with a single element
        elif "enum" in action_prop:
            for v in action_prop["enum"]:
                action_names.add(v)

    # Some Pydantic versions use $defs + $ref -- fallback: check the
    # discriminator mapping if present
    if not action_names:
        discriminator = params_schema.get("discriminator", {})
        mapping = discriminator.get("mapping", {})
        action_names = set(mapping.keys())

    return action_names


class TestDisabledWholeTool:
    """Tests for a completely disabled tool (sync)."""

    def test_disabled_tool_not_in_list(self):
        """sync tool should NOT appear in tools/list when disabled."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "sync" not in tool_names

    def test_enabled_tools_still_present(self):
        """Core tools that are NOT disabled should still appear."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "card_management" in tool_names
        assert "find_notes" in tool_names
        assert "add_note" in tool_names
        assert "list_decks" in tool_names


class TestDisabledActions:
    """Tests for per-action filtering (card_management:bury, card_management:unbury)."""

    def test_disabled_action_not_in_schema(self):
        """bury and unbury should NOT appear in card_management's inputSchema."""
        tools = list_tools()
        cm_tool = _get_tool_by_name(tools, "card_management")
        assert cm_tool is not None, "card_management tool should exist"

        schema_actions = _get_schema_action_names(cm_tool)
        assert len(schema_actions) > 0, "Could not extract action names from schema"
        assert "bury" not in schema_actions, (
            f"bury should be filtered out, but found in schema actions: {schema_actions}"
        )
        assert "unbury" not in schema_actions, (
            f"unbury should be filtered out, but found in schema actions: {schema_actions}"
        )

    def test_enabled_actions_still_in_schema(self):
        """The 7 non-disabled actions should still be present in the schema."""
        tools = list_tools()
        cm_tool = _get_tool_by_name(tools, "card_management")
        assert cm_tool is not None

        schema_actions = _get_schema_action_names(cm_tool)
        for action in _EXPECTED_ENABLED_ACTIONS:
            assert action in schema_actions, (
                f"Expected action '{action}' missing from schema. "
                f"Found: {schema_actions}"
            )

    def test_disabled_action_rejected(self):
        """Calling card_management with action=bury should fail."""
        uid = unique_id()
        deck_name = f"E2E::FilterBury{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            }
        })
        note_id = note_result["note_id"]
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        card_id = notes_info["notes"][0]["cards"][0]

        result = call_tool("card_management", {
            "params": {
                "action": "bury",
                "card_ids": [card_id],
            }
        })

        # Should error because bury is no longer a valid action in the schema
        assert result.get("isError") is True
        # Verify the error is about the invalid action, not some unrelated failure
        error_text = str(result)
        assert "bury" in error_text, (
            f"Expected error to mention 'bury', got: {error_text}"
        )

    def test_enabled_action_still_works(self):
        """Calling card_management with action=suspend should succeed."""
        uid = unique_id()
        deck_name = f"E2E::FilterSuspend{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            }
        })
        note_id = note_result["note_id"]
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        card_id = notes_info["notes"][0]["cards"][0]

        result = call_tool("card_management", {
            "params": {
                "action": "suspend",
                "card_ids": [card_id],
            }
        })

        assert result.get("isError") is not True
        assert "suspended_count" in result
        assert result["suspended_count"] == 1
