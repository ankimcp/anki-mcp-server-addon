"""Tests for the ``reposition`` action of the model_fields multi-action tool.

Each test creates a DISPOSABLE, uniquely-named model via create_model so the
shared "Basic" model is never dirtied. Uniquely-named models that no other test
touches are safe to leave behind without cleanup (same rationale as
test_update_model_styling.py / test_update_model_templates_partial_mutation.py).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool

CARD_TEMPLATES = [
    {
        "Name": "Card 1",
        "Front": "{{Front}}",
        "Back": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
    },
]


def _create_model(fields: list[str]) -> str:
    """Create a disposable model with a unique name and the given fields."""
    model_name = f"FieldsRepositionModel{unique_id()}"
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


class TestModelFieldsReposition:
    """Tests for the reposition action in the model_fields tool."""

    def test_move_field_to_new_index(self):
        """Repositioning reorders the fields and persists the new order."""
        model_name = _create_model(["Front", "Back", "Extra"])

        result = call_tool("model_fields", {
            "params": {
                "action": "reposition",
                "model_name": model_name,
                "field_name": "Extra",
                "index": 0,
            }
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["fields"] == ["Extra", "Front", "Back"]
        # Schema change must surface the full-sync warning.
        assert "warning" in result
        assert "full sync" in result["warning"].lower()

        # Read back: the persisted order must match.
        assert _field_names(model_name) == ["Extra", "Front", "Back"]

    def test_reject_out_of_range_index(self):
        """An index beyond the last valid position is rejected."""
        model_name = _create_model(["Front", "Back", "Extra"])

        result = call_tool("model_fields", {
            "params": {
                "action": "reposition",
                "model_name": model_name,
                "field_name": "Front",
                "index": 99,
            }
        })

        assert result.get("isError") is True
        assert "out of range" in str(result).lower()

    def test_reject_negative_index(self):
        """A negative index is rejected (lower bound of the range guard)."""
        model_name = _create_model(["Front", "Back", "Extra"])

        result = call_tool("model_fields", {
            "params": {
                "action": "reposition",
                "model_name": model_name,
                "field_name": "Front",
                "index": -1,
            }
        })

        assert result.get("isError") is True
        assert "out of range" in str(result).lower()
