"""Update notes tool - batch-update fields of multiple notes."""

from typing import Any
import logging

from pydantic import BaseModel, Field

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)

_MAX_NOTES = 100


class NoteUpdateEntry(BaseModel):
    """A single note to update in a batch."""

    id: int = Field(description="The note ID to update")
    fields: dict[str, str] = Field(
        description="Field values to update as key-value pairs "
        '(e.g., {"Front": "new question", "Back": "new answer"}). '
        "Only specified fields are changed; partial updates are OK."
    )


@Tool(
    "update_notes",
    "Update the fields of multiple existing notes in a single batch. Up to 100 notes. "
    "Each entry must include the note ID and the fields to update (partial updates are fine - "
    "only specified fields are changed). Failures for individual notes do not affect others. "
    "IMPORTANT: Only update notes that the user explicitly asked to modify. "
    "Returns summary counts (updated, failed) and a per-note results array with status and note_id.",
    write=True,
)
def update_notes(notes: list[NoteUpdateEntry]) -> dict[str, Any]:
    col = get_col()

    # --- Batch-level validation ---
    if not notes:
        raise HandlerError(
            "Notes array is empty",
            hint="Provide at least one note entry with id and fields.",
            code="validation_error",
        )

    if len(notes) > _MAX_NOTES:
        raise HandlerError(
            f"Too many notes: {len(notes)} (maximum is {_MAX_NOTES})",
            hint=f"Split your request into batches of {_MAX_NOTES} or fewer.",
            code="limit_exceeded",
            requested=len(notes),
            maximum=_MAX_NOTES,
        )

    results: list[dict[str, Any]] = []

    for i, entry in enumerate(notes):
        note_id = entry.id
        fields = entry.fields

        # Validate per-entry structure
        if not isinstance(note_id, int) or note_id <= 0:
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Invalid note ID: {note_id}",
            })
            continue

        if not fields or len(fields) == 0:
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": "Empty fields dict - provide at least one field to update",
            })
            continue

        # Try to get and validate the note
        try:
            anki_note = col.get_note(note_id)
        except KeyError:
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Note not found with ID {note_id}. The note ID is invalid or the note has been deleted.",
            })
            continue
        except Exception as e:
            logger.error("Unexpected error getting note %d: %s", note_id, e, exc_info=True)
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Failed to retrieve note {note_id}: {str(e)}",
            })
            continue

        # Validate field names
        existing_fields = list(anki_note.keys())
        invalid_fields = [f for f in fields if f not in existing_fields]
        if invalid_fields:
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Invalid fields for model \"{anki_note.note_type()['name']}\": {', '.join(invalid_fields)}. "
                         f"Valid fields are: {', '.join(existing_fields)}.",
            })
            continue

        # Apply field updates
        try:
            for field_name, field_value in fields.items():
                anki_note[field_name] = field_value
            col.update_note(anki_note)
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "updated",
                "updated_fields": list(fields.keys()),
            })
        except Exception as e:
            logger.error("Failed to update note %d: %s", note_id, e, exc_info=True)
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": str(e),
            })

    # --- Build response ---
    results.sort(key=lambda r: r["index"])

    updated = sum(1 for r in results if r["status"] == "updated")
    failed = sum(1 for r in results if r["status"] == "failed")
    total = len(notes)

    parts = []
    if failed:
        parts.append(f"{failed} failed")
    detail = f" ({', '.join(parts)})" if parts else ""

    return {
        "updated": updated,
        "failed": failed,
        "total_requested": total,
        "results": results,
        "message": f"Updated {updated} of {total} notes{detail}",
        "hint": "Use notes_info to verify the changes, model_field_names to check valid fields for a model, "
                "and find_notes to locate other notes by query.",
    }
