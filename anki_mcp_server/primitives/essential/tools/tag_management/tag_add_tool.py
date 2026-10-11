"""Add tags to notes."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.add_tags import add_tags_impl


@Tool(
    "tag_add",
    "Add tags to notes by note ID. tags is a space-separated list of tag names "
    "(e.g. 'vocab grammar'). Returns added_count.",
    title="Add Tags",
    write=True,
    destructive_hint=False,
    idempotent_hint=True,
)
def tag_add(
    note_ids: Annotated[list[int], Field(description="Note IDs to add tags to")],
    tags: Annotated[
        str, Field(description="Space-separated tag names to add (e.g., 'vocab grammar')")
    ],
) -> dict[str, Any]:
    if not note_ids:
        raise HandlerError(
            "note_ids is required and cannot be empty",
            hint="Provide at least one note ID",
        )
    if not tags.strip():
        raise HandlerError(
            "tags is required and cannot be empty",
            hint="Provide space-separated tag names (e.g., 'vocab grammar')",
        )
    return add_tags_impl(note_ids=note_ids, tags=tags)
