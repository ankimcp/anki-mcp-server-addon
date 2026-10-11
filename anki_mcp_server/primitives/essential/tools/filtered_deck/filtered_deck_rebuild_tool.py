"""Rebuild a filtered deck."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool

from .actions.rebuild import rebuild_impl


@Tool(
    "filtered_deck_rebuild",
    "Rebuild a filtered deck: return all its cards to their home decks, "
    "then re-pull cards matching its search terms. "
    "Returns card_count after the rebuild.",
    title="Rebuild Filtered Deck",
    write=True,
    destructive_hint=False,
)
def filtered_deck_rebuild(
    deck_id: Annotated[int, Field(description="Filtered deck ID")],
) -> dict[str, Any]:
    return rebuild_impl(deck_id=deck_id)
