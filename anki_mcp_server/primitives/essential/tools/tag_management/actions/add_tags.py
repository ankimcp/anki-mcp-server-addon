"""AddTags action implementation for tag_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def add_tags_impl(note_ids: list[int], tags: str) -> dict[str, Any]:
    """Add tags to notes.

    Args:
        note_ids: Note IDs to add tags to
        tags: Space-separated tag names to add

    Returns:
        Dict with added count, tags, and message
    """
    col = get_col()
    result = col.tags.bulk_add(note_ids, tags)
    return {
        "added_count": result.count,
        "tags": tags,
        "message": f"Added tags '{tags}' to {result.count} note(s)",
    }
