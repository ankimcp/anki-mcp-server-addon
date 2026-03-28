"""Notes info tool - get detailed information about specific notes."""
from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "notes_info",
    "Get detailed information about specific notes including all fields, tags, model info, and CSS styling. "
    "Use this after find_notes to get complete note data. Includes CSS for proper rendering awareness. "
    "Use include_fields to return only specific fields, or exclude_fields to omit fields - "
    "useful for reducing response size in bulk queries. If both are provided, include_fields takes priority.",
)
def notes_info(
    notes: list[int],
    include_fields: list[str] | None = None,
    exclude_fields: list[str] | None = None,
) -> dict[str, Any]:
    if not notes:
        raise HandlerError(
            "notes parameter cannot be empty",
            code="validation_error",
        )

    if len(notes) > 100:
        raise HandlerError(
            f"Maximum 100 notes at once (requested: {len(notes)})",
            hint="Split your request into smaller batches",
            code="limit_exceeded",
        )

    col = get_col()

    include_set = set(include_fields) if include_fields is not None else None
    exclude_set = set(exclude_fields) if exclude_fields is not None else None

    notes_data = []
    for note_id in notes:
        try:
            note = col.get_note(note_id)

            note_type = note.note_type()
            if note_type is None:
                logger.warning(f"Note {note_id} has no associated model (orphaned)")
                continue

            field_descriptions = {
                fld["name"]: fld.get("description", "")
                for fld in note_type["flds"]
            }

            fields_dict = {}
            for i, (field_name, field_value) in enumerate(note.items()):
                fields_dict[field_name] = {
                    "value": field_value,
                    "order": i,
                    "description": field_descriptions.get(field_name, ""),
                }

            # Apply field filtering: include_fields takes priority over exclude_fields
            if include_set is not None:
                fields_dict = {k: v for k, v in fields_dict.items() if k in include_set}
            elif exclude_set is not None:
                fields_dict = {k: v for k, v in fields_dict.items() if k not in exclude_set}

            card_ids = [card.id for card in note.cards()]

            note_info = {
                "noteId": note_id,
                "modelName": note_type["name"],
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
            "Fields may contain HTML. Use update_note_fields to modify content. "
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
            "Use model_styling tool to get CSS for specific models."
        ),
        "hint": hint,
    }
