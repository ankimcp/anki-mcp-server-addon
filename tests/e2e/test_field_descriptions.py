"""E2E tests for field description support in model and note tools."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestModelFieldNamesDescriptions:
    """Tests for field descriptions returned by modelFieldNames."""

    def test_model_field_names_returns_fields_with_descriptions(self):
        """modelFieldNames should return both field_names and fields with descriptions."""
        result = call_tool("modelFieldNames", {"model_name": "Basic"})

        assert "field_names" in result, f"Expected field_names in result, got: {result}"
        assert "fields" in result, f"Expected fields in result, got: {result}"

        # field_names is backward-compatible flat list of strings
        assert isinstance(result["field_names"], list)
        assert len(result["field_names"]) > 0

        # fields is a list of objects with name and description
        assert isinstance(result["fields"], list)
        assert len(result["fields"]) == len(result["field_names"])

        for field_obj in result["fields"]:
            assert "name" in field_obj, f"Expected name in field object, got: {field_obj}"
            assert "description" in field_obj, f"Expected description in field object, got: {field_obj}"

    def test_model_field_names_field_descriptions_are_strings(self):
        """Field descriptions should be strings (even when empty)."""
        result = call_tool("modelFieldNames", {"model_name": "Basic"})

        assert "fields" in result
        for field_obj in result["fields"]:
            assert isinstance(field_obj["description"], str), (
                f"Expected description to be str, got {type(field_obj['description'])!r} "
                f"for field {field_obj.get('name')!r}"
            )

    def test_model_field_names_field_names_match_fields(self):
        """field_names list should match names in fields list (same order)."""
        result = call_tool("modelFieldNames", {"model_name": "Basic"})

        assert "field_names" in result
        assert "fields" in result

        names_from_flat = result["field_names"]
        names_from_objects = [f["name"] for f in result["fields"]]
        assert names_from_flat == names_from_objects

    def test_basic_model_descriptions_default_to_empty_string(self):
        """Built-in Basic model has no custom descriptions — they should all be empty strings."""
        result = call_tool("modelFieldNames", {"model_name": "Basic"})

        assert "fields" in result
        for field_obj in result["fields"]:
            assert field_obj["description"] == "", (
                f"Expected empty description for Basic model field {field_obj.get('name')!r}, "
                f"got {field_obj['description']!r}"
            )


class TestNotesInfoFieldDescriptions:
    """Tests for field descriptions returned by notesInfo."""

    def _create_test_note(self, suffix: str = "Desc") -> int:
        """Create a Basic note and return its ID."""
        uid = unique_id()
        deck_name = f"E2E::{suffix}{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"{suffix} Front {uid}",
                "Back": f"{suffix} Back {uid}",
            },
        })
        assert "note_id" in result, f"Failed to create test note: {result}"
        return result["note_id"]

    def test_notes_info_includes_field_descriptions(self):
        """notesInfo should include description in each field entry."""
        note_id = self._create_test_note("FldDesc")

        result = call_tool("notesInfo", {"notes": [note_id]})
        assert result["count"] == 1

        note = result["notes"][0]
        assert "fields" in note

        for field_name, field_data in note["fields"].items():
            assert "value" in field_data, f"Missing value for field {field_name!r}"
            assert "order" in field_data, f"Missing order for field {field_name!r}"
            assert "description" in field_data, (
                f"Missing description for field {field_name!r}, got keys: {list(field_data.keys())}"
            )

    def test_notes_info_field_descriptions_are_strings(self):
        """notesInfo field descriptions should be strings."""
        note_id = self._create_test_note("FldStr")

        result = call_tool("notesInfo", {"notes": [note_id]})
        assert result["count"] == 1

        note = result["notes"][0]
        for field_name, field_data in note["fields"].items():
            assert isinstance(field_data["description"], str), (
                f"Expected str description for field {field_name!r}, "
                f"got {type(field_data['description'])!r}"
            )

    def test_notes_info_basic_model_descriptions_default_to_empty(self):
        """Basic model field descriptions should default to empty string in notesInfo."""
        note_id = self._create_test_note("FldEmpty")

        result = call_tool("notesInfo", {"notes": [note_id]})
        assert result["count"] == 1

        note = result["notes"][0]
        assert note["modelName"] == "Basic"

        for field_name, field_data in note["fields"].items():
            assert field_data["description"] == "", (
                f"Expected empty description for Basic model field {field_name!r}, "
                f"got {field_data['description']!r}"
            )
