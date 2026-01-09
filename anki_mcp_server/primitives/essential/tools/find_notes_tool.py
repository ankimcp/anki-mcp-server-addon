"""Find notes tool - search for notes using Anki query syntax."""
from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


@Tool(
    "findNotes",
    "Search for notes using Anki query syntax. Returns an array of note IDs matching the query. "
    'Examples: "deck:Spanish", "tag:verb", "is:due", "front:hello", "added:1" (cards added today), '
    '"prop:due<=2" (cards due within 2 days), "flag:1" (red flag), "is:suspended"',
)
def find_notes(query: str) -> dict[str, Any]:
    col = get_col()

    try:
        note_ids = list(col.find_notes(query))
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

    count = len(note_ids)

    if count == 0:
        return {
            "noteIds": [],
            "count": 0,
            "query": query,
            "message": "No notes found matching the search criteria",
            "hint": "Try a broader search query or check your deck/tag names",
        }

    hint = (
        "Large result set. Consider using notesInfo with smaller batches for detailed information."
        if count > 100
        else "Use notesInfo tool to get detailed information about these notes"
    )

    return {
        "noteIds": note_ids,
        "count": count,
        "query": query,
        "message": f"Found {count} note{'s' if count != 1 else ''} matching the query",
        "hint": hint,
    }
