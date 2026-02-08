"""Bury action implementation for card_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def bury_impl(card_ids: list[int]) -> dict[str, Any]:
    """Bury cards manually.

    Args:
        card_ids: Card IDs to bury

    Returns:
        Dict with buried count and message
    """
    col = get_col()
    result = col.sched.bury_cards(card_ids, manual=True)
    return {
        "buried_count": result.count,
        "card_ids": card_ids,
        "message": f"Buried {result.count} cards",
    }
