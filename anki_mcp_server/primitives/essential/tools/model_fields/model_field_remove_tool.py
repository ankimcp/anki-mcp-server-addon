"""Remove a field (and its content) from an existing note type (model)."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import get_col
from .....schema_state import FULL_SYNC_FLAG_DOC, attach_full_sync_flag

from .actions._helpers import SCHEMA_SENTENCE
from .actions.remove import remove_field_impl

# Removing a field changes field ORDINALS, which is what makes rslib call
# set_schema_modified (schemachange.rs update_notes_for_changed_fields ->
# ords_changed), so this tool always reports will_force_full_sync=true.
#
# destructive=True: irreversible bulk data loss, so the tool stays hidden from
# tools/list until the operator names it in enabled_destructive_tools.


@Tool(
    "model_field_remove",
    "Permanently delete a field and its content from every note of the note type. "
    "Irreversible bulk data loss -- the content is recoverable only from a backup. "
    "A note type must keep at least one field. "
    + SCHEMA_SENTENCE
    + " "
    + FULL_SYNC_FLAG_DOC
    + " This tool therefore always reports true.",
    title="Remove Note Type Field",
    write=True,
    destructive=True,
    idempotent_hint=True,
)
def model_field_remove(
    model_name: Annotated[str, Field(description="Name of the note type (model) to modify")],
    field_name: Annotated[str, Field(description="Name of the field to remove")],
) -> dict[str, Any]:
    # Resolved BEFORE the write, not at the attach site below. schema_state is
    # deliberately fail-safe -- a post-write read failure degrades to True
    # rather than raising -- but a get_col() raise after a successful write
    # would sail past that guard and turn a completed change into an error
    # result. Acquiring it up front means the only failure point is pre-write,
    # where raising is correct. The impl re-reads the same open collection.
    col = get_col()

    result = remove_field_impl(
        model_name=model_name,
        field_name=field_name,
    )

    return attach_full_sync_flag(result, col)
