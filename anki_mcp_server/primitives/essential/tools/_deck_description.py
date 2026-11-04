"""Shared lookup and response shape for deck descriptions."""
from typing import Any

from ....handler_wrappers import HandlerError


def get_description_deck(col: Any, deck_name: str) -> dict[str, Any]:
    if not deck_name.strip():
        raise HandlerError("Deck name must not be empty", code="validation_error")

    # by_name() is a lookup only; decks.id() would create an unknown deck.
    deck = col.decks.by_name(deck_name)
    if deck is None:
        raise HandlerError(
            f'Deck "{deck_name}" not found',
            hint="list_decks lists the available decks",
            code="not_found",
            deck_name=deck_name,
        )
    if deck.get("dyn"):
        raise HandlerError(
            "Filtered decks do not support deck descriptions",
            code="validation_error",
            deck_name=deck["name"],
        )
    return deck


def description_info(deck: dict[str, Any]) -> dict[str, Any]:
    return {
        "deck_id": deck["id"],
        "name": deck["name"],
        "description": deck.get("desc", ""),
        "is_markdown": bool(deck.get("md", False)),
    }
