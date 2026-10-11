"""Set or change the due date for cards."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.set_due_date import set_due_date_impl


@Tool(
    "card_set_due_date",
    "Set or change the due date for cards. "
    "days: '0' = due now, '5' = due in 5 days, '5-7' = random range, '5!' = also reset interval. "
    "Returns affected_count and card_ids.",
    title="Set Card Due Date",
    write=True,
)
def card_set_due_date(
    card_ids: Annotated[list[int], Field(description="Card IDs to reschedule")],
    days: Annotated[str, Field(description="Due date string: '5' = due in 5 days, '5-7' = random range, '0' = due now, '5!' = set due AND reset interval")],
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return set_due_date_impl(card_ids=card_ids, days=days)
