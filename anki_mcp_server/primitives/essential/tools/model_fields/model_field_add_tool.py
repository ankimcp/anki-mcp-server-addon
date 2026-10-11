"""Add a field to an existing note type (model)."""
from typing import Annotated, Any, Optional

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import get_col
from .....schema_state import FULL_SYNC_FLAG_DOC, attach_full_sync_flag

from .actions._helpers import SCHEMA_SENTENCE
from .actions.add import add_field_impl

# Adding a field changes field ORDINALS, which is what makes rslib call
# set_schema_modified (schemachange.rs update_notes_for_changed_fields ->
# ords_changed), so this tool always reports will_force_full_sync=true.


@Tool(
    "model_field_add",
    "Add a new field to a note type, optionally at a 0-based index (default: "
    "appended at the end). "
    + SCHEMA_SENTENCE
    + " "
    + FULL_SYNC_FLAG_DOC
    + " This tool therefore always reports true.",
    title="Add Note Type Field",
    write=True,
    idempotent_hint=True,
)
def model_field_add(
    model_name: Annotated[str, Field(description="Name of the note type (model) to modify")],
    field_name: Annotated[str, Field(description="Name of the new field to add")],
    index: Annotated[
        Optional[int],
        Field(
            description="0-based position for the new field (0..field_count, inclusive). "
                        "Omit to append at the end.",
        ),
    ] = None,
) -> dict[str, Any]:
    # Resolved BEFORE the write, not at the attach site below. schema_state is
    # deliberately fail-safe -- a post-write read failure degrades to True
    # rather than raising -- but a get_col() raise after a successful write
    # would sail past that guard and turn a completed change into an error
    # result. Acquiring it up front means the only failure point is pre-write,
    # where raising is correct. The impl re-reads the same open collection.
    col = get_col()

    result = add_field_impl(
        model_name=model_name,
        field_name=field_name,
        index=index,
    )

    return attach_full_sync_flag(result, col)
