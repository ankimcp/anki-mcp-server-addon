"""Tests for update_notes batch tool."""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool, list_tools

# A note ID that cannot exist (note IDs are epoch-millisecond timestamps)
NONEXISTENT_NOTE_ID = 99999999999999


def _create_notes(deck_name: str, uid: str, count: int) -> list[int]:
    """Create notes via add_notes and return their note IDs."""
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
    assert result["created"] == count, f"Setup failed: {result}"
    return [r["note_id"] for r in result["results"]]


def _delete_notes(note_ids: list[int]) -> None:
    """Delete notes to keep the shared collection clean."""
    if note_ids:
        call_tool("delete_notes", {
            "notes": note_ids,
            "confirmDeletion": True,
        })


def _field_value(note_info: dict, field_name: str) -> str:
    return note_info["fields"][field_name]["value"]


class TestUpdateNotes:
    """Tests for update_notes batch tool."""

    def test_tool_appears_in_tools_list(self):
        """update_notes should be registered and visible in the tools listing."""
        tool_names = [t["name"] for t in list_tools()]
        assert "update_notes" in tool_names

    def test_happy_path_batch_update(self):
        """update_notes should update 3 notes in one batch."""
        uid = unique_id()
        note_ids = _create_notes(f"E2E::BatchUpdate{uid}", uid, 3)

        try:
            entries = [
                {"id": nid, "fields": {"Front": f"New Q{i} {uid}", "Back": f"New A{i} {uid}"}}
                for i, nid in enumerate(note_ids)
            ]
            result = call_tool("update_notes", {"notes": entries})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["updated"] == 3
            assert result["failed"] == 0
            assert result["total_requested"] == 3
            assert len(result["results"]) == 3

            for r in result["results"]:
                assert r["status"] == "updated"
                assert sorted(r["updated_fields"]) == ["Back", "Front"]

            # Verify the new field values actually persisted
            info = call_tool("notes_info", {"notes": note_ids})
            assert info["count"] == 3
            for i, note in enumerate(info["notes"]):
                assert _field_value(note, "Front") == f"New Q{i} {uid}"
                assert _field_value(note, "Back") == f"New A{i} {uid}"
        finally:
            _delete_notes(note_ids)

    def test_partial_update_only_changes_specified_fields(self):
        """Updating only Back should leave Front untouched."""
        uid = unique_id()
        note_ids = _create_notes(f"E2E::PartialUpdate{uid}", uid, 1)

        try:
            result = call_tool("update_notes", {
                "notes": [{"id": note_ids[0], "fields": {"Back": f"Only back {uid}"}}],
            })

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["updated"] == 1
            assert result["results"][0]["updated_fields"] == ["Back"]

            info = call_tool("notes_info", {"notes": note_ids})
            note = info["notes"][0]
            assert _field_value(note, "Front") == f"Orig Q0 {uid}"  # unchanged
            assert _field_value(note, "Back") == f"Only back {uid}"
        finally:
            _delete_notes(note_ids)

    def test_nonexistent_note_reported_per_entry(self):
        """A missing note must fail as not-found without poisoning the batch."""
        uid = unique_id()
        note_ids = _create_notes(f"E2E::UpdateMissing{uid}", uid, 2)

        try:
            entries = [
                {"id": note_ids[0], "fields": {"Front": f"Updated A {uid}"}},
                {"id": NONEXISTENT_NOTE_ID, "fields": {"Front": "ghost"}},
                {"id": note_ids[1], "fields": {"Front": f"Updated B {uid}"}},
            ]
            result = call_tool("update_notes", {"notes": entries})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["updated"] == 2
            assert result["failed"] == 1

            failed = [r for r in result["results"] if r["status"] == "failed"]
            assert len(failed) == 1
            assert failed[0]["index"] == 1
            assert failed[0]["note_id"] == NONEXISTENT_NOTE_ID
            # Must be classified as not-found (permanent), not a transient error
            assert "not found" in failed[0]["error"].lower()
            assert failed[0]["retryable"] is False

            # The valid notes around it were still updated
            info = call_tool("notes_info", {"notes": note_ids})
            assert _field_value(info["notes"][0], "Front") == f"Updated A {uid}"
            assert _field_value(info["notes"][1], "Front") == f"Updated B {uid}"
        finally:
            _delete_notes(note_ids)

    def test_invalid_note_id_reported_per_entry(self):
        """A non-positive note ID must fail per-entry, others still update."""
        uid = unique_id()
        note_ids = _create_notes(f"E2E::UpdateBadId{uid}", uid, 1)

        try:
            entries = [
                {"id": 0, "fields": {"Front": "never applied"}},
                {"id": note_ids[0], "fields": {"Front": f"Still works {uid}"}},
            ]
            result = call_tool("update_notes", {"notes": entries})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["updated"] == 1
            assert result["failed"] == 1

            failed = [r for r in result["results"] if r["status"] == "failed"]
            assert failed[0]["index"] == 0
            assert "invalid note id" in failed[0]["error"].lower()
            assert failed[0]["retryable"] is False

            info = call_tool("notes_info", {"notes": note_ids})
            assert _field_value(info["notes"][0], "Front") == f"Still works {uid}"
        finally:
            _delete_notes(note_ids)

    def test_invalid_field_name_reported_per_entry(self):
        """An unknown field name must fail per-entry with a retry hint."""
        uid = unique_id()
        note_ids = _create_notes(f"E2E::UpdateBadField{uid}", uid, 2)

        try:
            entries = [
                {"id": note_ids[0], "fields": {"Bogus": "no such field"}},
                {"id": note_ids[1], "fields": {"Front": f"Good update {uid}"}},
            ]
            result = call_tool("update_notes", {"notes": entries})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["updated"] == 1
            assert result["failed"] == 1

            failed = [r for r in result["results"] if r["status"] == "failed"]
            assert failed[0]["index"] == 0
            assert "bogus" in failed[0]["error"].lower()
            # Field-name failures are retryable: the hint lists the valid fields
            assert failed[0]["retryable"] is True
            assert "front" in failed[0]["retry_hint"].lower()

            info = call_tool("notes_info", {"notes": note_ids})
            assert _field_value(info["notes"][0], "Front") == f"Orig Q0 {uid}"  # unchanged
            assert _field_value(info["notes"][1], "Front") == f"Good update {uid}"
        finally:
            _delete_notes(note_ids)

    def test_empty_fields_dict_reported_per_entry(self):
        """An entry with an empty fields dict must fail per-entry."""
        uid = unique_id()
        note_ids = _create_notes(f"E2E::UpdateEmptyFields{uid}", uid, 1)

        try:
            entries = [
                {"id": note_ids[0], "fields": {}},
            ]
            result = call_tool("update_notes", {"notes": entries})

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["updated"] == 0
            assert result["failed"] == 1
            assert result["results"][0]["retryable"] is False
            assert "field" in result["results"][0]["error"].lower()
        finally:
            _delete_notes(note_ids)

    def test_empty_notes_array_errors(self):
        """update_notes with an empty notes array should return isError."""
        result = call_tool("update_notes", {"notes": []})
        assert result.get("isError") is True

    def test_exceeds_max_notes_per_batch(self):
        """Exceeding max_notes_per_batch must fail fast with a limit error."""
        uid = unique_id()
        note_ids = _create_notes(f"E2E::UpdateLimit{uid}", uid, 1)

        try:
            # Discover the effective limit from a successful response
            probe = call_tool("update_notes", {
                "notes": [{"id": note_ids[0], "fields": {"Front": f"Probe {uid}"}}],
            })
            assert probe.get("isError") is not True, f"Probe failed: {probe}"
            max_notes = probe["max_notes_per_batch"]
            assert max_notes >= 1

            # Sanity cap: only bail on absurd configurations where building the
            # payload itself would be unreasonable, not on merely large limits
            if max_notes > 100000:
                pytest.skip(f"max_notes_per_batch={max_notes} absurdly large to exercise")

            # One entry over the limit. The tool rejects on len(notes) > max
            # BEFORE dereferencing or validating any entry, so minimal dummy
            # entries suffice — the IDs/fields are never touched.
            entries = [{"id": 1, "fields": {}}] * (max_notes + 1)
            result = call_tool("update_notes", {"notes": entries})

            assert result.get("isError") is True
            assert "too many" in str(result).lower() or "maximum" in str(result).lower()
        finally:
            _delete_notes(note_ids)
