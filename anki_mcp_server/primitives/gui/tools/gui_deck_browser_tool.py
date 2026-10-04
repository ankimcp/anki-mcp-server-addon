from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError


@Tool(
    "gui_deck_browser",
    "Open Anki Deck Browser dialog showing all decks. "
    "For when the user asks to open the deck browser, to see all decks or manage deck "
    "structure; not part of a review session.",
    title="Open Deck Browser",
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
