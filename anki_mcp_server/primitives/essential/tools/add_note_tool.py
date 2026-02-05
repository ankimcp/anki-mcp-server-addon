"""Add note tool - add a new note to Anki."""
from typing import Any, Optional
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


@Tool(
    "addNote",
    "Add a new note to Anki. Use modelNames to see available note types and "
    "modelFieldNames to see required fields. Returns the note ID on success. "
    "IMPORTANT: Only create notes that were explicitly requested by the user.",
    write=True,
)
def add_note(
    deck_name: str,
    model_name: str,
    fields: dict[str, str],
    tags: Optional[list[str]] = None,
    allow_duplicate: bool = False,
    duplicate_scope: Optional[str] = None,
    duplicate_scope_options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from anki.notes import Note

    col = get_col()

    deck = col.decks.by_name(deck_name)
    if deck is None:
        raise HandlerError(
            f"Deck not found: {deck_name}",
            hint="Use list_decks tool to see available decks or createDeck to create a new one.",
            deck_name=deck_name,
            model_name=model_name,
        )
    deck_id = deck["id"]

    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f"Model not found: {model_name}",
            hint="Use modelNames tool to see available models.",
            deck_name=deck_name,
            model_name=model_name,
        )

    model_fields = [field["name"] for field in model["flds"]]
    sort_field = model_fields[0]  # First field is the sort field

    # Only the sort field is required to be non-empty (matches Anki's behavior)
    sort_field_value = fields.get(sort_field, "")
    if not sort_field_value or not sort_field_value.strip():
        raise HandlerError(
            f"Sort field '{sort_field}' cannot be empty",
            hint="The first field (sort field) must have content. Other fields can be empty.",
            deck_name=deck_name,
            model_name=model_name,
            sort_field=sort_field,
        )

    provided_fields = list(fields.keys())
    missing_fields = [f for f in model_fields if f not in provided_fields]

    if missing_fields:
        raise HandlerError(
            f"Missing required fields: {', '.join(missing_fields)}",
            hint="Use modelFieldNames tool to see required fields for this model.",
            deck_name=deck_name,
            model_name=model_name,
            provided_fields=provided_fields,
            required_fields=model_fields,
        )

    extra_fields = [f for f in provided_fields if f not in model_fields]
    if extra_fields:
        raise HandlerError(
            f"Unknown fields for this model: {', '.join(extra_fields)}",
            hint="Use modelFieldNames tool to see valid fields for this model.",
            deck_name=deck_name,
            model_name=model_name,
            provided_fields=provided_fields,
            required_fields=model_fields,
        )

    if not allow_duplicate:
        first_field_name = model_fields[0]
        first_field_value = fields.get(first_field_name, "")

        try:
            duplicate_note_ids = col.find_notes(f'{first_field_name}:"{first_field_value}"')
            if duplicate_note_ids:
                raise HandlerError(
                    "Failed to create note - it may be a duplicate",
                    hint="The note appears to be a duplicate. Set allow_duplicate to true if you want to add it anyway.",
                    deck_name=deck_name,
                    model_name=model_name,
                )
        except HandlerError:
            raise
        except Exception as e:
            logger.warning(f"Duplicate check failed: {e}")

    note = Note(col, model)
    note.note_type()["did"] = deck_id

    for field_name, field_value in fields.items():
        note[field_name] = field_value

    if tags:
        note.tags = tags

    col.add_note(note, deck_id)

    if not note.id:
        raise HandlerError(
            "Failed to create note",
            hint="Check if the model and deck names are correct.",
            deck_name=deck_name,
            model_name=model_name,
        )

    field_count = len(fields)
    tag_count = len(tags) if tags else 0

    return {
        "note_id": note.id,
        "deck_name": deck_name,
        "model_name": model_name,
        "message": f'Successfully created note in deck "{deck_name}"',
        "details": {
            "fields_added": field_count,
            "tags_added": tag_count,
            "duplicate_check_scope": duplicate_scope or "default",
        },
    }
