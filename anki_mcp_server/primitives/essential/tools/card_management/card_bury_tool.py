"""Manually bury cards until the next day."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.bury import bury_impl


@Tool(
    "card_bury",
    "Manually bury cards to hide them until the next day. "
    "Works with any card type. "
    "Returns buried_count.",
    title="Bury Cards",
    write=True,
    idempotent_hint=True,
)
def card_bury(
    card_ids: Annotated[list[int], Field(description="Card IDs to bury")],
) -> dict[str, Any]:
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
        )
    return bury_impl(card_ids=card_ids)
