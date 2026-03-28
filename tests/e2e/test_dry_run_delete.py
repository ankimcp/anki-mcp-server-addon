"""E2E tests for delete_notes dry_run parameter."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestDryRunDelete:
    """Tests for the dry_run parameter on delete_notes."""

    def _create_test_note(self, suffix: str = "DryRun") -> int:
        """Create a note and return its ID."""
        uid = unique_id()
        deck_name = f"E2E::{suffix}{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"DryRun Front {uid}",
                "Back": f"DryRun Back {uid}",
            },
        })
        assert "note_id" in result, f"Failed to create test note: {result}"
        return result["note_id"]

    def test_dry_run_returns_preview_without_deleting(self):
        """dry_run=true should return deletion preview but NOT actually delete notes."""
        note_id = self._create_test_note("Preview")

        # Dry run delete
        result = call_tool("delete_notes", {
            "notes": [note_id],
            "confirmDeletion": True,
            "dry_run": True,
        })
        assert result["dry_run"] is True
        assert result["deletedCount"] == 1
        assert result["cardsDeleted"] >= 1
        assert note_id in result["deletedNoteIds"]
        assert "Dry run" in result["message"]

        # Verify note still exists
        info = call_tool("notes_info", {"notes": [note_id]})
        assert info["count"] == 1, "Note should still exist after dry_run"

    def test_dry_run_ignores_confirm_deletion(self):
        """dry_run=true should work even when confirmDeletion=false."""
        note_id = self._create_test_note("NoCnfDry")

        # Dry run with confirmDeletion=false -- should NOT error
        result = call_tool("delete_notes", {
            "notes": [note_id],
            "confirmDeletion": False,
            "dry_run": True,
        })
        assert result["dry_run"] is True
        assert result["deletedCount"] == 1

        # Verify note still exists
        info = call_tool("notes_info", {"notes": [note_id]})
        assert info["count"] == 1, "Note should still exist after dry_run"

    def test_dry_run_shows_not_found_count(self):
        """dry_run should report how many IDs were not found."""
        note_id = self._create_test_note("MixDry")
        fake_id = 999999999999

        result = call_tool("delete_notes", {
            "notes": [note_id, fake_id],
            "confirmDeletion": True,
            "dry_run": True,
        })
        assert result["dry_run"] is True
        assert result["deletedCount"] == 1
        assert result["notFoundCount"] == 1
        assert note_id in result["deletedNoteIds"]

        # Verify the real note still exists
        info = call_tool("notes_info", {"notes": [note_id]})
        assert info["count"] == 1, "Note should still exist after dry_run"
