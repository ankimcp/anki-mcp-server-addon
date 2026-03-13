"""ReplaceTags action implementation for tag_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def replace_tags_impl(note_ids: list[int], old_tag: str, new_tag: str) -> dict[str, Any]:
    """Replace a tag on notes by adding the new tag and removing the old one.

    Args:
        note_ids: Note IDs to replace tags on
        old_tag: Tag to remove
        new_tag: Tag to add

    Returns:
        Dict with note_ids, old/new tags, and message
    """
    col = get_col()
    add_result = col.tags.bulk_add(note_ids, new_tag)
    remove_result = col.tags.bulk_remove(note_ids, old_tag)
    return {
        "note_ids": note_ids,
        "old_tag": old_tag,
        "new_tag": new_tag,
        "added_count": add_result.count,
        "removed_count": remove_result.count,
        "message": f"Replaced tag '{old_tag}' with '{new_tag}' on {remove_result.count} note(s)",
    }
