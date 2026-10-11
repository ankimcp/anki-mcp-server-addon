"""Rename a field of an existing note type (model)."""
from typing import Annotated, Any

from pydantic import Field

from .....tool_decorator import Tool
from .....handler_wrappers import get_col
from .....schema_state import FULL_SYNC_FLAG_DOC, attach_full_sync_flag

from .actions.rename import rename_field_impl

# Unlike add/remove/reposition, a rename does NOT dirty the schema: rslib only
# calls set_schema_modified when field ORDINALS change (schemachange.rs
# update_notes_for_changed_fields -> ords_changed) or the sort field moves.
# pylib's rename_field just rewrites field["name"], leaving ordinals alone, so
# will_force_full_sync is true here only if the collection was already dirty.


@Tool(
    "model_field_rename",
    "Rename a field of a note type, keeping its content. Anki rewrites references "
    "to the field in the card templates to the new name. "
    "Renaming does not itself modify the schema. "
    + FULL_SYNC_FLAG_DOC
    + " This tool reports true only if the collection was already dirty.",
    title="Rename Note Type Field",
    write=True,
    idempotent_hint=True,
)
def model_field_rename(
    model_name: Annotated[str, Field(description="Name of the note type (model) to modify")],
    field_name: Annotated[str, Field(description="Current name of the field to rename")],
    new_name: Annotated[str, Field(description="New name for the field")],
) -> dict[str, Any]:
    # Resolved BEFORE the write, not at the attach site below. schema_state is
    # deliberately fail-safe -- a post-write read failure degrades to True
    # rather than raising -- but a get_col() raise after a successful write
    # would sail past that guard and turn a completed change into an error
    # result. Acquiring it up front means the only failure point is pre-write,
    # where raising is correct. The impl re-reads the same open collection.
    col = get_col()

    result = rename_field_impl(
        model_name=model_name,
        field_name=field_name,
        new_name=new_name,
    )

    return attach_full_sync_flag(result, col)
