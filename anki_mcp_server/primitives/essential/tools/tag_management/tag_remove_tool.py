"""Remove tags from notes."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.remove_tags import remove_tags_impl


@Tool(
    "tag_remove",
    "Remove tags from notes by note ID. tags is a space-separated list of tag names; "
    "removal is case-insensitive and also removes child tags (tag::...). "
    "Returns removed_count.",
    title="Remove Tags",
    write=True,
    idempotent_hint=True,
)
def tag_remove(
    note_ids: Annotated[list[int], Field(description="Note IDs to remove tags from")],
    tags: Annotated[
        str, Field(description="Space-separated tag names to remove (e.g., 'vocab grammar')")
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
    return remove_tags_impl(note_ids=note_ids, tags=tags)
