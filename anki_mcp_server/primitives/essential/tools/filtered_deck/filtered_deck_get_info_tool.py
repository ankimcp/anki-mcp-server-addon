"""Inspect filtered deck configuration."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool

from .actions.get_info import get_info_impl


@Tool(
    "filtered_deck_get_info",
    "Inspect the configuration of up to 50 decks by ID. "
    "Non-filtered decks are included with is_filtered=false; "
    "unknown deck IDs are skipped and counted in not_found. "
    "Returns search terms, limit, order, reschedule setting and card count per deck.",
    title="Get Filtered Deck Info",
)
def filtered_deck_get_info(
    deck_ids: Annotated[
        list[int],
        Field(
            description="Deck IDs to inspect (max 50)",
            min_length=1,
            max_length=50,
        ),
    ],
) -> dict[str, Any]:
    return get_info_impl(deck_ids=deck_ids)
