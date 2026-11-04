"""Replace a deck's description, optionally changing its Markdown setting."""
from copy import deepcopy
from typing import Annotated, Any

from pydantic import Field

from ....handler_wrappers import get_col
from ....tool_decorator import Tool
from ._deck_description import description_info, get_description_deck


@Tool(
    "update_deck_description",
    "Replace a deck's entire description and optionally change its Markdown setting. "
    "An empty description clears it. Text is stored as supplied, without HTML/Markdown "
    "conversion; omitting is_markdown preserves the current setting. "
    "Only the named deck is updated; other deck settings are preserved. "
    "Unknown and filtered decks return an error. Returns deck_id, name, description "
    "and is_markdown. This operation does not sync with AnkiWeb.",
    title="Update Deck Description",
    write=True,
    idempotent_hint=True,
)
def update_deck_description(
    deck_name: Annotated[
        str, Field(description='Deck name, including any "Parent::Child" path')
    ],
    description: Annotated[
        str, Field(description="Complete replacement description; an empty string clears it")
    ],
    is_markdown: Annotated[
        bool | None,
        Field(description="Whether Anki renders the description as Markdown; omitted or null preserves the current setting"),
    ] = None,
) -> dict[str, Any]:
    col = get_col()
    deck = deepcopy(get_description_deck(col, deck_name))
    deck["desc"] = description
    if is_markdown is not None:
        deck["md"] = is_markdown
    col.decks.update_dict(deck)
    return description_info(deck)
