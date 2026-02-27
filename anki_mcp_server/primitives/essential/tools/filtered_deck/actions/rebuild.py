"""Rebuild filtered deck action."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col
from ._validate import validate_filtered_deck


def rebuild_impl(deck_id: int) -> dict[str, Any]:
    col = get_col()
    validate_filtered_deck(col, deck_id)

    try:
        result = col.sched.rebuild_filtered_deck(deck_id)
    except Exception as e:
        raise HandlerError(
            f"Failed to rebuild filtered deck: {e}",
            hint="The deck may have been modified or deleted. Use list_decks to verify.",
            deck_id=deck_id,
        )

    return {
        "deck_id": deck_id,
        "card_count": result.count,
        "message": f"Rebuilt filtered deck: pulled {result.count} cards",
    }
