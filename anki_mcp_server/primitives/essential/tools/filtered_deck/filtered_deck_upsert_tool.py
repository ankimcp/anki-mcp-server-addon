"""Create a filtered deck or update an existing one."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .models import SearchTermParam

from .actions.create_or_update import create_or_update_impl


@Tool(
    "filtered_deck_upsert",
    "Create a new filtered deck (deck_id=0) or update an existing one. "
    "Filtered decks temporarily borrow cards from other decks based on 1-2 search terms "
    "(Anki's hard limit); cards are moved, not duplicated. "
    "Anki may change the name if it conflicts. "
    "Returns deck_id, name and card_count.",
    title="Create or Update Filtered Deck",
    write=True,
)
def filtered_deck_upsert(
    name: Annotated[
        str, Field(description="Deck name (e.g., 'Cram Session' or 'Study::Filtered')")
    ],
    search_terms: Annotated[
        list[SearchTermParam],
        Field(
            description="1-2 search terms defining which cards to pull",
            min_length=1,
            max_length=2,
        ),
    ],
    deck_id: Annotated[
        int,
        Field(description="0 = create new deck, non-zero = update existing filtered deck"),
    ] = 0,
    reschedule: Annotated[
        bool, Field(description="Update scheduling after review (recommended True)")
    ] = True,
    allow_empty: Annotated[
        bool,
        Field(
            description=(
                "Allow creation even if search matches 0 cards. "
                "When False, the ENTIRE operation is rolled back if no cards match "
                "-- deck is NOT created."
            )
        ),
    ] = True,
) -> dict[str, Any]:
    return create_or_update_impl(
        deck_id=deck_id,
        name=name,
        search_terms=search_terms,
        reschedule=reschedule,
        allow_empty=allow_empty,
    )
