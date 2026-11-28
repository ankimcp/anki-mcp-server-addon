"""Implementation for the card_change_deck tool."""
from typing import Any

from ......handler_wrappers import get_col


def change_deck_impl(card_ids: list[int], deck: str) -> dict[str, Any]:
    """Move cards to a different deck.

    Args:
        card_ids: Card IDs to move
        deck: Target deck name, already checked by ``resolve_deck_target``
            (creates if doesn't exist)

    Returns:
        Dict with moved count, deck_id, and message
    """
    col = get_col()
    deck_id = col.decks.id(deck)  # Creates deck if it doesn't exist
    result = col.set_deck(card_ids, deck_id)
    return {
        "moved": result.count,
        "deck_id": deck_id,
        "message": f"Moved {result.count} cards to deck '{deck}'",
    }
