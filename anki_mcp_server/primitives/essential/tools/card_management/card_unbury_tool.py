"""Restore all buried cards in a deck."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool

from .actions.unbury import unbury_impl


@Tool(
    "card_unbury",
    "Restore all buried cards in a deck. "
    "Unburies every buried card in the given deck, not specific cards. "
    "Returns deck_name.",
    title="Unbury Deck Cards",
    write=True,
    idempotent_hint=True,
)
def card_unbury(
    deck_name: Annotated[str, Field(description="Deck name to unbury all cards from")],
) -> dict[str, Any]:
    return unbury_impl(deck_name=deck_name)
