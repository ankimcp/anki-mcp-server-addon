"""Multi-action tool for tag management operations."""
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.add_tags import add_tags_impl
from .actions.remove_tags import remove_tags_impl
from .actions.replace_tags import replace_tags_impl
from .actions.get_tags import get_tags_impl
from .actions.clear_unused_tags import clear_unused_tags_impl


class AddTagsParams(BaseModel):
    """Parameters for addTags action."""
    action: Literal["addTags"]
    note_ids: list[int] = Field(description="Note IDs to add tags to")
    tags: str = Field(description="Space-separated tag names to add (e.g., 'vocab grammar')")


class RemoveTagsParams(BaseModel):
    """Parameters for removeTags action."""
    action: Literal["removeTags"]
    note_ids: list[int] = Field(description="Note IDs to remove tags from")
    tags: str = Field(description="Space-separated tag names to remove (e.g., 'vocab grammar')")


class ReplaceTagsParams(BaseModel):
    """Parameters for replaceTags action."""
    action: Literal["replaceTags"]
    note_ids: list[int] = Field(description="Note IDs to replace tags on")
    old_tag: str = Field(description="Tag to remove")
    new_tag: str = Field(description="Tag to add in place of old_tag")


class GetTagsParams(BaseModel):
    """Parameters for getTags action."""
    action: Literal["getTags"]


class ClearUnusedTagsParams(BaseModel):
    """Parameters for clearUnusedTags action."""
    action: Literal["clearUnusedTags"]


TagManagementParams = Annotated[
    Union[AddTagsParams, RemoveTagsParams, ReplaceTagsParams, GetTagsParams, ClearUnusedTagsParams],
    Field(discriminator="action")
]


@Tool(
    "tag_management",
    """Manage tags on notes with five actions:

    - addTags: Add tags to notes by note IDs.
      tags: Space-separated tag names (e.g., 'vocab grammar').

    - removeTags: Remove tags from notes by note IDs.
      tags: Space-separated tag names to remove.

    - replaceTags: Replace a tag with another on specific notes.
      Adds new_tag then removes old_tag on the given notes.

    - getTags: List all tags in the collection.
      No parameters needed.

    - clearUnusedTags: Remove tags that are not used by any notes.
      No parameters needed.""",
    write=True,
)
def tag_management(params: TagManagementParams) -> dict[str, Any]:
    """Dispatcher for tag management operations."""
    match params.action:
        case "addTags":
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
        case "removeTags":
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
        case "replaceTags":
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
        case "getTags":
            return get_tags_impl()
        case "clearUnusedTags":
            return clear_unused_tags_impl()
        case _:
            raise HandlerError(f"Unknown action: {params.action}")
