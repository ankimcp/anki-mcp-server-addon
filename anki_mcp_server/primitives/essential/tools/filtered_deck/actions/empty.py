"""Empty filtered deck action."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col
from ._validate import validate_filtered_deck


def empty_impl(deck_id: int) -> dict[str, Any]:
    col = get_col()
    validate_filtered_deck(col, deck_id)

    try:
        col.sched.empty_filtered_deck(deck_id)
    except Exception as e:
        raise HandlerError(
            f"Failed to empty filtered deck: {e}",
            hint="The deck may have been modified or deleted. Use list_decks to verify.",
            deck_id=deck_id,
        )

    return {
        "deck_id": deck_id,
        "message": "Cards returned to their original decks",
    }
