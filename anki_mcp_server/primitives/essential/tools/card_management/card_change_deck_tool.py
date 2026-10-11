"""Move cards to a different deck."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.change_deck import change_deck_impl


@Tool(
    "card_change_deck",
    "Move cards to a different deck, creating the deck if needed. "
    "Works with any card type. Cards in a filtered deck are taken out of it. "
    "With FSRS on, Anki before 26.09 clears the cards' memory state until their "
    "next review; 26.09+ keeps it. "
    "Returns moved count and deck_id.",
    title="Move Cards to Deck",
    write=True,
    destructive_hint=False,
    idempotent_hint=True,
)
def card_change_deck(
    card_ids: Annotated[list[int], Field(description="Card IDs to move")],
    deck: Annotated[str, Field(description="Target deck name (use '::' for nested, e.g., 'Spanish::Verbs')")],
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return change_deck_impl(card_ids=card_ids, deck=deck)
