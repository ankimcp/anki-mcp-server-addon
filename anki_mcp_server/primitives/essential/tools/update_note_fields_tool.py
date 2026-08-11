from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ._patch_helpers import (
    JSON_LITERAL_CAVEAT,
    apply_patch,
    reject_empty_full_input,
    require_patch_pair,
    select_mode,
)

logger = logging.getLogger(__name__)


@Tool(
    "update_note_fields",
    "Update the fields of an existing note. Supports HTML content in fields and preserves CSS styling.\n\n"
    "TWO MUTUALLY EXCLUSIVE INPUT MODES -- use exactly one:\n"
    "  1. FULL REPLACE: pass 'fields' as {field name: new full value}. Each "
    "listed field is overwritten entirely; fields you omit are left alone.\n"
    "  2. PATCH: pass 'field_name' + 'old_str' + 'new_str' to replace a snippet "
    "in place, like a text editor's find-and-replace. 'old_str' must match that "
    "field's current value EXACTLY ONCE (whitespace and HTML markup are "
    "significant); 0 matches or 2+ matches is an error and nothing is written. "
    "On a 0-match error the response includes an excerpt of the current value so "
    "you can correct 'old_str' and retry without a separate read. Patch mode "
    "touches exactly one field. Prefer PATCH for small edits to long fields -- "
    "it avoids resending the whole value and cannot silently clobber concurrent "
    "changes.\n"
    "  " + JSON_LITERAL_CAVEAT + "\n\n"
    "Note: media file attachment (audio/picture) is not yet supported. WARNING: Do not view the note in Anki browser while updating, or the fields will not update properly. Close the browser or switch to a different note before updating. IMPORTANT: Only update notes that the user explicitly asked to modify.",
    write=True,
)
def update_note_fields(
    id: int,
    fields: dict[str, str] | None = None,
    field_name: str | None = None,
    old_str: str | None = None,
    new_str: str | None = None,
) -> dict[str, Any]:
    from anki.errors import NotFoundError

    col = get_col()

    # --- Validate the input mode BEFORE looking up the note -------------------
    require_patch_pair(old_str, new_str, note_id=id)

    patch_present = field_name is not None or old_str is not None
    # Mode is selected on PRESENCE, not truthiness, so `fields={}` picks full
    # mode and is then rejected as "provided but empty" -- the mistake the
    # caller actually made -- instead of being misreported as "no input
    # provided". update_model_templates applies the identical rule.
    mode = select_mode(
        full_present=fields is not None,
        patch_present=patch_present,
        full_params="'fields'",
        patch_params="'field_name'/'old_str'/'new_str'",
        note_id=id,
    )
    if mode == "full":
        reject_empty_full_input(
            fields or {}, full_params="'fields'", what="field name", note_id=id,
        )

    try:
        anki_note = col.get_note(id)
    # Modern Anki raises anki.errors.NotFoundError for missing notes;
    # KeyError is kept for backward compatibility with older versions.
    except (NotFoundError, KeyError):
        raise HandlerError(
            f"Note not found with ID {id}. The note ID is invalid or the note has been deleted.",
            hint="Use find_notes to get valid note IDs.",
        )
    except Exception as e:
        logger.error(f"Unexpected error getting note {id}: {e}", exc_info=True)
        raise HandlerError(f"Failed to retrieve note {id}: {str(e)}")

    model_name = anki_note.note_type()["name"]
    existing_fields = list(anki_note.keys())

    if mode == "patch":
        updated_fields = _patch_field(
            anki_note, existing_fields, model_name, id,
            field_name, old_str or "", new_str or "",
        )
    else:
        updated_fields = _replace_fields(
            anki_note, existing_fields, model_name, fields or {},
        )

    col.update_note(anki_note)

    field_count = len(updated_fields)
    response: dict[str, Any] = {
        "noteId": id,
        "updatedFields": updated_fields,
        "fieldCount": field_count,
        "modelName": model_name,
        "message": f"Successfully updated {field_count} field{'s' if field_count != 1 else ''} in note",
        "cssNote": "HTML content is preserved. Model CSS styling remains unchanged.",
        "warning": "If changes don't appear, ensure the note wasn't open in Anki browser during update.",
        "hint": "Use notes_info to verify the changes or find_notes to locate other notes to update.",
    }
    if mode == "patch":
        response["patched"] = True
    return response


def _patch_field(
    anki_note: Any,
    existing_fields: list[str],
    model_name: str,
    note_id: int,
    field_name: str | None,
    old_str: str,
    new_str: str,
) -> list[str]:
    """Apply an exactly-once find-and-replace to a single field's value.

    Returns the list of field names written (always exactly one). Raises before
    mutating the note when the field is unknown or the match is not unique, so a
    rejected patch never reaches ``col.update_note``.
    """
    if field_name is None:
        raise HandlerError(
            "Patch mode requires field_name",
            hint="Pass field_name (the field to edit), old_str and new_str.",
            code="validation_error",
            note_id=note_id,
        )

    if field_name not in existing_fields:
        raise HandlerError(
            f'Field "{field_name}" does not exist on model "{model_name}". '
            f'Valid fields are: {", ".join(existing_fields)}.',
            hint="Use model_field_names to see valid fields for this model.",
            code="not_found",
            note_id=note_id,
        )

    anki_note[field_name] = apply_patch(
        anki_note[field_name],
        old_str,
        new_str,
        target=f'field "{field_name}" of note {note_id}',
        read_hint="notes_info",
        note_id=note_id,
        field_name=field_name,
    )
    return [field_name]


def _replace_fields(
    anki_note: Any,
    existing_fields: list[str],
    model_name: str,
    fields: dict[str, str],
) -> list[str]:
    """Overwrite whole field values. Returns the list of field names written."""
    invalid_fields = [field for field in fields.keys() if field not in existing_fields]
    if invalid_fields:
        raise HandlerError(
            f'Invalid fields for model "{model_name}": {", ".join(invalid_fields)}. '
            f'Valid fields are: {", ".join(existing_fields)}.',
            hint="Use model_field_names to see valid fields for this model.",
        )

    for field_name, field_value in fields.items():
        anki_note[field_name] = field_value

    return list(fields.keys())
