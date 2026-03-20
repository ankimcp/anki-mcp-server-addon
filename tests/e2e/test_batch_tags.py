"""Tests for batch_tags action in tag_management tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestBatchTags:
    """Tests for batch_tags action in tag_management tool."""

    def test_batch_add_and_remove(self):
        """batch_tags should handle mixed add/remove operations."""
        uid = unique_id()
        deck_name = f"E2E::BatchTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create two notes with an initial tag
        note1 = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q1 {uid}", "Back": f"A1 {uid}"},
            "tags": ["initial-tag"],
        })
        note2 = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q2 {uid}", "Back": f"A2 {uid}"},
            "tags": ["initial-tag"],
        })
        nid1 = note1["note_id"]
        nid2 = note2["note_id"]

        # Batch: add a new tag to both, remove initial-tag from note1
        result = call_tool("tag_management", {
            "params": {
                "action": "batch_tags",
                "operations": [
                    {"type": "add", "note_ids": [nid1, nid2], "tags": "new-tag"},
                    {"type": "remove", "note_ids": [nid1], "tags": "initial-tag"},
                ],
            }
        })

        assert result.get("isError") is not True
        assert result["succeeded"] == 2
        assert result["failed"] == 0
        assert result["total_operations"] == 2
        assert len(result["results"]) == 2

        # Verify note1: has new-tag, no initial-tag
        info1 = call_tool("notes_info", {"notes": [nid1]})
        tags1 = info1["notes"][0]["tags"]
        assert "new-tag" in tags1
        assert "initial-tag" not in tags1

        # Verify note2: has both tags
        info2 = call_tool("notes_info", {"notes": [nid2]})
        tags2 = info2["notes"][0]["tags"]
        assert "new-tag" in tags2
        assert "initial-tag" in tags2

    def test_batch_partial_success(self):
        """batch_tags should continue after individual operation failures."""
        uid = unique_id()
        deck_name = f"E2E::BatchPartial{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"},
        })
        nid = note["note_id"]

        # Middle operation has empty tags (should fail), others should succeed
        result = call_tool("tag_management", {
            "params": {
                "action": "batch_tags",
                "operations": [
                    {"type": "add", "note_ids": [nid], "tags": "good-tag-1"},
                    {"type": "add", "note_ids": [nid], "tags": "  "},
                    {"type": "add", "note_ids": [nid], "tags": "good-tag-2"},
                ],
            }
        })

        assert result.get("isError") is not True
        assert result["succeeded"] == 2
        assert result["failed"] == 1
        assert result["total_operations"] == 3

        # Check individual results
        assert result["results"][0]["status"] == "ok"
        assert result["results"][1]["status"] == "failed"
        assert result["results"][2]["status"] == "ok"

        # Verify the good tags were actually added
        info = call_tool("notes_info", {"notes": [nid]})
        tags = info["notes"][0]["tags"]
        assert "good-tag-1" in tags
        assert "good-tag-2" in tags

    def test_batch_empty_operations(self):
        """batch_tags should error with empty operations list."""
        result = call_tool("tag_management", {
            "params": {
                "action": "batch_tags",
                "operations": [],
            }
        })

        assert result.get("isError") is True

    def test_batch_operations_execute_in_order(self):
        """Operations should execute sequentially in given order."""
        uid = unique_id()
        deck_name = f"E2E::BatchOrder{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"},
        })
        nid = note["note_id"]

        # Add a tag then remove it in the same batch -- net result: no tag
        result = call_tool("tag_management", {
            "params": {
                "action": "batch_tags",
                "operations": [
                    {"type": "add", "note_ids": [nid], "tags": "ephemeral"},
                    {"type": "remove", "note_ids": [nid], "tags": "ephemeral"},
                ],
            }
        })

        assert result.get("isError") is not True
        assert result["succeeded"] == 2

        # Tag should NOT be present (removed after add)
        info = call_tool("notes_info", {"notes": [nid]})
        tags = info["notes"][0]["tags"]
        assert "ephemeral" not in tags

    def test_batch_empty_note_ids_in_operation(self):
        """An operation with empty note_ids should fail, others continue."""
        uid = unique_id()
        deck_name = f"E2E::BatchEmptyIds{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"},
        })
        nid = note["note_id"]

        result = call_tool("tag_management", {
            "params": {
                "action": "batch_tags",
                "operations": [
                    {"type": "add", "note_ids": [nid], "tags": "good-tag"},
                    {"type": "add", "note_ids": [], "tags": "wont-apply"},
                ],
            }
        })

        assert result.get("isError") is not True
        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert result["results"][1]["status"] == "failed"
        assert "note_ids" in result["results"][1]["error"]

    def test_batch_over_limit(self):
        """batch_tags should reject more than 50 operations."""
        # Limit check happens in dispatcher before impl runs — no setup needed
        operations = [
            {"type": "add", "note_ids": [1], "tags": f"tag-{i}"}
            for i in range(51)
        ]

        result = call_tool("tag_management", {
            "params": {
                "action": "batch_tags",
                "operations": operations,
            }
        })

        assert result.get("isError") is True
        assert "50" in str(result)
