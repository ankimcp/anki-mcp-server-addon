"""Multi-action tool for card management operations."""
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.reposition import reposition_impl
from .actions.change_deck import change_deck_impl
from .actions.bury import bury_impl
from .actions.unbury import unbury_impl


class RepositionParams(BaseModel):
    """Parameters for reposition action."""
    action: Literal["reposition"]
    card_ids: list[int] = Field(description="Card IDs to reposition")
    starting_from: int = Field(default=0, description="Starting position (0-based)")
    step_size: int = Field(default=1, description="Increment between cards")
    randomize: bool = Field(default=False, description="Shuffle before assigning positions")
    shift_existing: bool = Field(default=False, description="Push other cards forward")


class ChangeDeckParams(BaseModel):
    """Parameters for changeDeck action."""
    action: Literal["changeDeck"]
    card_ids: list[int] = Field(description="Card IDs to move")
    deck: str = Field(description="Target deck name (use '::' for nested, e.g., 'Spanish::Verbs')")


class BuryParams(BaseModel):
    """Parameters for bury action."""
    action: Literal["bury"]
    card_ids: list[int] = Field(description="Card IDs to bury")


class UnburyParams(BaseModel):
    """Parameters for unbury action."""
    action: Literal["unbury"]
    deck_name: str = Field(description="Deck name to unbury all cards from")


CardManagementParams = Annotated[
    Union[RepositionParams, ChangeDeckParams, BuryParams, UnburyParams],
    Field(discriminator="action")
]


@Tool(
    "card_management",
    """Manage card organization with four actions:

    - reposition: Reposition NEW cards in the review queue (set learning order).
      Note: Only works on NEW cards (queue=0). Non-new cards are silently skipped.

    - changeDeck: Move cards to a different deck (creates deck if needed).
      Note: Works with ANY card type.

    - bury: Manually bury cards to hide them until the next day.
      Note: Works with ANY card type.

    - unbury: Restore all buried cards in a specific deck.
      Note: Unburies ALL buried cards in the specified deck.""",
    write=True,
)
def card_management(params: CardManagementParams) -> dict[str, Any]:
    """Dispatcher for card management operations."""
    match params.action:
        case "reposition":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return reposition_impl(
                card_ids=params.card_ids,
                starting_from=params.starting_from,
                step_size=params.step_size,
                randomize=params.randomize,
                shift_existing=params.shift_existing,
            )
        case "changeDeck":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return change_deck_impl(card_ids=params.card_ids, deck=params.deck)
        case "bury":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return bury_impl(card_ids=params.card_ids)
        case "unbury":
            return unbury_impl(deck_name=params.deck_name)
        case _:
            raise HandlerError(f"Unknown action: {params.action}")
