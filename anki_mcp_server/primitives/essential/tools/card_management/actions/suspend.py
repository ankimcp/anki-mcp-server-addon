"""Suspend action implementation for card_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def suspend_impl(card_ids: list[int]) -> dict[str, Any]:
    """Suspend cards.

    Args:
        card_ids: Card IDs to suspend

    Returns:
        Dict with suspended count and message
    """
    col = get_col()
    result = col.sched.suspend_cards(card_ids)
    return {
        "suspended_count": result.count,
        "card_ids": card_ids,
        "message": f"Suspended {result.count} cards",
    }
