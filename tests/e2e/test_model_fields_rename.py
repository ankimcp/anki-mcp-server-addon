"""Tests for the ``rename`` action of the model_fields multi-action tool.

Each test creates a DISPOSABLE, uniquely-named model (and, where a note is
needed, a uniquely-named deck) so shared collection state is never dirtied.
Uniquely-named artifacts that no other test touches are safe to leave behind
without cleanup (same rationale as test_update_model_styling.py).
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
    model_name = f"FieldsRenameModel{unique_id()}"
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


class TestModelFieldsRename:
    """Tests for the rename action in the model_fields tool."""

    def test_rename_preserves_content_and_order(self):
        """Renaming keeps the field's content and overall order intact."""
        uid = unique_id()
        model_name = _create_model(["Front", "Back", "Extra"])
        deck_name = f"E2E::FieldsRename{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": model_name,
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
                "Extra": f"Extra content {uid}",
            },
        })
        assert note_result.get("isError") is not True, f"add_note failed: {note_result}"
        note_id = note_result["note_id"]

        result = call_tool("model_fields", {
            "params": {
                "action": "rename",
                "model_name": model_name,
                "field_name": "Extra",
                "new_name": "Notes",
            }
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["old_field_name"] == "Extra"
        assert result["new_field_name"] == "Notes"
        assert result["fields"] == ["Front", "Back", "Notes"]
        # The rename warning must mention that template references are NOT updated.
        assert "warning" in result
        assert "template" in result["warning"].lower()

        # Order is preserved with the renamed field in place.
        assert _field_names(model_name) == ["Front", "Back", "Notes"]

        # Content is preserved under the new field name.
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        fields = notes_info["notes"][0]["fields"]
        assert "Notes" in fields
        assert "Extra" not in fields
        assert fields["Notes"]["value"] == f"Extra content {uid}"

    def test_reject_missing_source(self):
        """Renaming a field that does not exist is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "rename",
                "model_name": model_name,
                "field_name": "Nope",
                "new_name": "Whatever",
            }
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

    def test_reject_collision_with_different_field(self):
        """Renaming onto a DIFFERENT existing field name is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "rename",
                "model_name": model_name,
                "field_name": "Back",
                "new_name": "Front",
            }
        })

        assert result.get("isError") is True
        assert "collides" in str(result).lower()

    def test_allow_pure_case_change(self):
        """A pure case-change of the SAME field is allowed."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "rename",
                "model_name": model_name,
                "field_name": "Front",
                "new_name": "front",
            }
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["new_field_name"] == "front"
        assert _field_names(model_name) == ["front", "Back"]

    def test_reject_empty_new_name(self):
        """A blank (whitespace-only) new name is rejected."""
        model_name = _create_model(["Front", "Back"])

        result = call_tool("model_fields", {
            "params": {
                "action": "rename",
                "model_name": model_name,
                "field_name": "Front",
                # Whitespace-only proves the server-side .strip()-then-reject
                # path is exercised (a stronger assertion than a bare-empty value).
                "new_name": " ",
            }
        })

        assert result.get("isError") is True
        assert "empty" in str(result).lower()
