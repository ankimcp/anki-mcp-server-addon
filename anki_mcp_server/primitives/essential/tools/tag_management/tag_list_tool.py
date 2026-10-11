"""List the tags in the collection or a deck."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool

from .actions.get_tags import get_tags_impl


@Tool(
    "tag_list",
    "List all tags in the collection. The optional deck param scopes results to tags "
    "on notes with at least one card in that deck (subdecks included); omit it for "
    "all collection tags. Returns tags array and count.",
    title="List Tags",
)
def tag_list(
    deck: Annotated[
        str,
        Field(
            description=(
                "Optional deck name to scope tags to notes with a card in this deck "
                "(subdecks included). Omit or leave empty for all collection tags."
            ),
        ),
    ] = "",
) -> dict[str, Any]:
    return get_tags_impl(deck=deck)
