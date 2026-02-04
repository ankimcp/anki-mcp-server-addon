"""E2E tests for write operations (create, update, delete)."""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool


class TestCreateDeck:
    """Tests for deck creation."""

    def test_create_simple_deck(self):
        """create_deck should create a simple deck."""
        deck_name = f"E2EWrite{unique_id()}"
        result = call_tool("create_deck", {"deck_name": deck_name})
        assert "deckId" in result
        assert result["deckId"] > 0
        assert result["created"] is True

    def test_create_nested_deck(self):
        """create_deck should create nested deck (2 levels max)."""
        deck_name = f"E2E::Nested{unique_id()}"
        result = call_tool("create_deck", {"deck_name": deck_name})
        assert "deckId" in result
        assert result["deckId"] > 0

    def test_create_existing_deck_returns_id(self):
        """create_deck on existing deck should return its ID."""
        deck_name = f"E2E::Exist{unique_id()}"
        # Create first
        result1 = call_tool("create_deck", {"deck_name": deck_name})
        deck_id = result1["deckId"]

        # Create again - should return same ID
        result2 = call_tool("create_deck", {"deck_name": deck_name})
        assert result2["deckId"] == deck_id


class TestAddNote:
    """Tests for note creation."""

    def test_add_basic_note(self):
        """addNote should create a note with Basic model."""
        uid = unique_id()
        deck_name = f"E2E::Notes{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Test Question {uid}",
                "Back": f"Test Answer {uid}"
            }
        })
        assert "note_id" in result, f"Expected note_id in result, got: {result}"
        assert result["note_id"] > 0
        assert result["model_name"] == "Basic"

    def test_add_note_with_tags(self):
        """addNote should support tags."""
        uid = unique_id()
        deck_name = f"E2E::Tags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Tagged Question {uid}",
                "Back": f"Tagged Answer {uid}"
            },
            "tags": ["e2e", "test"]
        })
        assert result["note_id"] > 0
        assert result["details"]["tags_added"] == 2

    def test_add_duplicate_note_fails(self):
        """addNote should reject duplicates by default."""
        uid = unique_id()
        deck_name = f"E2E::Dup{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        fields = {
            "Front": f"Unique Question {uid}",
            "Back": "Answer"
        }
        # Create first note
        result1 = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": fields
        })
        assert result1["note_id"] > 0

        # Try to create duplicate
        result2 = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": fields
        })
        assert result2.get("isError") is True

    def test_add_duplicate_with_allow_flag(self):
        """addNote should allow duplicates when allow_duplicate=true."""
        uid = unique_id()
        deck_name = f"E2E::Allow{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        fields = {
            "Front": f"Allowed Duplicate {uid}",
            "Back": "Answer"
        }
        # Create first
        result1 = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": fields
        })
        assert result1["note_id"] > 0

        # Create duplicate with flag
        result2 = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": fields,
            "allow_duplicate": "true"
        })
        assert "note_id" in result2, f"Expected note_id, got: {result2}"
        assert result2["note_id"] > 0

    def test_add_note_missing_fields(self):
        """addNote should fail when required fields are missing."""
        uid = unique_id()
        deck_name = f"E2E::Miss{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Only front {uid}"
                # Missing "Back"
            }
        })
        assert result.get("isError") is True

    def test_add_note_invalid_deck(self):
        """addNote should fail for non-existent deck."""
        result = call_tool("addNote", {
            "deck_name": f"NonExist{unique_id()}",
            "model_name": "Basic",
            "fields": {
                "Front": "Q",
                "Back": "A"
            }
        })
        assert result.get("isError") is True

    def test_add_note_invalid_model(self):
        """addNote should fail for non-existent model."""
        uid = unique_id()
        deck_name = f"E2E::BadMod{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "NonExistentModel12345",
            "fields": {
                "Front": "Q",
                "Back": "A"
            }
        })
        assert result.get("isError") is True


class TestUpdateNote:
    """Tests for note updates."""

    def create_test_note(self) -> int:
        """Create a note and return its ID."""
        uid = unique_id()
        deck_name = f"E2E::Upd{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Original Question {uid}",
                "Back": f"Original Answer {uid}"
            }
        })
        assert "note_id" in result, f"Failed to create test note: {result}"
        return result["note_id"]

    def test_update_single_field(self):
        """updateNoteFields should update a single field."""
        note_id = self.create_test_note()

        result = call_tool("updateNoteFields", {
            "note": {
                "id": note_id,
                "fields": {
                    "Front": f"Updated Question {unique_id()}"
                }
            }
        })
        assert result["noteId"] == note_id
        assert "Front" in result["updatedFields"]
        assert result["fieldCount"] == 1

    def test_update_multiple_fields(self):
        """updateNoteFields should update multiple fields."""
        note_id = self.create_test_note()

        result = call_tool("updateNoteFields", {
            "note": {
                "id": note_id,
                "fields": {
                    "Front": f"New Front {unique_id()}",
                    "Back": f"New Back {unique_id()}"
                }
            }
        })
        assert result["fieldCount"] == 2
        assert set(result["updatedFields"]) == {"Front", "Back"}

    def test_update_verifies_with_notes_info(self):
        """Updated fields should be visible via notesInfo."""
        note_id = self.create_test_note()
        new_content = f"Verified Updated Content {unique_id()}"

        # Update
        call_tool("updateNoteFields", {
            "note": {
                "id": note_id,
                "fields": {"Front": new_content}
            }
        })

        # Verify
        info = call_tool("notesInfo", {"notes": [note_id]})
        assert info["count"] == 1
        note = info["notes"][0]
        assert note["fields"]["Front"]["value"] == new_content

    def test_update_invalid_note_id(self):
        """updateNoteFields should fail for non-existent note."""
        result = call_tool("updateNoteFields", {
            "note": {
                "id": 999999999999,
                "fields": {"Front": "X"}
            }
        })
        assert result.get("isError") is True

    def test_update_invalid_field_name(self):
        """updateNoteFields should fail for invalid field name."""
        note_id = self.create_test_note()

        result = call_tool("updateNoteFields", {
            "note": {
                "id": note_id,
                "fields": {"InvalidFieldName": "X"}
            }
        })
        assert result.get("isError") is True


