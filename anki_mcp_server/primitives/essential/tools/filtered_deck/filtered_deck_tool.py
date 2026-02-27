"""Multi-action tool for filtered deck lifecycle management."""
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError
from .models import SearchTermParam

from .actions.create_or_update import create_or_update_impl
from .actions.rebuild import rebuild_impl
from .actions.empty import empty_impl
from .actions.delete import delete_impl


class CreateOrUpdateParams(BaseModel):
    action: Literal["create_or_update"]
    deck_id: int = Field(
        default=0,
        description="0 = create new deck, non-zero = update existing filtered deck",
    )
    name: str = Field(
        description="Deck name (e.g., 'Cram Session' or 'Study::Filtered')"
    )
    search_terms: list[SearchTermParam] = Field(
        description="1-2 search terms defining which cards to pull",
        min_length=1,
        max_length=2,
    )
    reschedule: bool = Field(
        default=True,
        description="Update scheduling after review (recommended True)",
    )
    allow_empty: bool = Field(
        default=True,
        description=(
            "Allow creation even if search matches 0 cards. "
            "When False, the ENTIRE operation is rolled back if no cards match "
            "-- deck is NOT created."
        ),
    )


class RebuildParams(BaseModel):
    action: Literal["rebuild"]
    deck_id: int = Field(description="Filtered deck ID")


class EmptyParams(BaseModel):
    action: Literal["empty"]
    deck_id: int = Field(description="Filtered deck ID")


class DeleteParams(BaseModel):
    action: Literal["delete"]
    deck_id: int = Field(description="Filtered deck ID")


FilteredDeckParams = Annotated[
    Union[CreateOrUpdateParams, RebuildParams, EmptyParams, DeleteParams],
    Field(discriminator="action"),
]


@Tool(
    "filtered_deck",
    """Manage filtered (cram) decks with four actions:

    - create_or_update: Create a new filtered deck or update an existing one.
      Filtered decks temporarily borrow cards from other decks based on search queries.
      Cards are NOT duplicated -- they are moved temporarily.
      Max 2 search terms per deck (Anki hard limit).
      Name collisions are resolved by Anki appending '+' (case-insensitive).
      Filtered decks can be children of normal decks but cannot have children.

    - rebuild: Empty and re-pull cards matching the deck's search terms.
      Rebuilding first returns all cards to home decks, then re-searches.

    - empty: Return all cards to their original decks.
      Card scheduling is preserved.

    - delete: Return all cards to original decks and remove the deck.
      Cards are NOT deleted -- they go back to their home decks with scheduling intact.""",
    write=True,
)
def filtered_deck(params: FilteredDeckParams) -> dict[str, Any]:
    match params.action:
        case "create_or_update":
            return create_or_update_impl(
                deck_id=params.deck_id,
                name=params.name,
                search_terms=params.search_terms,
                reschedule=params.reschedule,
                allow_empty=params.allow_empty,
            )
        case "rebuild":
            return rebuild_impl(deck_id=params.deck_id)
        case "empty":
            return empty_impl(deck_id=params.deck_id)
        case "delete":
            return delete_impl(deck_id=params.deck_id)
        case _:
            raise HandlerError(f"Unknown action: {params.action}")
