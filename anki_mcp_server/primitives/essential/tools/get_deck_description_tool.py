"""Read a deck's stored description and Markdown setting."""
from typing import Annotated, Any

from pydantic import Field

from ....handler_wrappers import get_col
from ....tool_decorator import Tool
from ._deck_description import description_info, get_description_deck


@Tool(
    "get_deck_description",
    "Read a deck's own stored description and Markdown setting. "
    "The description can document the deck's purpose and card-authoring conventions. "
    "Returns deck_id, name, description and is_markdown, with the original text "
    "and formatting preserved. An unset description is an empty string. "
    "Unknown and filtered decks return an error; parent descriptions are not inherited.",
    title="Get Deck Description",
)
def get_deck_description(
    deck_name: Annotated[
        str, Field(description='Deck name, including any "Parent::Child" path')
    ],
) -> dict[str, Any]:
    return description_info(get_description_deck(get_col(), deck_name))
