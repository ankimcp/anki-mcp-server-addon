"""Empty a filtered deck."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool

from .actions.empty import empty_impl


@Tool(
    "filtered_deck_empty",
    "Empty a filtered deck, returning all its cards to their original decks. "
    "Card scheduling is preserved. "
    "Returns deck_id.",
    title="Empty Filtered Deck",
    write=True,
    destructive_hint=False,
    idempotent_hint=True,
)
def filtered_deck_empty(
    deck_id: Annotated[int, Field(description="Filtered deck ID")],
) -> dict[str, Any]:
    return empty_impl(deck_id=deck_id)
