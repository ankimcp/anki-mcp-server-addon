"""Add notes tool - batch-add multiple notes to Anki."""

from typing import Any
import logging

from pydantic import BaseModel, Field

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)

_MAX_NOTES = 100


class NoteEntry(BaseModel):
    """A single note to add in a batch."""

    fields: dict[str, str] = Field(
        description="Field values as key-value pairs "
        '(e.g., {"Front": "question", "Back": "answer"})'
    )
    tags: list[str] | None = Field(
        default=None,
        description="Additional tags for this note (merged with shared tags)",
    )


@Tool(
    "add_notes",
    "Add multiple notes to Anki in a single batch. Up to 100 notes sharing the same deck and model. "
    "Uses Anki's native batch API for atomic undo support. Supports partial success - "
    "individual failures don't affect others. "
    "IMPORTANT: Only create notes that were explicitly requested by the user.",
    write=True,
)
def add_notes(
    deck_name: str,
    model_name: str,
    notes: list[NoteEntry],
    tags: list[str] | None = None,
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    from anki.notes import Note
    from anki.collection import AddNoteRequest

    col = get_col()

    # --- Batch-level validation ---

    if not notes:
        raise HandlerError(
            "Notes array is empty",
            hint="Provide at least one note in the notes array.",
        )

    if len(notes) > _MAX_NOTES:
        raise HandlerError(
            f"Too many notes: {len(notes)} (maximum is {_MAX_NOTES})",
            hint=f"Split your request into batches of {_MAX_NOTES} or fewer.",
            requested=len(notes),
            maximum=_MAX_NOTES,
        )

    # Validate deck exists
    deck = col.decks.by_name(deck_name)
    if deck is None:
        raise HandlerError(
            f"Deck not found: {deck_name}",
            hint="Use list_decks tool to see available decks or create_deck to create a new one.",
            deck_name=deck_name,
            model_name=model_name,
        )
    deck_id = deck["id"]

    # Validate model exists
    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f"Model not found: {model_name}",
            hint="Use model_names tool to see available models.",
            deck_name=deck_name,
            model_name=model_name,
        )

    model_fields = [field["name"] for field in model["flds"]]
    sort_field = model_fields[0]
    model_fields_set = set(model_fields)

    # --- Per-note validation ---

    results: list[dict[str, Any]] = []
    valid_indices: list[int] = []
    valid_notes: list[Note] = []

    for i, entry in enumerate(notes):
        error = _validate_note_entry(
            entry, i, model_fields, model_fields_set, sort_field
        )
        if error is not None:
            results.append({"index": i, "status": "failed", "error": error})
            continue

        # Check for duplicates against existing collection.
        # Intra-batch duplicates are not detected (matches AnkiConnect behavior).
        if not allow_duplicate:
            sort_value = entry.fields[sort_field]
            try:
                duplicate_ids = col.find_notes(f'{sort_field}:"{sort_value}"')
                if duplicate_ids:
                    results.append(
                        {"index": i, "status": "skipped", "reason": "duplicate"}
                    )
                    continue
            except Exception as e:
                logger.warning("Duplicate check failed for note %d: %s", i, e)

        # Build note object
        note = Note(col, model)
        note.note_type()["did"] = deck_id

        for field_name, field_value in entry.fields.items():
            note[field_name] = field_value

        # Merge shared tags with per-note tags (deduplicated)
        merged_tags = list(dict.fromkeys((tags or []) + (entry.tags or [])))
        if merged_tags:
            note.tags = merged_tags

        valid_indices.append(i)
        valid_notes.append(note)

    # --- Batch add via native API ---

    if valid_notes:
        requests = [
            AddNoteRequest(note=note, deck_id=deck_id) for note in valid_notes
        ]
        try:
            col.add_notes(requests)

            # After add_notes, each note object has its .id assigned
            for idx, note in zip(valid_indices, valid_notes):
                if note.id:
                    results.append(
                        {"index": idx, "status": "created", "note_id": note.id}
                    )
                else:
                    results.append(
                        {
                            "index": idx,
                            "status": "failed",
                            "error": "Backend returned no ID for this note",
                        }
                    )
        except Exception as e:
            logger.error("col.add_notes() failed: %s", e)
            # All valid notes failed at the backend level
            for idx in valid_indices:
                results.append(
                    {"index": idx, "status": "failed", "error": str(e)}
                )

    # --- Build response ---

    # Sort results by index for consistent output
    results.sort(key=lambda r: r["index"])

    created = sum(1 for r in results if r["status"] == "created")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "failed")
    total = len(notes)

    parts = []
    if skipped:
        parts.append(f"{skipped} skipped")
    if failed:
        parts.append(f"{failed} failed")
    detail = f" ({', '.join(parts)})" if parts else ""

    return {
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "total_requested": total,
        "deck_name": deck_name,
        "model_name": model_name,
        "results": results,
        "message": f'Created {created} of {total} notes in deck "{deck_name}"{detail}',
    }


def _validate_note_entry(
    entry: NoteEntry,
    index: int,
    model_fields: list[str],
    model_fields_set: set[str],
    sort_field: str,
) -> str | None:
    """Validate a single note entry's fields.

    Returns an error message string if validation fails, None if valid.
    """
    provided = set(entry.fields.keys())

    # Check for missing fields
    missing = model_fields_set - provided
    if missing:
        return f"Missing required fields: {', '.join(sorted(missing))}"

    # Check for extra fields
    extra = provided - model_fields_set
    if extra:
        return f"Unknown fields for this model: {', '.join(sorted(extra))}"

    # Check sort field is non-empty
    sort_value = entry.fields.get(sort_field, "")
    if not sort_value or not sort_value.strip():
        return f"Sort field '{sort_field}' cannot be empty"

    return None
