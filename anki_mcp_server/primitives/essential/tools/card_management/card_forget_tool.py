"""Reset cards back to the new state."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.forget import forget_impl


@Tool(
    "card_forget",
    "Reset cards back to the new state, forgetting their scheduling. "
    "restore_position (default true) restores the original new-card position; "
    "reset_counts (default false) also zeroes review and lapse counts. "
    "Returns affected_count and card_ids.",
    title="Forget Cards",
    write=True,
)
def card_forget(
    card_ids: Annotated[list[int], Field(description="Card IDs to reset to new state")],
    restore_position: Annotated[bool, Field(description="Restore original new-card position")] = True,
    reset_counts: Annotated[bool, Field(description="Reset review and lapse counts")] = False,
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return forget_impl(
        card_ids=card_ids,
        restore_position=restore_position,
        reset_counts=reset_counts,
    )
