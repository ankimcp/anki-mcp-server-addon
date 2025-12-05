# primitives/essential/tools/find_notes_tool.py
"""Find notes tool - MCP tool and handler in one file."""

from typing import Any, Callable, Coroutine

from ....handler_registry import register_handler


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _find_notes_handler(query: str) -> dict[str, Any]:
    """
    Search for notes using Anki query syntax.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    Uses Anki's collection.find_notes() method to search for notes matching
    the given query. The query uses Anki's standard search syntax.

    Args:
        query: Anki search query string. Supports all standard Anki query syntax:
            - "deck:DeckName" - notes in a specific deck
            - "tag:tagname" - notes with a specific tag
            - "is:due" - cards due for review
            - "is:new" - new unseen cards
            - "is:review" - cards in review
            - "is:suspended" - suspended cards
            - "front:text" - search front field
            - "back:text" - search back field
            - "added:1" - notes added in last 1 day
            - "prop:due<=2" - cards due within 2 days
            - "flag:1" - flagged cards (1=red, 2=orange, 3=green, 4=blue)
            Empty string returns all notes.

    Returns:
        dict: Result containing:
            - noteIds (list[int]): List of note IDs matching the query
            - count (int): Number of notes found
            - query (str): The search query used

    Raises:
        RuntimeError: If collection is not loaded
        Exception: If the search query is invalid or search fails

    Examples:
        >>> _find_notes_handler(query="deck:Spanish")
        {'noteIds': [1234, 5678], 'count': 2, 'query': 'deck:Spanish'}

        >>> _find_notes_handler(query="tag:verb is:due")
        {'noteIds': [1234], 'count': 1, 'query': 'tag:verb is:due'}

    Note:
        - This method uses mw.col.find_notes() which returns note IDs, not card IDs
        - The search is case-insensitive for most fields
        - Multiple search terms are combined with AND by default
        - Use "OR" explicitly for alternative conditions
        - Invalid queries will raise an exception from Anki's search parser
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Execute the search using Anki's collection API
    # find_notes() returns a list of note IDs
    try:
        note_ids = mw.col.find_notes(query)
    except Exception as e:
        # Re-raise with more context about the query
        raise Exception(f"Search query failed: {str(e)}")

    # Return result in expected format
    return {
        "noteIds": note_ids,
        "count": len(note_ids),
        "query": query
    }


# Register handler at import time
register_handler("find_notes", _find_notes_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_find_notes_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register find_notes tool with the MCP server."""

    @mcp.tool(
        description=(
            "Search for notes using Anki query syntax. Returns an array of note IDs matching the query. "
            'Examples: "deck:Spanish", "tag:verb", "is:due", "front:hello", "added:1" (cards added today), '
            '"prop:due<=2" (cards due within 2 days), "flag:1" (red flag), "is:suspended"'
        )
    )
    async def findNotes(query: str) -> dict[str, Any]:
        """Search for notes using Anki query syntax.

        Args:
            query: Anki search query. Use Anki query syntax like "deck:DeckName",
                "tag:tagname", "is:due", "is:new", "is:review", "front:text",
                "back:text", or combine with spaces for AND, OR for alternatives.
                Empty string returns all notes.

        Returns:
            Dictionary containing:
            - success: Boolean indicating success
            - noteIds: List of note IDs matching the query
            - count: Number of notes found
            - query: The search query used
            - message: Human-readable result message
            - hint: Helpful hint about next steps or query refinement

        Raises:
            Exception: If the search fails

        Examples:
            >>> await findNotes(query="deck:Spanish")
            >>> await findNotes(query="tag:verb")
            >>> await findNotes(query="is:due")
            >>> await findNotes(query="front:hello")
            >>> await findNotes(query="added:1")
        """
        try:
            # Call main thread to execute the search
            result = await call_main_thread("find_notes", {"query": query})

            # Structure the successful response
            note_ids = result.get("noteIds", [])
            count = len(note_ids)

            if count == 0:
                return {
                    "success": True,
                    "noteIds": [],
                    "count": 0,
                    "query": query,
                    "message": "No notes found matching the search criteria",
                    "hint": "Try a broader search query or check your deck/tag names"
                }

            # Success with results
            hint = (
                "Large result set. Consider using notesInfo with smaller batches for detailed information."
                if count > 100
                else "Use notesInfo tool to get detailed information about these notes"
            )

            return {
                "success": True,
                "noteIds": note_ids,
                "count": count,
                "query": query,
                "message": f"Found {count} note{'s' if count != 1 else ''} matching the query",
                "hint": hint
            }

        except Exception as e:
            # Handle errors and provide helpful hints
            error_msg = str(e)

            # Check for query-related errors
            if "query" in error_msg.lower() or "search" in error_msg.lower():
                return {
                    "success": False,
                    "query": query,
                    "error": error_msg,
                    "hint": "Invalid query syntax. Check Anki documentation for valid search syntax.",
                    "examples": [
                        '"deck:DeckName" - all notes in a deck',
                        '"tag:important" - notes with specific tag',
                        '"is:due" - cards that are due for review',
                        '"added:7" - notes added in last 7 days',
                        '"front:word" - notes with "word" in front field',
                    ]
                }

            # Generic error
            return {
                "success": False,
                "query": query,
                "error": error_msg,
                "hint": "Make sure Anki is running and the query syntax is valid"
            }
