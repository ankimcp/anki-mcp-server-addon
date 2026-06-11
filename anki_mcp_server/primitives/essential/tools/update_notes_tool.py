"""Update notes tool - batch-update fields of multiple notes."""

from typing import Any
import logging

from pydantic import BaseModel, Field

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ....config import get_max_notes_per_batch

logger = logging.getLogger(__name__)


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
    "Update the fields of multiple existing notes in a single batch. "
    "Each entry must include the note ID and the fields to update (partial updates are fine - "
    "only specified fields are changed). Failures for individual notes do not affect others. "
    "IMPORTANT: Only update notes that the user explicitly asked to modify. "
    "Returns summary counts (updated, failed) and a per-note results array with "
    "retry hints for recoverable failures.",
    write=True,
)
def update_notes(notes: list[NoteUpdateEntry]) -> dict[str, Any]:
    from anki.errors import NotFoundError

    col = get_col()
    max_notes = get_max_notes_per_batch()

    # --- Batch-level validation ---
    if not notes:
        raise HandlerError(
            "Notes array is empty",
            hint="Provide at least one note entry with id and fields.",
            code="validation_error",
        )

    if len(notes) > max_notes:
        raise HandlerError(
            f"Too many notes: {len(notes)} (maximum is {max_notes})",
            hint=f"Split your request into batches of {max_notes} or fewer. "
                 f"You can increase the limit via the 'max_notes_per_batch' addon config option.",
            code="limit_exceeded",
            requested=len(notes),
            maximum=max_notes,
        )

    results: list[dict[str, Any]] = []
    valid_indices: list[int] = []
    valid_notes: list = []
    valid_field_names: list[list[str]] = []

    for i, entry in enumerate(notes):
        note_id = entry.id
        fields = entry.fields

        # Validate per-entry structure (not retryable — AI must fix the input)
        if not isinstance(note_id, int) or note_id <= 0:
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Invalid note ID: {note_id}",
                "retryable": False,
                "retry_hint": "Provide a valid positive integer note ID.",
            })
            continue

        if not fields or len(fields) == 0:
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": "Empty fields dict - provide at least one field to update",
                "retryable": False,
                "retry_hint": "Include a non-empty fields dict with field name/value pairs.",
            })
            continue

        # Try to get and validate the note
        try:
            anki_note = col.get_note(note_id)
        # Modern Anki raises anki.errors.NotFoundError for missing notes;
        # KeyError is kept for backward compatibility with older versions.
        except (NotFoundError, KeyError):
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Note not found with ID {note_id}. The note ID is invalid or the note has been deleted.",
                "retryable": False,
                "retry_hint": "Use find_notes to locate the correct note ID, or skip this note.",
            })
            continue
        except Exception as e:
            logger.error("Unexpected error getting note %d: %s", note_id, e, exc_info=True)
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Failed to retrieve note {note_id}: {str(e)}",
                "retryable": True,
                "retry_hint": "This may be a transient error. Retry this specific note with the same ID and fields.",
            })
            continue

        # Validate field names — retryable because we tell the AI exactly what's wrong
        model_name = anki_note.note_type()["name"]
        existing_fields = list(anki_note.keys())
        invalid_fields = [f for f in fields if f not in existing_fields]
        if invalid_fields:
            results.append({
                "index": i,
                "note_id": note_id,
                "status": "failed",
                "error": f"Invalid fields for model \"{model_name}\": {', '.join(invalid_fields)}.",
                "retryable": True,
                "retry_hint": f"Model \"{model_name}\" valid fields are: {', '.join(existing_fields)}. "
                             f"Replace {', '.join(invalid_fields)} with valid field names and retry.",
            })
            continue

        # Apply field updates to the note object (not yet persisted)
        for field_name, field_value in fields.items():
            anki_note[field_name] = field_value

        valid_indices.append(i)
        valid_notes.append(anki_note)
        valid_field_names.append(list(fields.keys()))

    # --- Batch update via native API (single undo step) ---
    if valid_notes:
        try:
            col.update_notes(valid_notes)
            for idx, field_names in zip(valid_indices, valid_field_names):
                results.append({
                    "index": idx,
                    "note_id": notes[idx].id,
                    "status": "updated",
                    "updated_fields": field_names,
                })
        except Exception as e:
            logger.error("col.update_notes() batch failed: %s", e, exc_info=True)
            for idx in valid_indices:
                results.append({
                    "index": idx,
                    "note_id": notes[idx].id,
                    "status": "failed",
                    "error": str(e),
                    "retryable": True,
                    "retry_hint": "The batch update failed. Retry with the same notes.",
                })

    # --- Build response ---
    results.sort(key=lambda r: r["index"])

    updated = sum(1 for r in results if r["status"] == "updated")
    failed = sum(1 for r in results if r["status"] == "failed")
    retryable_failed = sum(
        1 for r in results if r["status"] == "failed" and r.get("retryable")
    )
    total = len(notes)

    parts = []
    if failed:
        parts.append(f"{failed} failed")
        if retryable_failed:
            parts.append(f"{retryable_failed} retryable")
    detail = f" ({', '.join(parts)})" if parts else ""

    return {
        "updated": updated,
        "failed": failed,
        "retryable_failed": retryable_failed,
        "total_requested": total,
        "max_notes_per_batch": max_notes,
        "results": results,
        "message": f"Updated {updated} of {total} notes{detail}",
        "hint": f"Batch limit is {max_notes} notes per call. "
                f"Retry retryable failures by calling update_notes again with just those note IDs, "
                f"fixing field names or closing Anki browser first. "
                f"Use notes_info to verify successful updates.",
    }
