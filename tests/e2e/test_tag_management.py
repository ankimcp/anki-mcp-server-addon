"""Tests for tag_management multi-action tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


class TestAddTags:
    """Tests for addTags action in tag_management tool."""

    def test_add_tags_to_note(self):
        """addTags action should add tags to notes."""
        uid = unique_id()
        deck_name = f"E2E::AddTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create a note without tags
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            }
        })
        note_id = note_result["note_id"]

        # Add tags
        result = call_tool("tag_management", {
            "params": {
                "action": "addTags",
                "note_ids": [note_id],
                "tags": "test-tag-1 test-tag-2",
            }
        })

        assert result.get("isError") is not True

        # Verify tags were actually added
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        tags = notes_info["notes"][0]["tags"]
        assert "test-tag-1" in tags
        assert "test-tag-2" in tags

    def test_add_tags_empty_note_ids(self):
        """addTags action should error with empty note_ids."""
        result = call_tool("tag_management", {
            "params": {
                "action": "addTags",
                "note_ids": [],
                "tags": "some-tag",
            }
        })

        assert result.get("isError") is True

    def test_add_tags_empty_tags(self):
        """addTags action should error with empty tags string."""
        uid = unique_id()
        deck_name = f"E2E::AddTagsEmpty{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            }
        })
        note_id = note_result["note_id"]

        result = call_tool("tag_management", {
            "params": {
                "action": "addTags",
                "note_ids": [note_id],
                "tags": "",
            }
        })

        assert result.get("isError") is True


class TestRemoveTags:
    """Tests for removeTags action in tag_management tool."""

    def test_remove_tags_from_note(self):
        """removeTags action should remove tags from notes."""
        uid = unique_id()
        deck_name = f"E2E::RemoveTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create a note with tags
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            },
            "tags": ["remove-me"]
        })
        note_id = note_result["note_id"]

        # Verify tag is present
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        assert "remove-me" in notes_info["notes"][0]["tags"]

        # Remove the tag
        result = call_tool("tag_management", {
            "params": {
                "action": "removeTags",
                "note_ids": [note_id],
                "tags": "remove-me",
            }
        })

        assert result.get("isError") is not True

        # Verify tag was removed
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        assert "remove-me" not in notes_info["notes"][0]["tags"]

    def test_remove_tags_empty_note_ids(self):
        """removeTags action should error with empty note_ids."""
        result = call_tool("tag_management", {
            "params": {
                "action": "removeTags",
                "note_ids": [],
                "tags": "some-tag",
            }
        })

        assert result.get("isError") is True

    def test_remove_tags_empty_tags(self):
        """removeTags action should error with empty tags string."""
        uid = unique_id()
        deck_name = f"E2E::RemoveTagsEmpty{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            }
        })
        note_id = note_result["note_id"]

        result = call_tool("tag_management", {
            "params": {
                "action": "removeTags",
                "note_ids": [note_id],
                "tags": "",
            }
        })

        assert result.get("isError") is True


class TestReplaceTags:
    """Tests for replaceTags action in tag_management tool."""

    def test_replace_tag(self):
        """replaceTags action should replace old tag with new tag."""
        uid = unique_id()
        deck_name = f"E2E::ReplaceTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create a note with the old tag
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            },
            "tags": ["old-tag"]
        })
        note_id = note_result["note_id"]

        # Verify old tag is present
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        assert "old-tag" in notes_info["notes"][0]["tags"]

        # Replace old-tag with new-tag
        result = call_tool("tag_management", {
            "params": {
                "action": "replaceTags",
                "note_ids": [note_id],
                "old_tag": "old-tag",
                "new_tag": "new-tag",
            }
        })

        assert result.get("isError") is not True

        # Verify old tag is gone and new tag is present
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        tags = notes_info["notes"][0]["tags"]
        assert "old-tag" not in tags
        assert "new-tag" in tags

    def test_replace_tags_empty_note_ids(self):
        """replaceTags action should error with empty note_ids."""
        result = call_tool("tag_management", {
            "params": {
                "action": "replaceTags",
                "note_ids": [],
                "old_tag": "old",
                "new_tag": "new",
            }
        })

        assert result.get("isError") is True

    def test_replace_tags_same_old_and_new(self):
        """replaceTags action should error when old_tag equals new_tag."""
        uid = unique_id()
        deck_name = f"E2E::ReplaceSame{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            },
            "tags": ["same-tag"]
        })
        note_id = note_result["note_id"]

        result = call_tool("tag_management", {
            "params": {
                "action": "replaceTags",
                "note_ids": [note_id],
                "old_tag": "same-tag",
                "new_tag": "same-tag",
            }
        })

        assert result.get("isError") is True
        assert "must be different" in str(result)


class TestGetTags:
    """Tests for getTags action in tag_management tool."""

    def test_get_tags(self):
        """getTags action should return list of all tags."""
        result = call_tool("tag_management", {
            "params": {
                "action": "getTags",
            }
        })

        assert result.get("isError") is not True
        assert "tags" in result
        assert isinstance(result["tags"], list)
        assert "count" in result
        assert result["count"] >= 0


class TestClearUnusedTags:
    """Tests for clearUnusedTags action in tag_management tool."""

    def test_clear_unused_tags(self):
        """clearUnusedTags action should clear tags not used by any notes."""
        result = call_tool("tag_management", {
            "params": {
                "action": "clearUnusedTags",
            }
        })

        assert result.get("isError") is not True
        assert "cleared_count" in result
        assert result["cleared_count"] >= 0
