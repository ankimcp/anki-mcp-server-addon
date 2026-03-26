"""Tests for notes_info field filtering (include_fields / exclude_fields)."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


def _create_basic_note(uid: str) -> int:
    """Create a Basic note and return its note ID."""
    deck_name = f"E2E::FieldFilter{uid}"
    call_tool("create_deck", {"deck_name": deck_name})
    result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {
            "Front": f"FF Front {uid}",
            "Back": f"FF Back {uid}",
        },
    })
    assert "note_id" in result, f"Failed to create test note: {result}"
    return result["note_id"]


class TestNotesInfoFieldFiltering:
    """Tests for include_fields and exclude_fields parameters on notes_info."""

    def test_no_filtering_returns_all_fields(self):
        """Without include_fields or exclude_fields, all fields are returned."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {"notes": [note_id]})

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        assert "Front" in fields
        assert "Back" in fields

    def test_include_fields_returns_only_specified(self):
        """include_fields should return only the requested fields."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "include_fields": ["Front"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        assert "Front" in fields
        assert "Back" not in fields
        # Verify field structure is intact
        assert "value" in fields["Front"]
        assert "order" in fields["Front"]

    def test_exclude_fields_removes_specified(self):
        """exclude_fields should return all fields except the excluded ones."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "exclude_fields": ["Back"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        assert "Front" in fields
        assert "Back" not in fields

    def test_include_fields_takes_priority_over_exclude(self):
        """When both include_fields and exclude_fields are provided, include wins."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "include_fields": ["Front"],
            "exclude_fields": ["Front"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        # include_fields wins: Front should be present even though exclude_fields lists it
        assert "Front" in fields
        assert "Back" not in fields

    def test_nonexistent_include_field_silently_ignored(self):
        """include_fields with a non-existent field name returns empty fields dict."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "include_fields": ["NonExistentField"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        assert len(fields) == 0

    def test_nonexistent_exclude_field_silently_ignored(self):
        """exclude_fields with a non-existent field name has no effect."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "exclude_fields": ["NonExistentField"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        # Both real fields should still be present
        assert "Front" in fields
        assert "Back" in fields

    def test_metadata_always_returned_regardless_of_filtering(self):
        """Note metadata (noteId, modelName, tags, cards, mod) is never filtered."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "include_fields": ["Front"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        note = result["notes"][0]
        assert "noteId" in note
        assert "modelName" in note
        assert "tags" in note
        assert "cards" in note
        assert "mod" in note

    def test_include_fields_with_mix_of_real_and_fake(self):
        """include_fields with a mix of real and non-existent fields returns only real ones."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "include_fields": ["Front", "Bogus", "AlsoFake"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        assert "Front" in fields
        assert "Back" not in fields
        assert "Bogus" not in fields
        assert "AlsoFake" not in fields

    def test_filtering_applies_to_all_notes_in_batch(self):
        """Field filtering works consistently across multiple notes."""
        uid = unique_id()
        nid1 = _create_basic_note(uid + "a")
        nid2 = _create_basic_note(uid + "b")

        result = call_tool("notes_info", {
            "notes": [nid1, nid2],
            "include_fields": ["Front"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 2
        for note in result["notes"]:
            fields = note["fields"]
            assert "Front" in fields
            assert "Back" not in fields

    def test_exclude_all_fields_returns_empty_dict(self):
        """Excluding all fields leaves an empty fields dict."""
        uid = unique_id()
        note_id = _create_basic_note(uid)

        result = call_tool("notes_info", {
            "notes": [note_id],
            "exclude_fields": ["Front", "Back"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        fields = result["notes"][0]["fields"]
        assert len(fields) == 0
