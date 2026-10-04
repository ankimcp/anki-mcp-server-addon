"""Multi-action tool for tag management operations."""
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.add_tags import add_tags_impl
from .actions.remove_tags import remove_tags_impl
from .actions.replace_tags import replace_tags_impl
from .actions._replace_conflict import find_replace_conflict
from .actions.get_tags import get_tags_impl
from .actions.clear_unused_tags import clear_unused_tags_impl
from .actions.batch_tags import batch_tags_impl, _MAX_OPERATIONS

_BASE_DESCRIPTION = "Manage tags on notes"


class AddTagsParams(BaseModel):
    """Parameters for add_tags action."""
    _tool_description: ClassVar[str] = (
        "add_tags: Add tags to notes by note IDs. "
        "Tags: space-separated tag names (e.g., 'vocab grammar'). "
        "Returns added_count."
    )
    action: Literal["add_tags"]
    note_ids: list[int] = Field(description="Note IDs to add tags to")
    tags: str = Field(description="Space-separated tag names to add (e.g., 'vocab grammar')")


class RemoveTagsParams(BaseModel):
    """Parameters for remove_tags action."""
    _tool_description: ClassVar[str] = (
        "remove_tags: Remove tags from notes by note IDs. "
        "Tags: space-separated tag names to remove. "
        "Returns removed_count."
    )
    action: Literal["remove_tags"]
    note_ids: list[int] = Field(description="Note IDs to remove tags from")
    tags: str = Field(description="Space-separated tag names to remove (e.g., 'vocab grammar')")


class ReplaceTagsParams(BaseModel):
    """Parameters for replace_tags action."""
    _tool_description: ClassVar[str] = (
        "replace_tags: Replace a tag with another on specific notes. "
        "Adds new_tag then removes old_tag on the given notes. "
        "The removal matches case-insensitively and also strips old_tag's child tags "
        "(old_tag::...), so a new_tag equal to old_tag ignoring case, or a child of it, "
        "is rejected before anything is written. "
        "Returns added_count and removed_count."
    )
    action: Literal["replace_tags"]
    note_ids: list[int] = Field(description="Note IDs to replace tags on")
    old_tag: str = Field(description="Tag to remove")
    new_tag: str = Field(description="Tag to add in place of old_tag")


class GetTagsParams(BaseModel):
    """Parameters for get_tags action."""
    _tool_description: ClassVar[str] = (
        "get_tags: List all tags in the collection. "
        "Optional deck param scopes results to tags on notes with at least one "
        "card in that deck (subdecks included); omit for all collection tags. "
        "Returns tags array and count."
    )
    action: Literal["get_tags"]
    deck: str = Field(
        default="",
        description=(
            "Optional deck name to scope tags to notes with a card in this deck "
            "(subdecks included). Omit or leave empty for all collection tags."
        ),
    )


class ClearUnusedTagsParams(BaseModel):
    """Parameters for clear_unused_tags action."""
    _tool_description: ClassVar[str] = (
        "clear_unused_tags: Remove tags that are not used by any notes. "
        "No parameters needed. "
        "Returns cleared_count."
    )
    action: Literal["clear_unused_tags"]


class TagOperation(BaseModel):
    """A single tag operation within a batch."""
    type: Literal["add", "remove"] = Field(
        description="Operation type: 'add' to add tags, 'remove' to remove tags"
    )
    note_ids: list[int] = Field(description="Note IDs to apply this operation to")
    tags: str = Field(
        description="Space-separated tag names (e.g., 'vocab grammar')"
    )


class BatchTagsParams(BaseModel):
    """Parameters for batch_tags action."""
    _tool_description: ClassVar[str] = (
        "batch_tags: Apply multiple add/remove tag operations in a single call. "
        "Each operation specifies type ('add' or 'remove'), note_ids, and tags. "
        "Operations execute in order with partial success support. Max 50 operations. "
        "Returns per-operation results with affected_count, plus succeeded/failed totals."
    )
    action: Literal["batch_tags"]
    operations: list[TagOperation] = Field(
        description="List of tag operations. Each has: "
        "type ('add'/'remove'), note_ids (list of ints), tags (space-separated string). "
        "Executed in order."
    )


TagManagementParams = Annotated[
    Union[
        AddTagsParams, RemoveTagsParams, ReplaceTagsParams,
        GetTagsParams, ClearUnusedTagsParams, BatchTagsParams,
    ],
    Field(discriminator="action", description="The tag operation to perform and its arguments")
]


@Tool(
    "tag_management",
    _BASE_DESCRIPTION,  # Rebuilt dynamically at MCP registration from _tool_description ClassVars
    title="Tag Management",
    write=True,
    idempotent_hint=True,
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
            conflict = find_replace_conflict(params.old_tag, params.new_tag)
            if conflict == "same_tag":
                raise HandlerError(
                    "old_tag and new_tag must be different (Anki compares tags case-insensitively)",
                    hint="To change only the capitalisation, replace old_tag with a temporary "
                    "tag, then replace the temporary tag with new_tag.",
                    action=params.action,
                    old_tag=params.old_tag,
                    new_tag=params.new_tag,
                )
            if conflict == "child_tag":
                raise HandlerError(
                    "new_tag is a child of old_tag, and removing old_tag also removes its "
                    "child tags, so new_tag would be removed too",
                    hint="Replace old_tag with a temporary tag first, then replace the "
                    "temporary tag with new_tag.",
                    action=params.action,
                    old_tag=params.old_tag,
                    new_tag=params.new_tag,
                )
            return replace_tags_impl(
                note_ids=params.note_ids,
                old_tag=params.old_tag,
                new_tag=params.new_tag,
            )
        case "get_tags":
            return get_tags_impl(deck=params.deck)
        case "clear_unused_tags":
            return clear_unused_tags_impl()
        case "batch_tags":
            if not params.operations:
                raise HandlerError(
                    "operations is required and cannot be empty",
                    hint="Provide at least one operation with type, note_ids, and tags",
                    action=params.action,
                )
            if len(params.operations) > _MAX_OPERATIONS:
                raise HandlerError(
                    f"Too many operations: {len(params.operations)} "
                    f"(maximum is {_MAX_OPERATIONS})",
                    hint=f"Split into batches of {_MAX_OPERATIONS} or fewer.",
                    action=params.action,
                    requested=len(params.operations),
                    maximum=_MAX_OPERATIONS,
                )
            return batch_tags_impl(
                operations=[op.model_dump() for op in params.operations]
            )
        case _:
            raise HandlerError(f"Unknown action: {params.action}")
