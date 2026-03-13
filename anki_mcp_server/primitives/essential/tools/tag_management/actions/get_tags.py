"""GetTags action implementation for tag_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def get_tags_impl() -> dict[str, Any]:
    """List all tags in the collection.

    Returns:
        Dict with tags list, count, and message
    """
    col = get_col()
    tags = col.tags.all()
    return {
        "tags": tags,
        "count": len(tags),
        "message": f"Found {len(tags)} tag(s)",
    }
