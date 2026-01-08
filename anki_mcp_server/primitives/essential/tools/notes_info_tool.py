"""Notes info tool - get detailed information about specific notes."""
from typing import Any
import logging

from ....tool_decorator import Tool, ToolError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "notesInfo",
    "Get detailed information about specific notes including all fields, tags, model info, and CSS styling. "
    "Use this after findNotes to get complete note data. Includes CSS for proper rendering awareness.",
)
def notes_info(notes: list[int]) -> dict[str, Any]:
    if not notes:
        raise ToolError("notes parameter cannot be empty")

    if len(notes) > 100:
        raise ToolError(
            f"Maximum 100 notes at once (requested: {len(notes)})",
            hint="Split your request into smaller batches",
        )

    col = get_col()

    notes_data = []
    for note_id in notes:
        try:
            note = col.get_note(note_id)

            fields_dict = {}
            for i, (field_name, field_value) in enumerate(note.items()):
                fields_dict[field_name] = {"value": field_value, "order": i}

            card_ids = [card.id for card in note.cards()]

            note_info = {
                "noteId": note_id,
                "modelName": note.note_type()["name"],
                "tags": note.tags,
                "fields": fields_dict,
                "cards": card_ids,
                "mod": note.mod,
            }
            notes_data.append(note_info)
        except KeyError as e:
            logger.warning(f"Note {note_id} not found: {e}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error retrieving note {note_id}: {e}", exc_info=True)
            continue

    valid_count = len(notes_data)
    not_found_count = len(notes) - valid_count

    unique_models = list(set(note["modelName"] for note in notes_data))

    if not_found_count > 0:
        message = (
            f"Retrieved {valid_count} note(s). "
            f"{not_found_count} note(s) not found (possibly deleted)."
        )
    else:
        message = f"Successfully retrieved information for {valid_count} note(s)"

    if valid_count > 0:
        hint = (
            "Fields may contain HTML. Use updateNoteFields to modify content. "
            "Do not view notes in Anki browser while updating."
        )
    else:
        hint = "No valid notes found. They may have been deleted."

    return {
        "notes": notes_data,
        "count": valid_count,
        "notFound": not_found_count,
        "requestedIds": notes,
        "message": message,
        "models": unique_models,
        "cssNote": (
            "Each note model has its own CSS styling. "
            "Use modelStyling tool to get CSS for specific models."
        ),
        "hint": hint,
    }
