from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "updateNoteFields",
    "Update the fields of an existing note. Supports HTML content in fields and preserves CSS styling. Note: media file attachment (audio/picture) is not yet supported. WARNING: Do not view the note in Anki browser while updating, or the fields will not update properly. Close the browser or switch to a different note before updating. IMPORTANT: Only update notes that the user explicitly asked to modify.",
    write=True,
)
def update_note_fields(id: int, fields: dict[str, str]) -> dict[str, Any]:
    col = get_col()

    if not fields:
        raise HandlerError(
            "No fields provided for update. Provide at least one field to update.",
            hint="Include fields dict with field names and values to update",
        )

    try:
        anki_note = col.get_note(id)
    except KeyError:
        raise HandlerError(
            f"Note not found with ID {id}. The note ID is invalid or the note has been deleted.",
            hint="Use findNotes to get valid note IDs.",
        )
    except Exception as e:
        logger.error(f"Unexpected error getting note {id}: {e}", exc_info=True)
        raise HandlerError(f"Failed to retrieve note {id}: {str(e)}")

    model_name = anki_note.note_type()["name"]
    existing_fields = list(anki_note.keys())

    invalid_fields = [field for field in fields.keys() if field not in existing_fields]
    if invalid_fields:
        raise HandlerError(
            f'Invalid fields for model "{model_name}": {", ".join(invalid_fields)}. '
            f'Valid fields are: {", ".join(existing_fields)}.',
            hint="Use modelFieldNames to see valid fields for this model.",
        )

    for field_name, field_value in fields.items():
        anki_note[field_name] = field_value

    col.update_note(anki_note)

    field_count = len(fields)
    return {
        "noteId": id,
        "updatedFields": list(fields.keys()),
        "fieldCount": field_count,
        "modelName": model_name,
        "message": f"Successfully updated {field_count} field{'s' if field_count != 1 else ''} in note",
        "cssNote": "HTML content is preserved. Model CSS styling remains unchanged.",
        "warning": "If changes don't appear, ensure the note wasn't open in Anki browser during update.",
        "hint": "Use notesInfo to verify the changes or findNotes to locate other notes to update.",
    }
