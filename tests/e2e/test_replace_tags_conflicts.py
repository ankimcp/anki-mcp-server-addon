"""E2E tests for replace_tags inputs that Anki's tag removal would undo.

replace_tags adds new_tag, then removes old_tag. Anki's removal matches
case-insensitively and also strips old_tag's children (old_tag::...), so a
new_tag that is a case variant or a child of old_tag is rejected before any
write -- the note's tags must be left exactly as they were.

Anki's tag registry reuses the first-seen spelling of a tag collection-wide,
so every test uses its own uuid-suffixed tag root (no two tests share a tag
ignoring case) and compares against the tags as stored right after creation.
"""
from __future__ import annotations

from uuid import uuid4

from .conftest import unique_id
from .helpers import call_tool


def _tag_root() -> str:
    return f"e2e{uuid4().hex[:8]}"


def _note_with_tags(deck_prefix: str, tags: list[str]) -> int:
    uid = unique_id()
    deck_name = f"E2E::{deck_prefix}{uid}"
    call_tool("create_deck", {"deck_name": deck_name})
    note_result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {
            "Front": f"Question {uid}",
            "Back": f"Answer {uid}",
        },
        "tags": tags,
    })
    return note_result["note_id"]


def _tags(note_id: int) -> list[str]:
    return call_tool("notes_info", {"notes": [note_id]})["notes"][0]["tags"]


def _replace(note_id: int, old_tag: str, new_tag: str) -> dict:
    return call_tool("tag_management", {
        "params": {
            "action": "replace_tags",
            "note_ids": [note_id],
            "old_tag": old_tag,
            "new_tag": new_tag,
        }
    })


class TestReplaceTagsConflicts:
    def test_child_of_old_tag_is_rejected(self):
        root = _tag_root()
        note_id = _note_with_tags("ReplaceChild", [root])
        before = _tags(note_id)

        result = _replace(note_id, root, f"{root}::verbs")

        assert result.get("isError") is True
        assert "child" in str(result)
        assert _tags(note_id) == before

    def test_case_only_rename_is_rejected(self):
        root = _tag_root()
        capitalised = root.capitalize()
        note_id = _note_with_tags("ReplaceCase", [capitalised])
        before = _tags(note_id)

        result = _replace(note_id, capitalised, root)

        assert result.get("isError") is True
        assert "must be different" in str(result)
        assert _tags(note_id) == before

    def test_replace_child_with_parent_still_works(self):
        root = _tag_root()
        note_id = _note_with_tags("ReplaceToParent", [f"{root}::verbs"])
        before = _tags(note_id)
        assert len(before) == 1

        result = _replace(note_id, before[0], root)

        assert result.get("isError") is not True
        assert _tags(note_id) == [root]
