"""Tests for tag_management multi-action tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


class TestAddTags:
    """Tests for add_tags action in tag_management tool."""

    def test_add_tags_to_note(self):
        """add_tags action should add tags to notes."""
        uid = unique_id()
        deck_name = f"E2E::AddTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create a note without tags
        note_result = call_tool("add_note", {
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
                "action": "add_tags",
                "note_ids": [note_id],
                "tags": "test-tag-1 test-tag-2",
            }
        })

        assert result.get("isError") is not True

        # Verify tags were actually added
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        tags = notes_info["notes"][0]["tags"]
        assert "test-tag-1" in tags
        assert "test-tag-2" in tags

    def test_add_tags_empty_note_ids(self):
        """add_tags action should error with empty note_ids."""
        result = call_tool("tag_management", {
            "params": {
                "action": "add_tags",
                "note_ids": [],
                "tags": "some-tag",
            }
        })

        assert result.get("isError") is True

    def test_add_tags_empty_tags(self):
        """add_tags action should error with empty tags string."""
        uid = unique_id()
        deck_name = f"E2E::AddTagsEmpty{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("add_note", {
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
                "action": "add_tags",
                "note_ids": [note_id],
                "tags": "",
            }
        })

        assert result.get("isError") is True


class TestRemoveTags:
    """Tests for remove_tags action in tag_management tool."""

    def test_remove_tags_from_note(self):
        """remove_tags action should remove tags from notes."""
        uid = unique_id()
        deck_name = f"E2E::RemoveTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create a note with tags
        note_result = call_tool("add_note", {
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
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        assert "remove-me" in notes_info["notes"][0]["tags"]

        # Remove the tag
        result = call_tool("tag_management", {
            "params": {
                "action": "remove_tags",
                "note_ids": [note_id],
                "tags": "remove-me",
            }
        })

        assert result.get("isError") is not True

        # Verify tag was removed
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        assert "remove-me" not in notes_info["notes"][0]["tags"]

    def test_remove_tags_empty_note_ids(self):
        """remove_tags action should error with empty note_ids."""
        result = call_tool("tag_management", {
            "params": {
                "action": "remove_tags",
                "note_ids": [],
                "tags": "some-tag",
            }
        })

        assert result.get("isError") is True

    def test_remove_tags_empty_tags(self):
        """remove_tags action should error with empty tags string."""
        uid = unique_id()
        deck_name = f"E2E::RemoveTagsEmpty{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("add_note", {
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
                "action": "remove_tags",
                "note_ids": [note_id],
                "tags": "",
            }
        })

        assert result.get("isError") is True


class TestReplaceTags:
    """Tests for replace_tags action in tag_management tool."""

    def test_replace_tag(self):
        """replace_tags action should replace old tag with new tag."""
        uid = unique_id()
        deck_name = f"E2E::ReplaceTags{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Create a note with the old tag
        note_result = call_tool("add_note", {
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
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        assert "old-tag" in notes_info["notes"][0]["tags"]

        # Replace old-tag with new-tag
        result = call_tool("tag_management", {
            "params": {
                "action": "replace_tags",
                "note_ids": [note_id],
                "old_tag": "old-tag",
                "new_tag": "new-tag",
            }
        })

        assert result.get("isError") is not True

        # Verify old tag is gone and new tag is present
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        tags = notes_info["notes"][0]["tags"]
        assert "old-tag" not in tags
        assert "new-tag" in tags

    def test_replace_tags_empty_note_ids(self):
        """replace_tags action should error with empty note_ids."""
        result = call_tool("tag_management", {
            "params": {
                "action": "replace_tags",
                "note_ids": [],
                "old_tag": "old",
                "new_tag": "new",
            }
        })

        assert result.get("isError") is True

    def test_replace_tags_same_old_and_new(self):
        """replace_tags action should error when old_tag equals new_tag."""
        uid = unique_id()
        deck_name = f"E2E::ReplaceSame{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        note_result = call_tool("add_note", {
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
                "action": "replace_tags",
                "note_ids": [note_id],
                "old_tag": "same-tag",
                "new_tag": "same-tag",
            }
        })

        assert result.get("isError") is True
        assert "must be different" in str(result)


class TestGetTags:
    """Tests for get_tags action in tag_management tool."""

    def test_get_tags(self):
        """get_tags action should return list of all tags."""
        result = call_tool("tag_management", {
            "params": {
                "action": "get_tags",
            }
        })

        assert result.get("isError") is not True
        assert "tags" in result
        assert isinstance(result["tags"], list)
        assert "count" in result
        assert result["count"] >= 0


class TestClearUnusedTags:
    """Tests for clear_unused_tags action in tag_management tool."""

    def test_clear_unused_tags(self):
        """clear_unused_tags action should clear tags not used by any notes."""
        result = call_tool("tag_management", {
            "params": {
                "action": "clear_unused_tags",
            }
        })

        assert result.get("isError") is not True
        assert "cleared_count" in result
        assert result["cleared_count"] >= 0
