"""SetDueDate action implementation for card_management tool."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col


def set_due_date_impl(card_ids: list[int], days: str) -> dict[str, Any]:
    """Set due date for cards.

    Args:
        card_ids: Card IDs to reschedule
        days: Due date string: "5" = due in 5 days, "5-7" = random range,
              "0" = due now, "5!" = set due AND reset interval

    Returns:
        Dict with affected count, card IDs, days string, and message

    Raises:
        HandlerError: If days string is empty
    """
    if not days.strip():
        raise HandlerError(
            "days parameter cannot be empty",
            hint="Use '0' for due now, '5' for 5 days, '5-7' for random range, or '5!' to also reset interval",
            days=days,
        )

    col = get_col()
    col.sched.set_due_date(card_ids, days)
    return {
        "affected_count": len(card_ids),
        "card_ids": card_ids,
        "days": days,
        "message": f"Set due date for {len(card_ids)} card(s) with days='{days}'",
    }
