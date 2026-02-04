from typing import Any
import logging

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "updateNoteFields",
    "Update the fields of an existing note. Supports HTML content in fields and preserves CSS styling. WARNING: Do not view the note in Anki browser while updating, or the fields will not update properly. Close the browser or switch to a different note before updating. IMPORTANT: Only update notes that the user explicitly asked to modify.",
    write=True,
)
def update_note_fields(note: dict[str, Any]) -> dict[str, Any]:
    col = get_col()

    note_id = note.get("id")
    fields_to_update = note.get("fields", {})
    audio = note.get("audio")
    picture = note.get("picture")

    if not note_id:
        raise HandlerError("note.id is required")

    if not fields_to_update:
        raise HandlerError(
            "No fields provided for update. Provide at least one field to update.",
            hint="Include fields dict with field names and values to update",
        )

    if not isinstance(fields_to_update, dict):
        raise HandlerError("note.fields must be a dictionary")

    try:
        anki_note = col.get_note(note_id)
    except KeyError:
        raise HandlerError(
            f"Note not found with ID {note_id}. The note ID is invalid or the note has been deleted.",
            hint="Use findNotes to get valid note IDs.",
        )
    except Exception as e:
        logger.error(f"Unexpected error getting note {note_id}: {e}", exc_info=True)
        raise HandlerError(f"Failed to retrieve note {note_id}: {str(e)}")

    model_name = anki_note.note_type()["name"]
    existing_fields = list(anki_note.keys())

    invalid_fields = [field for field in fields_to_update.keys() if field not in existing_fields]
    if invalid_fields:
        raise HandlerError(
            f'Invalid fields for model "{model_name}": {", ".join(invalid_fields)}. '
            f'Valid fields are: {", ".join(existing_fields)}.',
            hint="Use modelFieldNames to see valid fields for this model.",
        )

    for field_name, field_value in fields_to_update.items():
        anki_note[field_name] = field_value

    # TODO: Handle media files (audio and picture)
    # This requires downloading from URLs and adding to Anki's media collection
    if audio or picture:
        pass

    col.update_note(anki_note)

    field_count = len(fields_to_update)
    return {
        "noteId": note_id,
        "updatedFields": list(fields_to_update.keys()),
        "fieldCount": field_count,
        "modelName": model_name,
        "message": f"Successfully updated {field_count} field{'s' if field_count != 1 else ''} in note",
        "cssNote": "HTML content is preserved. Model CSS styling remains unchanged.",
        "warning": "If changes don't appear, ensure the note wasn't open in Anki browser during update.",
        "hint": "Use notesInfo to verify the changes or findNotes to locate other notes to update.",
    }
