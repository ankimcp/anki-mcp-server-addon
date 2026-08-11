"""Find notes tool - search for notes using Anki query syntax."""
from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)

_MAX_LIMIT = 500

# Character budget for the first-field label returned by include_first_field.
# Enough to recognize a note, small enough that 500 labels stay cheap.
_FIRST_FIELD_EXCERPT_CHARS = 250


def _build_first_field_labels(col: Any, note_ids: list[int]) -> list[dict[str, Any]]:
    """Build {noteId, firstField, truncated, fullLength} labels for note IDs.

    The label is the note's first field (usually its title) -- ordinal 0 of the
    note type, which is NOT necessarily the note type's sort field
    (``note_type["sortf"]``, what Anki's browser actually sorts and displays by).
    It is the cheapest way to tell results apart without a follow-up notes_info
    call. Values are plain-sliced at ``_FIRST_FIELD_EXCERPT_CHARS`` (no HTML
    awareness) and are for identification only, never as the basis for an edit.

    Notes that vanished between the search and this read are skipped with a
    warning rather than aborting the whole search result.

    Args:
        col: The open Anki collection.
        note_ids: Note IDs from the current page of search results.

    Returns:
        One entry per readable note, in the same order as ``note_ids``.
    """
    from anki.errors import NotFoundError

    labels: list[dict[str, Any]] = []
    for note_id in note_ids:
        try:
            note = col.get_note(note_id)
        # Modern Anki raises anki.errors.NotFoundError for missing notes;
        # KeyError is kept for backward compatibility with older versions.
        except (NotFoundError, KeyError) as e:
            logger.warning(f"Note {note_id} not found while labeling results: {e}")
            continue

        value = note.fields[0] if note.fields else ""
        full_length = len(value)
        truncated = full_length > _FIRST_FIELD_EXCERPT_CHARS
        # camelCase to match this tool's existing keys (noteIds, hasMore).
        labels.append({
            "noteId": note_id,
            "firstField": value[:_FIRST_FIELD_EXCERPT_CHARS] if truncated else value,
            "truncated": truncated,
            "fullLength": full_length,
        })
    return labels


@Tool(
    "find_notes",
    "Search for notes using Anki query syntax. Returns an array of note IDs matching the query. "
    "Supports pagination with limit/offset parameters. Maximum limit is 500 per request. "
    'Examples: "deck:Spanish", "tag:verb", "is:due", "front:hello", "added:1" (cards added today), '
    '"prop:due<=2" (cards due within 2 days), "flag:1" (red flag), "is:suspended". '
    "Set include_first_field=true to also get a 'noteLabels' array of "
    "{noteId, firstField} entries "
    f"(first field excerpted at {_FIRST_FIELD_EXCERPT_CHARS} chars, with truncated/fullLength markers) - "
    "one call to find AND label results instead of find_notes followed by notes_info. "
    "The labels identify notes; read full content with notes_info before editing. "
    "Pass 'noteIds' (not 'noteLabels') to any tool that wants note IDs.",
)
def find_notes(
    query: str,
    limit: int = 100,
    offset: int = 0,
    include_first_field: bool = False,
) -> dict[str, Any]:
    """Search for notes using Anki query syntax.

    Args:
        query: Anki search query string.
        limit: Maximum number of note IDs to return (default 100, max 500).
        offset: Number of results to skip for pagination (default 0).
        include_first_field: When True, add a ``noteLabels`` array of
            {noteId, firstField, truncated, fullLength} labels alongside the
            existing note ID list (default False). Named ``noteLabels`` rather
            than ``notes`` so it cannot be confused with the ``notes``
            PARAMETER of notes_info, which takes note IDs -- pasting these
            objects there would be a silent type error.

    Returns:
        Dictionary with note IDs and pagination metadata.
    """
    if limit <= 0:
        raise HandlerError(
            "limit must be positive",
            hint="Use a value >= 1",
            code="validation_error",
            provided_value=limit,
        )
    if limit > _MAX_LIMIT:
        raise HandlerError(
            f"limit exceeds maximum of {_MAX_LIMIT} (requested: {limit})",
            hint=f"Use a limit <= {_MAX_LIMIT} and paginate with offset",
            code="limit_exceeded",
            provided_value=limit,
            maximum=_MAX_LIMIT,
        )
    if offset < 0:
        raise HandlerError(
            "offset cannot be negative",
            hint="Use a value >= 0",
            code="validation_error",
            provided_value=offset,
        )

    col = get_col()

    try:
        all_note_ids = col.find_notes(query)
    except Exception as e:
        raise HandlerError(
            f"Search query failed: {e}",
            hint="Check Anki documentation for valid search syntax",
            examples=[
                '"deck:DeckName" - all notes in a deck',
                '"tag:important" - notes with specific tag',
                '"is:due" - cards that are due for review',
                '"added:7" - notes added in last 7 days',
            ],
        )

    total = len(all_note_ids)
    note_ids = list(all_note_ids[offset : offset + limit])
    count = len(note_ids)

    if total == 0:
        empty: dict[str, Any] = {
            "noteIds": [],
            "count": 0,
            "total": 0,
            "hasMore": False,
            "offset": offset,
            "limit": limit,
            "query": query,
            "message": "No notes found matching the search criteria",
            "hint": "Try a broader search query or check your deck/tag names",
        }
        if include_first_field:
            empty["noteLabels"] = []
        return empty

    hint = (
        "Use notes_info tool to get detailed information about these notes. "
        "Use offset parameter to fetch more results."
        if offset + limit < total
        else "Use notes_info tool to get detailed information about these notes"
    )

    result: dict[str, Any] = {
        "noteIds": note_ids,
        "count": count,
        "total": total,
        "hasMore": offset + limit < total,
        "offset": offset,
        "limit": limit,
        "query": query,
        "message": f"Found {total} note{'s' if total != 1 else ''} matching the query, returning {count}",
        "hint": hint,
    }

    if include_first_field:
        result["noteLabels"] = _build_first_field_labels(col, note_ids)

    return result