class TestDeleteNotes:
    """Tests for note deletion."""

    def create_test_note(self, suffix: str = "Del") -> int:
        """Create a note for deletion testing."""
        uid = unique_id()
        deck_name = f"E2E::{suffix}{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"To {suffix} {uid}",
                "Back": f"Answer {uid}"
            }
        })
        assert "note_id" in result, f"Failed to create test note: {result}"
        return result["note_id"]

    def test_delete_single_note(self):
        """deleteNotes should delete a single note."""
        note_id = self.create_test_note("Sgl")

        result = call_tool("deleteNotes", {
            "notes": [note_id],
            "confirmDeletion": True
        })
        assert result["deletedCount"] == 1
        assert note_id in result["deletedNoteIds"]

    def test_delete_multiple_notes(self):
        """deleteNotes should delete multiple notes."""
        note_ids = [self.create_test_note(f"M{i}") for i in range(3)]

        result = call_tool("deleteNotes", {
            "notes": note_ids,
            "confirmDeletion": True
        })
        assert result["deletedCount"] == 3

    def test_delete_requires_confirmation(self):
        """deleteNotes should fail without confirmation."""
        note_id = self.create_test_note("NoCnf")

        result = call_tool("deleteNotes", {
            "notes": [note_id],
            "confirmDeletion": False
        })
        assert result.get("isError") is True

    def test_delete_nonexistent_note(self):
        """deleteNotes should handle non-existent notes gracefully."""
        result = call_tool("deleteNotes", {
            "notes": [999999999999],
            "confirmDeletion": True
        })
        # Should succeed but report 0 deleted
        assert result["deletedCount"] == 0
        assert result["notFoundCount"] == 1

    def test_delete_verifies_note_gone(self):
        """Deleted notes should not appear in notesInfo."""
        note_id = self.create_test_note("Vrf")

        # Verify exists
        info1 = call_tool("notesInfo", {"notes": [note_id]})
        assert info1["count"] == 1

        # Delete
        call_tool("deleteNotes", {
            "notes": [note_id],
            "confirmDeletion": True
        })

        # Verify gone
        info2 = call_tool("notesInfo", {"notes": [note_id]})
        assert info2["count"] == 0
        assert info2["notFound"] == 1


class TestNotesInfo:
    """Tests for notes info retrieval."""

    def create_test_note(self, suffix: str = "Inf") -> int:
        """Create a note and return its ID."""
        uid = unique_id()
        deck_name = f"E2E::{suffix}{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"{suffix} Front {uid}",
                "Back": f"{suffix} Back {uid}"
            },
            "tags": ["info-test"]
        })
        assert "note_id" in result, f"Failed to create test note: {result}"
        return result["note_id"]

    def test_get_note_info(self):
        """notesInfo should return detailed note information."""
        note_id = self.create_test_note("Get")

        result = call_tool("notesInfo", {"notes": [note_id]})
        assert result["count"] == 1

        note = result["notes"][0]
        assert note["noteId"] == note_id
        assert note["modelName"] == "Basic"
        assert "info-test" in note["tags"]
        assert "Front" in note["fields"]
        assert "Back" in note["fields"]

    def test_get_multiple_notes_info(self):
        """notesInfo should handle multiple notes."""
        note_ids = [self.create_test_note(f"Mlt{i}") for i in range(3)]

        info = call_tool("notesInfo", {"notes": note_ids})
        assert info["count"] == 3

    def test_notes_info_mixed_valid_invalid(self):
        """notesInfo should handle mix of valid and invalid IDs."""
        valid_id = self.create_test_note("Mix")

        info = call_tool("notesInfo", {"notes": [valid_id, 999999999999]})
        assert info["count"] == 1
        assert info["notFound"] == 1

    def test_notes_info_empty_list_fails(self):
        """notesInfo should fail with empty list."""
        result = call_tool("notesInfo", {"notes": []})
        assert result.get("isError") is True
