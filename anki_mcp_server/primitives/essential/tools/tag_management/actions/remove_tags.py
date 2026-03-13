"""RemoveTags action implementation for tag_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def remove_tags_impl(note_ids: list[int], tags: str) -> dict[str, Any]:
    """Remove tags from notes.

    Args:
        note_ids: Note IDs to remove tags from
        tags: Space-separated tag names to remove

    Returns:
        Dict with removed count, tags, and message
    """
    col = get_col()
    result = col.tags.bulk_remove(note_ids, tags)
    return {
        "removed_count": result.count,
        "tags": tags,
        "message": f"Removed tags '{tags}' from {result.count} note(s)",
    }
