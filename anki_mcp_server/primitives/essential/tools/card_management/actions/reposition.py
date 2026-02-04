"""Reposition action implementation for card_actions tool."""
from typing import Any

from anki_mcp_server.handler_wrappers import HandlerError, get_col


def reposition_impl(
    card_ids: list[int],
    starting_from: int,
    step_size: int,
    randomize: bool,
    shift_existing: bool,
) -> dict[str, Any]:
    """Reposition NEW cards in the review queue.

    Args:
        card_ids: Card IDs to reposition
        starting_from: Starting position (e.g., 1 for first)
        step_size: Increment between cards (typically 1)
        randomize: Shuffle before assigning positions
        shift_existing: Push other cards forward to make room

    Returns:
        Dict with repositioned count and message

    Raises:
        HandlerError: If parameters are invalid
    """
    if starting_from < 0:
        raise HandlerError(
            "starting_from must be >= 0",
            hint="Use 0 or positive integers for card positions",
            starting_from=starting_from
        )
    if step_size < 1:
        raise HandlerError(
            "step_size must be >= 1",
            hint="Use positive integers for step size (typically 1)",
            step_size=step_size
        )

    col = get_col()
    result = col.sched.reposition_new_cards(
        card_ids=card_ids,
        starting_from=starting_from,
        step_size=step_size,
        randomize=randomize,
        shift_existing=shift_existing,
    )
    return {
        "repositioned": result.count,
        "message": f"Repositioned {result.count} new cards starting at position {starting_from}",
    }
