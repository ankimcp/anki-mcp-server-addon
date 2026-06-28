"""E2E tests for update_notes dry_run parameter."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool

NONEXISTENT_NOTE_ID = 99999999999999


class TestUpdateNotesDryRun:
    """Tests for the dry_run parameter on update_notes."""

    def _create_notes(self, deck_suffix: str, count: int) -> list[int]:
        """Create Basic notes and return their IDs."""
        uid = unique_id()
        deck_name = f"E2E::DryRunUpdate{deck_suffix}{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        notes = [
            {"fields": {"Front": f"Orig Q{i} {uid}", "Back": f"Orig A{i} {uid}"}}
            for i in range(count)
        ]
        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
        })
        assert result.get("isError") is not True, f"Setup failed: {result}"
        assert result["created"] == count
        return [r["note_id"] for r in result["results"]]

    def _delete_notes(self, note_ids: list[int]) -> None:
        if note_ids:
            call_tool("delete_notes", {"notes": note_ids, "confirmDeletion": True})

    def _field_value(self, note_info: dict, field_name: str) -> str:
        return note_info["fields"][field_name]["value"]

    # --- dry_run=True: no writes ---

    def test_dry_run_returns_preview_without_writing(self):
        """dry_run=true should return a preview but NOT persist any field changes."""
        note_ids = self._create_notes("Preview", 2)

        try:
            entries = [
                {"id": note_ids[0], "fields": {"Front": "SHOULD NOT APPEAR"}},
                {"id": note_ids[1], "fields": {"Front": "ALSO NOT APPEAR"}},
            ]
            result = call_tool("update_notes", {"notes": entries, "dry_run": True})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["dry_run"] is True
            assert result["would_update"] == 2
            assert result["would_fail"] == 0
            assert result["total_requested"] == 2
            assert len(result["results"]) == 2
            for r in result["results"]:
                assert r["status"] == "would_update"
                assert "Front" in r["updated_fields"]

            # Verify original field values are completely unchanged
            info = call_tool("notes_info", {"notes": note_ids})
            assert info["count"] == 2
            for i, note in enumerate(info["notes"]):
                assert "SHOULD NOT APPEAR" not in self._field_value(note, "Front")
                assert "ALSO NOT APPEAR" not in self._field_value(note, "Front")
        finally:
            self._delete_notes(note_ids)

    def test_dry_run_validates_field_names(self):
        """dry_run=true must still validate field names and report failures."""
        note_ids = self._create_notes("ValidateFields", 2)

        try:
            entries = [
                {"id": note_ids[0], "fields": {"Bogus": "invalid field"}},
                {"id": note_ids[1], "fields": {"Front": "valid field"}},
            ]
            result = call_tool("update_notes", {"notes": entries, "dry_run": True})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["dry_run"] is True
            assert result["would_update"] == 1
            assert result["would_fail"] == 1

            failed = [r for r in result["results"] if r["status"] == "failed"]
            assert len(failed) == 1
            assert failed[0]["note_id"] == note_ids[0]
            assert "bogus" in failed[0]["error"].lower()

            # Neither note should be changed
            info = call_tool("notes_info", {"notes": note_ids})
            assert "valid field" not in self._field_value(info["notes"][1], "Front")
        finally:
            self._delete_notes(note_ids)

    def test_dry_run_reports_nonexistent_notes(self):
        """dry_run=true should report missing note IDs as failures."""
        note_ids = self._create_notes("MissingNote", 1)

        try:
            entries = [
                {"id": note_ids[0], "fields": {"Front": "new value"}},
                {"id": NONEXISTENT_NOTE_ID, "fields": {"Front": "ghost"}},
            ]
            result = call_tool("update_notes", {"notes": entries, "dry_run": True})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["dry_run"] is True
            assert result["would_update"] == 1
            assert result["would_fail"] == 1

            failed = [r for r in result["results"] if r["status"] == "failed"]
            assert failed[0]["note_id"] == NONEXISTENT_NOTE_ID
            assert "not found" in failed[0]["error"].lower()

            # The valid note is still untouched
            info = call_tool("notes_info", {"notes": note_ids})
            assert "new value" not in self._field_value(info["notes"][0], "Front")
        finally:
            self._delete_notes(note_ids)

    def test_dry_run_response_includes_hint_to_confirm(self):
        """dry_run response must tell the caller how to proceed with the real update."""
        note_ids = self._create_notes("HintCheck", 1)

        try:
            result = call_tool("update_notes", {
                "notes": [{"id": note_ids[0], "fields": {"Front": "pending"}}],
                "dry_run": True,
            })

            assert result.get("isError") is not True
            assert result["dry_run"] is True
            assert "dry_run=false" in result.get("hint", "").lower() or \
                   "dry_run" in result.get("hint", "").lower()
        finally:
            self._delete_notes(note_ids)

    # --- dry_run=False (default): writes proceed normally ---

    def test_default_dry_run_false_still_writes(self):
        """Omitting dry_run (or passing false) must still persist changes."""
        note_ids = self._create_notes("RealWrite", 1)

        try:
            result = call_tool("update_notes", {
                "notes": [{"id": note_ids[0], "fields": {"Front": "written value"}}],
            })

            assert result.get("isError") is not True
            assert result.get("dry_run") is False
            assert result["updated"] == 1

            info = call_tool("notes_info", {"notes": note_ids})
            assert self._field_value(info["notes"][0], "Front") == "written value"
        finally:
            self._delete_notes(note_ids)

    def test_dry_run_false_explicit_writes(self):
        """Explicitly passing dry_run=false must persist changes."""
        note_ids = self._create_notes("ExplicitFalse", 1)

        try:
            result = call_tool("update_notes", {
                "notes": [{"id": note_ids[0], "fields": {"Back": "explicit false"}}],
                "dry_run": False,
            })

            assert result.get("isError") is not True
            assert result.get("dry_run") is False
            assert result["updated"] == 1

            info = call_tool("notes_info", {"notes": note_ids})
            assert self._field_value(info["notes"][0], "Back") == "explicit false"
        finally:
            self._delete_notes(note_ids)
