"""Reposition new cards in the new-card queue."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.reposition import reposition_impl


@Tool(
    "card_reposition",
    "Reposition new cards in the new-card queue (set learning order). "
    "Only new cards (queue=0) are affected; other cards are silently skipped. "
    "Returns repositioned count.",
    title="Reposition New Cards",
    write=True,
)
def card_reposition(
    card_ids: Annotated[list[int], Field(description="Card IDs to reposition")],
    starting_from: Annotated[int, Field(description="Starting position (0-based)")] = 0,
    step_size: Annotated[int, Field(description="Increment between cards")] = 1,
    randomize: Annotated[bool, Field(description="Shuffle before assigning positions")] = False,
    shift_existing: Annotated[bool, Field(description="Push other cards forward")] = False,
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return reposition_impl(
        card_ids=card_ids,
        starting_from=starting_from,
        step_size=step_size,
        randomize=randomize,
        shift_existing=shift_existing,
    )
