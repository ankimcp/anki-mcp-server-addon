"""Apply several add/remove tag operations in one call."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.batch_tags import batch_tags_impl, _MAX_OPERATIONS


class TagOperation(BaseModel):
    """A single tag operation within a batch."""
    type: Literal["add", "remove"] = Field(
        description="Operation type: 'add' to add tags, 'remove' to remove tags"
    )
    note_ids: list[int] = Field(description="Note IDs to apply this operation to")
    tags: str = Field(
        description="Space-separated tag names (e.g., 'vocab grammar')"
    )


@Tool(
    "tag_batch",
    "Apply multiple add/remove tag operations in a single call. "
    "Each operation specifies type ('add' or 'remove'), note_ids, and tags. "
    "Operations execute in order with partial success support. Max 50 operations. "
    "Returns per-operation results with affected_count, plus succeeded/failed totals.",
    title="Batch Tag Operations",
    write=True,
    idempotent_hint=True,
)
def tag_batch(
    operations: Annotated[
        list[TagOperation],
        Field(
            description="List of tag operations. Each has: "
            "type ('add'/'remove'), note_ids (list of ints), tags (space-separated string). "
            "Executed in order."
        ),
    ],
) -> dict[str, Any]:
    if not operations:
        raise HandlerError(
            "operations is required and cannot be empty",
            hint="Provide at least one operation with type, note_ids, and tags",
        )
    if len(operations) > _MAX_OPERATIONS:
        raise HandlerError(
            f"Too many operations: {len(operations)} "
            f"(maximum is {_MAX_OPERATIONS})",
            hint=f"Split into batches of {_MAX_OPERATIONS} or fewer.",
            requested=len(operations),
            maximum=_MAX_OPERATIONS,
        )
    return batch_tags_impl(operations=[op.model_dump() for op in operations])
