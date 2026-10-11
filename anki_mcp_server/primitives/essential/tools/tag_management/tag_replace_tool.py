"""Replace one tag with another on notes."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.replace_tags import replace_tags_impl
from .actions._replace_conflict import find_replace_conflict


@Tool(
    "tag_replace",
    "Replace a tag with another on specific notes. "
    "Adds new_tag then removes old_tag on the given notes. "
    "The removal matches case-insensitively and also strips old_tag's child tags "
    "(old_tag::...), so a new_tag equal to old_tag ignoring case, or a child of it, "
    "is rejected before anything is written. "
    "Returns added_count and removed_count.",
    title="Replace Tag",
    write=True,
    idempotent_hint=True,
)
def tag_replace(
    note_ids: Annotated[list[int], Field(description="Note IDs to replace tags on")],
    old_tag: Annotated[str, Field(description="Tag to remove")],
    new_tag: Annotated[str, Field(description="Tag to add in place of old_tag")],
) -> dict[str, Any]:
    if not note_ids:
        raise HandlerError(
            "note_ids is required and cannot be empty",
            hint="Provide at least one note ID",
        )
    if not old_tag.strip():
        raise HandlerError(
            "old_tag is required and cannot be empty",
            hint="Provide the tag to replace",
        )
    if not new_tag.strip():
        raise HandlerError(
            "new_tag is required and cannot be empty",
            hint="Provide the replacement tag",
        )
    conflict = find_replace_conflict(old_tag, new_tag)
    if conflict == "same_tag":
        raise HandlerError(
            "old_tag and new_tag must be different (Anki compares tags case-insensitively)",
            hint="To change only the capitalisation, replace old_tag with a temporary "
            "tag, then replace the temporary tag with new_tag.",
            old_tag=old_tag,
            new_tag=new_tag,
        )
    if conflict == "child_tag":
        raise HandlerError(
            "new_tag is a child of old_tag, and removing old_tag also removes its "
            "child tags, so new_tag would be removed too",
            hint="Replace old_tag with a temporary tag first, then replace the "
            "temporary tag with new_tag.",
            old_tag=old_tag,
            new_tag=new_tag,
        )
    return replace_tags_impl(note_ids=note_ids, old_tag=old_tag, new_tag=new_tag)
