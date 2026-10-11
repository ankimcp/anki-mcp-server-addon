"""Suspend cards."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.suspend import suspend_impl


@Tool(
    "card_suspend",
    "Suspend cards, hiding them from review until they are unsuspended. "
    "Works with any card type. "
    "Returns suspended_count.",
    title="Suspend Cards",
    write=True,
    destructive_hint=False,
    idempotent_hint=True,
)
def card_suspend(
    card_ids: Annotated[list[int], Field(description="Card IDs to suspend")],
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return suspend_impl(card_ids=card_ids)
