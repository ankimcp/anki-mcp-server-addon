"""Unbury action implementation for card_management tool."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col


def unbury_impl(deck_name: str) -> dict[str, Any]:
    """Unbury all buried cards in a deck.

    Args:
        deck_name: Deck name to unbury all cards from

    Returns:
        Dict with deck name and message

    Raises:
        HandlerError: If deck is not found
    """
    col = get_col()
    deck = col.decks.by_name(deck_name)

    if deck is None:
        raise HandlerError(
            f"Deck '{deck_name}' not found",
            hint="Check spelling or use list_decks to see available decks",
            deck_name=deck_name,
        )

    col.sched.unbury_deck(deck["id"])

    return {
        "deck_name": deck_name,
        "message": f"All buried cards in '{deck_name}' have been restored",
    }
