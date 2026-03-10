"""ForgetCards action implementation for card_management tool."""
from typing import Any

from ......handler_wrappers import get_col


def forget_impl(
    card_ids: list[int],
    restore_position: bool,
    reset_counts: bool,
) -> dict[str, Any]:
    """Forget cards, resetting them back to new state.

    Args:
        card_ids: Card IDs to forget
        restore_position: Restore original new-card position
        reset_counts: Reset review and lapse counts

    Returns:
        Dict with affected count, card IDs, and message
    """
    col = get_col()
    col.sched.schedule_cards_as_new(
        card_ids,
        restore_position=restore_position,
        reset_counts=reset_counts,
    )
    return {
        "affected_count": len(card_ids),
        "card_ids": card_ids,
        "message": f"Reset {len(card_ids)} card(s) to new state",
    }
