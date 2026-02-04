from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import get_col


@Tool(
    "gui_browse",
    "Open Anki Card Browser and search for cards using Anki query syntax. "
    "Returns array of card IDs found. IMPORTANT: Only use when user explicitly "
    "requests opening the browser. This tool is for note editing/creation workflows, "
    "NOT for review sessions. Use this to find and select cards/notes that need editing.",
    write=False,
)
def gui_browse(query: str) -> dict[str, Any]:
    from aqt import mw, dialogs

    col = get_col()

    browser = dialogs.open("Browser", mw)
    browser.activateWindow()

    if query:
        browser.form.searchEdit.lineEdit().setText(query)
        if hasattr(browser, "onSearch"):
            browser.onSearch()
        else:
            browser.onSearchActivated()

    card_ids = list(col.find_cards(query))
    card_count = len(card_ids)

    if card_count == 0:
        message = f'Browser opened with query "{query}" - no cards found'
        hint = "Try a different query or check if the deck/tags exist"
    elif card_count == 1:
        message = f'Browser opened with query "{query}" - found 1 card'
        hint = "You can now edit or review the card in the browser"
    else:
        message = f'Browser opened with query "{query}" - found {card_count} cards'
        hint = "You can now select, edit, or export these cards in the browser"

    return {
        "cardIds": card_ids,
        "cardCount": card_count,
        "query": query,
        "message": message,
        "hint": hint,
    }
