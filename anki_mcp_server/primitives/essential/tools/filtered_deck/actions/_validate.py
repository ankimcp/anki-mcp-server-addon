"""Shared validation for filtered deck actions."""
from typing import Any

from ......handler_wrappers import HandlerError


def validate_filtered_deck(col: Any, deck_id: int) -> dict[str, Any]:
    deck = col.decks.get(deck_id)
    if not deck:
        raise HandlerError(
            "Deck not found",
            hint="Check deck_id. Use list_decks to see available decks.",
            deck_id=deck_id,
        )
    if not deck.get("dyn"):
        raise HandlerError(
            "Not a filtered deck",
            hint="This operation only works on filtered decks. Use list_decks to find filtered decks.",
            deck_id=deck_id,
            deck_name=deck.get("name", ""),
        )
    return deck
