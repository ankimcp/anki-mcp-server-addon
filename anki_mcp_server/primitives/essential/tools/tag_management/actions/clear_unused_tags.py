"""ClearUnusedTags action implementation for tag_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def clear_unused_tags_impl() -> dict[str, Any]:
    """Clear all unused tags from the collection.

    Returns:
        Dict with cleared count and message
    """
    col = get_col()
    result = col.tags.clear_unused_tags()
    return {
        "cleared_count": result.count,
        "message": f"Cleared {result.count} unused tag(s)",
    }
