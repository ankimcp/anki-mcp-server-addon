"""Tests for add_notes batch tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


class TestAddNotes:
    """Tests for add_notes batch tool."""

    def test_happy_path_add_three_notes(self):
        """add_notes should create 3 valid notes and return note_ids."""
        uid = unique_id()
        deck_name = f"E2E::BatchAdd{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        notes = [
            {"fields": {"Front": f"Q1 {uid}", "Back": f"A1 {uid}"}},
            {"fields": {"Front": f"Q2 {uid}", "Back": f"A2 {uid}"}},
            {"fields": {"Front": f"Q3 {uid}", "Back": f"A3 {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["created"] == 3
        assert result["skipped"] == 0
        assert result["failed"] == 0
        assert result["total_requested"] == 3
        assert result["deck_name"] == deck_name
        assert result["model_name"] == "Basic"
        assert len(result["results"]) == 3

        # Each result should have status=created and a valid note_id
        for r in result["results"]:
            assert r["status"] == "created"
            assert r["note_id"] > 0

        # Verify notes actually exist via notes_info
        note_ids = [r["note_id"] for r in result["results"]]
        info = call_tool("notes_info", {"notes": note_ids})
        assert info["count"] == 3

        # Cleanup
        call_tool("delete_notes", {
            "notes": note_ids,
            "confirmDeletion": True,
        })

    def test_shared_and_per_note_tags(self):
        """Shared tags should apply to all notes, per-note tags should merge."""
        uid = unique_id()
        deck_name = f"E2E::BatchTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        notes = [
            {
                "fields": {"Front": f"TagQ1 {uid}", "Back": f"TagA1 {uid}"},
                "tags": ["per-note-1"],
            },
            {
                "fields": {"Front": f"TagQ2 {uid}", "Back": f"TagA2 {uid}"},
                "tags": ["per-note-2"],
            },
            {
                "fields": {"Front": f"TagQ3 {uid}", "Back": f"TagA3 {uid}"},
                # No per-note tags -- should still get shared tags
            },
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
            "tags": ["shared-a", "shared-b"],
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["created"] == 3

        note_ids = [r["note_id"] for r in result["results"]]
        info = call_tool("notes_info", {"notes": note_ids})

        # Note 0: shared-a, shared-b, per-note-1
        tags_0 = info["notes"][0]["tags"]
        assert "shared-a" in tags_0
        assert "shared-b" in tags_0
        assert "per-note-1" in tags_0

        # Note 1: shared-a, shared-b, per-note-2
        tags_1 = info["notes"][1]["tags"]
        assert "shared-a" in tags_1
        assert "shared-b" in tags_1
        assert "per-note-2" in tags_1

        # Note 2: shared-a, shared-b only
        tags_2 = info["notes"][2]["tags"]
        assert "shared-a" in tags_2
        assert "shared-b" in tags_2

        # Cleanup
        call_tool("delete_notes", {
            "notes": note_ids,
            "confirmDeletion": True,
        })

    def test_partial_failure_empty_sort_field(self):
        """Note with empty Front (sort field) should fail; others should succeed."""
        uid = unique_id()
        deck_name = f"E2E::BatchEmpty{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        notes = [
            {"fields": {"Front": f"Good1 {uid}", "Back": f"A1 {uid}"}},
            {"fields": {"Front": "", "Back": f"A2 {uid}"}},  # empty sort field
            {"fields": {"Front": f"Good3 {uid}", "Back": f"A3 {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["created"] == 2
        assert result["failed"] == 1
        assert result["total_requested"] == 3

        # Find the failed result
        failed = [r for r in result["results"] if r["status"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["index"] == 1
        assert "sort field" in failed[0]["error"].lower() or "empty" in failed[0]["error"].lower()

        # Find created results and verify they have note_ids
        created = [r for r in result["results"] if r["status"] == "created"]
        assert len(created) == 2
        for c in created:
            assert c["note_id"] > 0

        # Cleanup
        note_ids = [c["note_id"] for c in created]
        call_tool("delete_notes", {
            "notes": note_ids,
            "confirmDeletion": True,
        })

    def test_partial_failure_missing_fields(self):
        """Note missing a required field should fail; others should succeed."""
        uid = unique_id()
        deck_name = f"E2E::BatchMissing{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        notes = [
            {"fields": {"Front": f"OK1 {uid}", "Back": f"A1 {uid}"}},
            {"fields": {"Front": f"MissingBack {uid}"}},  # missing Back field
            {"fields": {"Front": f"OK3 {uid}", "Back": f"A3 {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["created"] == 2
        assert result["failed"] == 1

        # The failed note should be at index 1
        failed = [r for r in result["results"] if r["status"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["index"] == 1
        assert "missing" in failed[0]["error"].lower() or "back" in failed[0]["error"].lower()

        # Cleanup
        created_ids = [r["note_id"] for r in result["results"] if r["status"] == "created"]
        call_tool("delete_notes", {
            "notes": created_ids,
            "confirmDeletion": True,
        })

    def test_duplicate_skipping(self):
        """Duplicate notes should be skipped by default."""
        uid = unique_id()
        deck_name = f"E2E::BatchDup{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create one note first via add_note (singular)
        pre_result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Existing {uid}",
                "Back": f"Already here {uid}",
            },
        })
        existing_note_id = pre_result["note_id"]

        # Batch-add 3 notes, one of which duplicates the existing note
        notes = [
            {"fields": {"Front": f"New1 {uid}", "Back": f"B1 {uid}"}},
            {"fields": {"Front": f"Existing {uid}", "Back": f"Dup back {uid}"}},  # duplicate Front
            {"fields": {"Front": f"New3 {uid}", "Back": f"B3 {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["created"] == 2
        assert result["skipped"] == 1

        # The skipped note should be at index 1
        skipped = [r for r in result["results"] if r["status"] == "skipped"]
        assert len(skipped) == 1
        assert skipped[0]["index"] == 1
        assert skipped[0]["reason"] == "duplicate"

        # Cleanup
        created_ids = [r["note_id"] for r in result["results"] if r["status"] == "created"]
        call_tool("delete_notes", {
            "notes": created_ids + [existing_note_id],
            "confirmDeletion": True,
        })

    def test_allow_duplicate(self):
        """With allow_duplicate=true, duplicates should be created."""
        uid = unique_id()
        deck_name = f"E2E::BatchAllowDup{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create one note first
        pre_result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Dup {uid}",
                "Back": f"Original {uid}",
            },
        })
        existing_note_id = pre_result["note_id"]

        # Batch-add 3 notes including the duplicate, with allow_duplicate
        notes = [
            {"fields": {"Front": f"Fresh1 {uid}", "Back": f"B1 {uid}"}},
            {"fields": {"Front": f"Dup {uid}", "Back": f"Copy {uid}"}},  # duplicate Front
            {"fields": {"Front": f"Fresh3 {uid}", "Back": f"B3 {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
            "allow_duplicate": "true",
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["created"] == 3
        assert result["skipped"] == 0

        # Cleanup
        created_ids = [r["note_id"] for r in result["results"] if r["status"] == "created"]
        call_tool("delete_notes", {
            "notes": created_ids + [existing_note_id],
            "confirmDeletion": True,
        })

    def test_invalid_deck(self):
        """add_notes with a non-existent deck should return isError."""
        uid = unique_id()
        notes = [
            {"fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": f"NonExistentDeck{uid}",
            "model_name": "Basic",
            "notes": notes,
        })

        assert result.get("isError") is True
        assert "deck" in str(result).lower() or "not found" in str(result).lower()

    def test_invalid_model(self):
        """add_notes with a non-existent model should return isError."""
        uid = unique_id()
        deck_name = f"E2E::BatchBadModel{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        notes = [
            {"fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": f"NonExistentModel{uid}",
            "notes": notes,
        })

        assert result.get("isError") is True
        assert "model" in str(result).lower() or "not found" in str(result).lower()

    def test_tool_appears_in_tools_list(self):
        """add_notes should be registered and visible in the tools listing."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "add_notes" in tool_names

    def test_empty_notes_array(self):
        """add_notes with an empty notes array should return isError."""
        uid = unique_id()
        deck_name = f"E2E::BatchEmpty{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": [],
        })

        assert result.get("isError") is True

    def test_partial_failure_extra_fields(self):
        """Note with extra fields should fail; others should succeed."""
        uid = unique_id()
        deck_name = f"E2E::BatchExtra{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        notes = [
            {"fields": {"Front": f"Good1 {uid}", "Back": f"A1 {uid}"}},
            {"fields": {"Front": f"Bad {uid}", "Back": f"A2 {uid}", "Bogus": "value"}},
            {"fields": {"Front": f"Good3 {uid}", "Back": f"A3 {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["created"] == 2
        assert result["failed"] == 1

        # The failed note should be at index 1 and mention unknown fields
        failed = [r for r in result["results"] if r["status"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["index"] == 1
        assert "unknown fields" in failed[0]["error"].lower()

        # Cleanup
        created_ids = [r["note_id"] for r in result["results"] if r["status"] == "created"]
        call_tool("delete_notes", {
            "notes": created_ids,
            "confirmDeletion": True,
        })

    def test_all_notes_fail_validation(self):
        """All notes with empty Front fields should fail without a batch-level error."""
        uid = unique_id()
        deck_name = f"E2E::BatchAllFail{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        notes = [
            {"fields": {"Front": "", "Back": f"A1 {uid}"}},
            {"fields": {"Front": "", "Back": f"A2 {uid}"}},
            {"fields": {"Front": "", "Back": f"A3 {uid}"}},
        ]

        result = call_tool("add_notes", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "notes": notes,
        })

        assert result.get("isError") is not True, f"Unexpected batch error: {result}"
        assert result["created"] == 0
        assert result["failed"] == 3
        assert result["total_requested"] == 3

        # Every result should be failed
        for r in result["results"]:
            assert r["status"] == "failed"
