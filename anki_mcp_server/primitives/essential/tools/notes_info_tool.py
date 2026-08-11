"""Notes info tool - get detailed information about specific notes."""
from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ....config import get_max_notes_per_batch

logger = logging.getLogger(__name__)


def _apply_excerpt(fields_dict: dict[str, dict[str, Any]], excerpt_chars: int) -> None:
    """Truncate every field value in place and annotate it with excerpt markers.

    Truncation is plain string slicing by design -- no HTML awareness -- so a cut
    value may end mid-tag or mid-entity. Excerpts identify notes cheaply; they are
    never a safe basis for edits.

    Args:
        fields_dict: Field entries as built by ``notes_info`` (mutated in place).
        excerpt_chars: Maximum number of characters to keep per value.
    """
    for entry in fields_dict.values():
        value = entry["value"]
        full_length = len(value)
        if full_length > excerpt_chars:
            entry["value"] = value[:excerpt_chars]
            entry["truncated"] = True
        else:
            entry["truncated"] = False
        entry["fullLength"] = full_length


@Tool(
    "notes_info",
    "Get detailed information about specific notes including all fields, tags, model info, and CSS styling. "
    "Use this after find_notes to get complete note data. Includes CSS for proper rendering awareness. "
    "Use include_fields to return only specific fields, or exclude_fields to omit fields - "
    "useful for reducing response size in bulk queries. If both are provided, include_fields takes priority. "
    "Use excerpt_chars (>= 1) to cap every returned field value at that many characters; each field then also "
    "carries truncated (bool) and fullLength (original character count). Excerpts exist to IDENTIFY notes "
    "cheaply, NOT to search their content - content search belongs in find_notes queries, which run server-side "
    "over full content. Never use a truncated value as the basis for an edit: re-read the field without "
    "excerpt_chars first, otherwise you will write back a cut-off value.",
)
def notes_info(
    notes: list[int],
    include_fields: list[str] | None = None,
    exclude_fields: list[str] | None = None,
    excerpt_chars: int | None = None,
) -> dict[str, Any]:
    from anki.errors import NotFoundError

    if not notes:
        raise HandlerError(
            "notes parameter cannot be empty",
            code="validation_error",
        )

    if excerpt_chars is not None and excerpt_chars < 1:
        raise HandlerError(
            f"excerpt_chars must be >= 1 (got {excerpt_chars})",
            hint="Omit excerpt_chars to get full field values, "
                 "or pass a positive character budget such as 120",
            code="validation_error",
            provided_value=excerpt_chars,
        )

    max_notes = get_max_notes_per_batch()
    if len(notes) > max_notes:
        raise HandlerError(
            f"Too many notes: {len(notes)} (maximum is {max_notes})",
            hint=f"Split your request into batches of {max_notes} or fewer. "
                 f"You can increase the limit via the 'max_notes_per_batch' addon config option.",
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

            # Excerpting runs after filtering: only surviving fields are truncated.
            if excerpt_chars is not None:
                _apply_excerpt(fields_dict, excerpt_chars)

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
        # Modern Anki raises anki.errors.NotFoundError for missing notes;
        # KeyError is kept for backward compatibility with older versions.
        except (NotFoundError, KeyError) as e:
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
        if excerpt_chars is not None:
            hint += (
                f" Values are excerpts capped at {excerpt_chars} character(s); "
                "any field with truncated=true is incomplete -- re-read it without "
                "excerpt_chars before editing, and search content via find_notes."
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
