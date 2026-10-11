"""Unsuspend cards."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.unsuspend import unsuspend_impl


@Tool(
    "card_unsuspend",
    "Unsuspend cards, restoring them to their previous queue. "
    "Only cards that are currently suspended are affected. "
    "Returns unsuspended_count and card_ids.",
    title="Unsuspend Cards",
    write=True,
    idempotent_hint=True,
)
def card_unsuspend(
    card_ids: Annotated[list[int], Field(description="Card IDs to unsuspend")],
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return unsuspend_impl(card_ids=card_ids)
