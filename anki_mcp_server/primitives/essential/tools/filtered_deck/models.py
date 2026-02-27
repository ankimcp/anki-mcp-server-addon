"""Pydantic models and constants for filtered deck tool."""
from typing import Literal

from pydantic import BaseModel, Field


# Values from proto/anki/decks.proto Deck.Filtered.SearchTerm.Order
ORDER_MAP: dict[str, int] = {
    "oldest_reviewed_first": 0,
    "random": 1,
    "intervals_ascending": 2,
    "intervals_descending": 3,
    "lapses": 4,
    "added": 5,
    "due": 6,
    "reverse_added": 7,
    "retrievability_ascending": 8,
    "retrievability_descending": 9,
    "relative_overdueness": 10,
}


class SearchTermParam(BaseModel):
    search: str = Field(description="Anki search query (e.g., 'deck:Spanish is:due')")
    limit: int = Field(default=100, description="Max cards to pull", ge=1)
    order: Literal[
        "oldest_reviewed_first",
        "random",
        "intervals_ascending",
        "intervals_descending",
        "lapses",
        "added",
        "due",
        "reverse_added",
        "retrievability_ascending",
        "retrievability_descending",
        "relative_overdueness",
    ] = Field(default="random", description="Card selection order (default matches Anki GUI)")
