"""Find notes tool - search for notes using Anki query syntax."""
from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col


@Tool(
    "findNotes",
    "Search for notes using Anki query syntax. Returns an array of note IDs matching the query. "
    "Supports pagination with limit/offset parameters. "
    'Examples: "deck:Spanish", "tag:verb", "is:due", "front:hello", "added:1" (cards added today), '
    '"prop:due<=2" (cards due within 2 days), "flag:1" (red flag), "is:suspended"',
)
def find_notes(query: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Search for notes using Anki query syntax.

    Args:
        query: Anki search query string.
        limit: Maximum number of note IDs to return (default 100).
        offset: Number of results to skip for pagination (default 0).

    Returns:
        Dictionary with note IDs and pagination metadata.
    """
    if limit <= 0:
        raise HandlerError(
            "limit must be positive",
            hint="Use a value >= 1",
            provided_value=limit,
        )
    if offset < 0:
        raise HandlerError(
            "offset cannot be negative",
            hint="Use a value >= 0",
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
        return {
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

    hint = (
        "Use notesInfo tool to get detailed information about these notes. "
        "Use offset parameter to fetch more results."
        if offset + limit < total
        else "Use notesInfo tool to get detailed information about these notes"
    )

    return {
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
