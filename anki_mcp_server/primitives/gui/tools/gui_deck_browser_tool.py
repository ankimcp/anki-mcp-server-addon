from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError


@Tool(
    "gui_deck_browser",
    "Open Anki Deck Browser dialog showing all decks. "
    "IMPORTANT: Only use when user explicitly requests opening the deck browser. "
    "This tool is for deck management and organization workflows, NOT for review sessions. "
    "Use this when user wants to see all decks or manage deck structure.",
    write=False,
)
def gui_deck_browser() -> dict[str, Any]:
    from aqt import mw

    if mw is None:
        raise HandlerError("Anki main window not available", hint="Make sure Anki is running")

    if mw.col is None:
        raise HandlerError("Collection not loaded", hint="Open a profile in Anki first")

    mw.moveToState("deckBrowser")

    return {
        "message": "Deck Browser opened successfully",
        "hint": "All decks are now visible in the Anki GUI. User can select a deck to study or manage.",
    }
