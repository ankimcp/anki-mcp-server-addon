"""Delete a filtered deck."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool

from .actions.delete import delete_impl


@Tool(
    "filtered_deck_delete",
    "Delete a filtered deck after returning all its cards to their original decks. "
    "Cards are not deleted; they keep their scheduling. "
    "Returns deck_id.",
    title="Delete Filtered Deck",
    write=True,
    idempotent_hint=True,
)
def filtered_deck_delete(
    deck_id: Annotated[int, Field(description="Filtered deck ID")],
) -> dict[str, Any]:
    return delete_impl(deck_id=deck_id)
