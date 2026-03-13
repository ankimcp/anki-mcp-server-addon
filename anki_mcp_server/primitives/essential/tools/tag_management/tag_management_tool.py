"""Multi-action tool for tag management operations."""
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.add_tags import add_tags_impl
from .actions.remove_tags import remove_tags_impl
from .actions.replace_tags import replace_tags_impl
from .actions.get_tags import get_tags_impl
from .actions.clear_unused_tags import clear_unused_tags_impl

_BASE_DESCRIPTION = "Manage tags on notes"


class AddTagsParams(BaseModel):
    """Parameters for add_tags action."""
    _tool_description: ClassVar[str] = (
        "add_tags: Add tags to notes by note IDs. "
        "Tags: space-separated tag names (e.g., 'vocab grammar')."
    )
    action: Literal["add_tags"]
    note_ids: list[int] = Field(description="Note IDs to add tags to")
    tags: str = Field(description="Space-separated tag names to add (e.g., 'vocab grammar')")


class RemoveTagsParams(BaseModel):
    """Parameters for remove_tags action."""
    _tool_description: ClassVar[str] = (
        "remove_tags: Remove tags from notes by note IDs. "
        "Tags: space-separated tag names to remove."
    )
    action: Literal["remove_tags"]
    note_ids: list[int] = Field(description="Note IDs to remove tags from")
    tags: str = Field(description="Space-separated tag names to remove (e.g., 'vocab grammar')")


class ReplaceTagsParams(BaseModel):
    """Parameters for replace_tags action."""
    _tool_description: ClassVar[str] = (
        "replace_tags: Replace a tag with another on specific notes. "
        "Adds new_tag then removes old_tag on the given notes."
    )
    action: Literal["replace_tags"]
    note_ids: list[int] = Field(description="Note IDs to replace tags on")
    old_tag: str = Field(description="Tag to remove")
    new_tag: str = Field(description="Tag to add in place of old_tag")


class GetTagsParams(BaseModel):
    """Parameters for get_tags action."""
    _tool_description: ClassVar[str] = (
        "get_tags: List all tags in the collection. "
        "No parameters needed."
    )
    action: Literal["get_tags"]


class ClearUnusedTagsParams(BaseModel):
    """Parameters for clear_unused_tags action."""
    _tool_description: ClassVar[str] = (
        "clear_unused_tags: Remove tags that are not used by any notes. "
        "No parameters needed."
    )
    action: Literal["clear_unused_tags"]


TagManagementParams = Annotated[
    Union[AddTagsParams, RemoveTagsParams, ReplaceTagsParams, GetTagsParams, ClearUnusedTagsParams],
    Field(discriminator="action")
]


@Tool(
    "tag_management",
    """Manage tags on notes with five actions:

    - add_tags: Add tags to notes by note IDs.
      tags: Space-separated tag names (e.g., 'vocab grammar').

    - remove_tags: Remove tags from notes by note IDs.
      tags: Space-separated tag names to remove.

    - replace_tags: Replace a tag with another on specific notes.
      Adds new_tag then removes old_tag on the given notes.

    - get_tags: List all tags in the collection.
      No parameters needed.

    - clear_unused_tags: Remove tags that are not used by any notes.
      No parameters needed.""",
    write=True,
)
def tag_management(params: TagManagementParams) -> dict[str, Any]:
    """Dispatcher for tag management operations."""
    match params.action:
        case "add_tags":
            if not params.note_ids:
                raise HandlerError(
                    "note_ids is required and cannot be empty",
                    hint="Provide at least one note ID",
                    action=params.action,
                )
            if not params.tags.strip():
                raise HandlerError(
                    "tags is required and cannot be empty",
                    hint="Provide space-separated tag names (e.g., 'vocab grammar')",
                    action=params.action,
                )
            return add_tags_impl(note_ids=params.note_ids, tags=params.tags)
        case "remove_tags":
            if not params.note_ids:
                raise HandlerError(
                    "note_ids is required and cannot be empty",
                    hint="Provide at least one note ID",
                    action=params.action,
                )
            if not params.tags.strip():
                raise HandlerError(
                    "tags is required and cannot be empty",
                    hint="Provide space-separated tag names (e.g., 'vocab grammar')",
                    action=params.action,
                )
            return remove_tags_impl(note_ids=params.note_ids, tags=params.tags)
        case "replace_tags":
            if not params.note_ids:
                raise HandlerError(
                    "note_ids is required and cannot be empty",
                    hint="Provide at least one note ID",
                    action=params.action,
                )
            if not params.old_tag.strip():
                raise HandlerError(
                    "old_tag is required and cannot be empty",
                    hint="Provide the tag to replace",
                    action=params.action,
                )
            if not params.new_tag.strip():
                raise HandlerError(
                    "new_tag is required and cannot be empty",
                    hint="Provide the replacement tag",
                    action=params.action,
                )
            if params.old_tag.strip() == params.new_tag.strip():
                raise HandlerError(
                    "old_tag and new_tag must be different",
                    hint="Provide different tag names for replacement",
                    action=params.action,
                )
            return replace_tags_impl(
                note_ids=params.note_ids,
                old_tag=params.old_tag,
                new_tag=params.new_tag,
            )
        case "get_tags":
            return get_tags_impl()
        case "clear_unused_tags":
            return clear_unused_tags_impl()
        case _:
            raise HandlerError(f"Unknown action: {params.action}")
