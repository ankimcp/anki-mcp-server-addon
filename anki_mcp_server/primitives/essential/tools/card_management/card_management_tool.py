"""Multi-action tool for card management operations."""
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.reposition import reposition_impl
from .actions.change_deck import change_deck_impl
from .actions.bury import bury_impl
from .actions.unbury import unbury_impl
from .actions.suspend import suspend_impl
from .actions.unsuspend import unsuspend_impl
from .actions.set_flag import set_flag_impl
from .actions.set_due_date import set_due_date_impl
from .actions.forget import forget_impl

_BASE_DESCRIPTION = "Manage card organization"


class RepositionParams(BaseModel):
    """Parameters for reposition action."""
    _tool_description: ClassVar[str] = (
        "reposition: Reposition NEW cards in the review queue (set learning order). "
        "Only works on NEW cards (queue=0). Non-new cards are silently skipped."
    )
    action: Literal["reposition"]
    card_ids: list[int] = Field(description="Card IDs to reposition")
    starting_from: int = Field(default=0, description="Starting position (0-based)")
    step_size: int = Field(default=1, description="Increment between cards")
    randomize: bool = Field(default=False, description="Shuffle before assigning positions")
    shift_existing: bool = Field(default=False, description="Push other cards forward")


class ChangeDeckParams(BaseModel):
    """Parameters for change_deck action."""
    _tool_description: ClassVar[str] = (
        "change_deck: Move cards to a different deck (creates deck if needed). "
        "Works with ANY card type."
    )
    action: Literal["change_deck"]
    card_ids: list[int] = Field(description="Card IDs to move")
    deck: str = Field(description="Target deck name (use '::' for nested, e.g., 'Spanish::Verbs')")


class BuryParams(BaseModel):
    """Parameters for bury action."""
    _tool_description: ClassVar[str] = (
        "bury: Manually bury cards to hide them until the next day. "
        "Works with ANY card type."
    )
    action: Literal["bury"]
    card_ids: list[int] = Field(description="Card IDs to bury")


class UnburyParams(BaseModel):
    """Parameters for unbury action."""
    _tool_description: ClassVar[str] = (
        "unbury: Restore all buried cards in a specific deck. "
        "Unburies ALL buried cards in the specified deck."
    )
    action: Literal["unbury"]
    deck_name: str = Field(description="Deck name to unbury all cards from")


class SuspendParams(BaseModel):
    """Parameters for suspend action."""
    _tool_description: ClassVar[str] = (
        "suspend: Suspend cards (hide from review indefinitely until unsuspended). "
        "Works with ANY card type."
    )
    action: Literal["suspend"]
    card_ids: list[int] = Field(description="Card IDs to suspend")


class UnsuspendParams(BaseModel):
    """Parameters for unsuspend action."""
    _tool_description: ClassVar[str] = (
        "unsuspend: Unsuspend cards (restore suspended cards to their previous queue). "
        "Only affects cards that are currently suspended."
    )
    action: Literal["unsuspend"]
    card_ids: list[int] = Field(description="Card IDs to unsuspend")


class SetFlagParams(BaseModel):
    """Parameters for set_flag action."""
    _tool_description: ClassVar[str] = (
        "set_flag: Set or remove a colored flag on cards. "
        "Flag values: 0=none/remove, 1=red, 2=orange, 3=green, 4=blue, 5-7=custom."
    )
    action: Literal["set_flag"]
    card_ids: list[int] = Field(description="Card IDs to flag")
    flag: int = Field(description="Flag value: 0=none/remove, 1=red, 2=orange, 3=green, 4=blue, 5-7=custom user flags")


class SetDueDateParams(BaseModel):
    """Parameters for set_due_date action."""
    _tool_description: ClassVar[str] = (
        "set_due_date: Set or change the due date for cards. "
        "Days: '0' = due now, '5' = due in 5 days, '5-7' = random range, '5!' = also reset interval."
    )
    action: Literal["set_due_date"]
    card_ids: list[int] = Field(description="Card IDs to reschedule")
    days: str = Field(description="Due date string: '5' = due in 5 days, '5-7' = random range, '0' = due now, '5!' = set due AND reset interval")


class ForgetCardsParams(BaseModel):
    """Parameters for forget_cards action."""
    _tool_description: ClassVar[str] = (
        "forget_cards: Reset cards back to new state (forget scheduling). "
        "Options: restore_position (default true), reset_counts (default false)."
    )
    action: Literal["forget_cards"]
    card_ids: list[int] = Field(description="Card IDs to reset to new state")
    restore_position: bool = Field(default=True, description="Restore original new-card position")
    reset_counts: bool = Field(default=False, description="Reset review and lapse counts")


CardManagementParams = Annotated[
    Union[RepositionParams, ChangeDeckParams, BuryParams, UnburyParams, SuspendParams, UnsuspendParams, SetFlagParams, SetDueDateParams, ForgetCardsParams],
    Field(discriminator="action")
]


@Tool(
    "card_management",
    """Manage card organization with nine actions:

    - reposition: Reposition NEW cards in the review queue (set learning order).
      Note: Only works on NEW cards (queue=0). Non-new cards are silently skipped.

    - change_deck: Move cards to a different deck (creates deck if needed).
      Note: Works with ANY card type.

    - bury: Manually bury cards to hide them until the next day.
      Note: Works with ANY card type.

    - unbury: Restore all buried cards in a specific deck.
      Note: Unburies ALL buried cards in the specified deck.

    - suspend: Suspend cards (hide from review indefinitely until unsuspended).
      Note: Works with ANY card type.

    - unsuspend: Unsuspend cards (restore suspended cards to their previous queue).
      Note: Only affects cards that are currently suspended.

    - set_flag: Set or remove a colored flag on cards.
      flag values: 0=none/remove, 1=red, 2=orange, 3=green, 4=blue, 5-7=custom.

    - set_due_date: Set or change the due date for cards.
      days: '0' = due now, '5' = due in 5 days, '5-7' = random range, '5!' = also reset interval.

    - forget_cards: Reset cards back to new state (forget scheduling).
      Options: restore_position (default true), reset_counts (default false).""",
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
        case "change_deck":
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
        case "suspend":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return suspend_impl(card_ids=params.card_ids)
        case "unsuspend":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return unsuspend_impl(card_ids=params.card_ids)
        case "set_flag":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return set_flag_impl(card_ids=params.card_ids, flag=params.flag)
        case "set_due_date":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return set_due_date_impl(card_ids=params.card_ids, days=params.days)
        case "forget_cards":
            if not params.card_ids:
                raise HandlerError(
                    "card_ids is required and cannot be empty",
                    hint="Provide at least one card ID",
                    action=params.action,
                )
            return forget_impl(
                card_ids=params.card_ids,
                restore_position=params.restore_position,
                reset_counts=params.reset_counts,
            )
        case _:
            raise HandlerError(f"Unknown action: {params.action}")
