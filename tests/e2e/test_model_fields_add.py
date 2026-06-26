"""Tests for the ``add`` action of the model_fields multi-action tool.

Each test creates a DISPOSABLE, uniquely-named model via create_model so the
shared "Basic" model is never dirtied. Uniquely-named models that no other test
touches are safe to leave behind without cleanup (same rationale as
test_update_model_styling.py / test_update_model_templates_partial_mutation.py).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools

CARD_TEMPLATES = [
    {
        "Name": "Card 1",
        "Front": "{{Front}}",
        "Back": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
    },
]


def _create_model(fields: list[str]) -> str:
    """Create a disposable model with a unique name and the given fields."""
    model_name = f"FieldsAddModel{unique_id()}"
    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": fields,
        "card_templates": CARD_TEMPLATES,
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name


def _field_names(model_name: str) -> list[str]:
    """Return the current ordered field names via model_field_names."""
    result = call_tool("model_field_names", {"model_name": model_name})
    assert result.get("isError") is not True, f"model_field_names failed: {result}"
    return result["field_names"]


class TestModelFieldsAdd:
    """Tests for the add action in the model_fields tool."""

    def test_tool_appears_in_tools_list(self):
        """The model_fields tool should be registered and visible."""
        tool_names = [t["name"] for t in list_tools()]
        assert "model_fields" in tool_names

    def test_append_field_at_end(self):
        """Without an index the new field is appended at the end."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Hint",
            }
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["fields"] == ["Front", "Back", "Hint"]
        # Schema change must surface the full-sync warning.
        assert "warning" in result
        assert "full sync" in result["warning"].lower()

        # Read back: the persisted order must match.
        assert _field_names(model_name) == ["Front", "Back", "Hint"]

    def test_add_at_specific_index(self):
        """A 0-based index inserts the field at that position."""
        model_name = _create_model(["Front", "Back", "Extra"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Hint",
                "index": 1,
            }
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["fields"] == ["Front", "Hint", "Back", "Extra"]

        # Read back: the persisted order must match.
        assert _field_names(model_name) == ["Front", "Hint", "Back", "Extra"]

    def test_append_at_index_equal_to_field_count(self):
        """An index equal to the current field count appends at the end."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Hint",
                "index": 2,
            }
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["fields"] == ["Front", "Back", "Hint"]

        # Read back: the explicitly-indexed append must land at the end.
        assert _field_names(model_name) == ["Front", "Back", "Hint"]

    def test_reject_duplicate_name(self):
        """Adding a field whose name already exists is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Front",
            }
        })

        assert result.get("isError") is True
        assert "already exists" in str(result).lower()

    def test_reject_case_variant_collision(self):
        """Adding a name that collides case-insensitively is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "front",
            }
        })

        assert result.get("isError") is True
        assert "case" in str(result).lower()

    def test_reject_out_of_range_index(self):
        """An index beyond field_count is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Hint",
                "index": 99,
            }
        })

        assert result.get("isError") is True
        assert "out of range" in str(result).lower()

    def test_reject_negative_index(self):
        """A negative index is rejected (lower bound of the range guard)."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                "field_name": "Hint",
                "index": -1,
            }
        })

        assert result.get("isError") is True
        assert "out of range" in str(result).lower()

    def test_reject_empty_name(self):
        """A blank (whitespace-only) field name is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "add",
                "model_name": model_name,
                # Whitespace-only proves the server-side .strip()-then-reject
                # path is exercised (a stronger assertion than a bare-empty value).
                "field_name": " ",
            }
        })

        assert result.get("isError") is True
        assert "empty" in str(result).lower()
