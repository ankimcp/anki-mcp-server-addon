"""Unsuspend action implementation for card_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def unsuspend_impl(card_ids: list[int]) -> dict[str, Any]:
    """Unsuspend cards.

    Args:
        card_ids: Card IDs to unsuspend

    Returns:
        Dict with unsuspended count and message
    """
    col = get_col()
    col.sched.unsuspend_cards(card_ids)
    return {
        "unsuspended_count": len(card_ids),
        "card_ids": card_ids,
        "message": f"Unsuspended {len(card_ids)} card(s)",
    }
