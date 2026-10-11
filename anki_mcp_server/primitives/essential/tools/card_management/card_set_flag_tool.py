"""Set or remove a colored flag on cards."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.set_flag import set_flag_impl


@Tool(
    "card_set_flag",
    "Set or remove a colored flag on cards. "
    "Flag values: 0=none/remove, 1=red, 2=orange, 3=green, 4=blue, 5-7=custom. "
    "Returns flagged_count.",
    title="Set Card Flag",
    write=True,
    idempotent_hint=True,
)
def card_set_flag(
    card_ids: Annotated[list[int], Field(description="Card IDs to flag")],
    flag: Annotated[int, Field(description="Flag value: 0=none/remove, 1=red, 2=orange, 3=green, 4=blue, 5-7=custom user flags")],
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return set_flag_impl(card_ids=card_ids, flag=flag)
