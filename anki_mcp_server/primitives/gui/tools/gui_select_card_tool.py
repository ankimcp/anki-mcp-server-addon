from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError


logger = logging.getLogger(__name__)


@Tool(
    "gui_select_card",
    "Select a specific card in an open Card Browser window. "
    "Returns true if browser is open and card was selected, false if browser is not open. "
    "IMPORTANT: Only use when user explicitly requests selecting a card in the browser. "
    "This tool is for note editing/creation workflows, NOT for review sessions. "
    "The Card Browser must already be open (use gui_browse first).",
    write=False,
)
def gui_select_card(card_id: int) -> dict[str, Any]:
    from aqt import mw, dialogs

    if mw is None or mw.col is None:
        raise HandlerError("Anki not ready", hint="Open a profile in Anki first")

    browser = dialogs._dialogs.get("Browser", [None, None])[1]

    if browser is None:
        return {
            "success": True,
            "selected": False,
            "cardId": card_id,
            "browserOpen": False,
            "message": "Card Browser is not open",
            "hint": "Use gui_browse to open the Card Browser first, then try selecting the card again.",
        }

    try:
        card = mw.col.get_card(card_id)
        if not card:
            raise HandlerError(
                f"Card {card_id} not found",
                hint="Card ID not found. Make sure the card exists and is visible in the current browser search.",
                cardId=card_id,
                browserOpen=True,
            )
    except HandlerError:
        raise
    except Exception:
        raise HandlerError(
            f"Card {card_id} not found",
            hint="Card ID not found. Make sure the card exists and is visible in the current browser search.",
            cardId=card_id,
            browserOpen=True,
        )

    if hasattr(browser, "table") and hasattr(browser.table, "select_cards"):
        browser.table.select_cards([card_id])
        logger.debug(f"Selected card {card_id} using table.select_cards()")
    elif hasattr(browser, "table") and hasattr(browser.table, "select_rows"):
        try:
            browser.table.select_rows([card_id])
            logger.debug(f"Selected card {card_id} using table.select_rows()")
        except Exception as e:
            logger.warning(f"select_rows failed with card ID: {e}, card may not be in current view")
            raise HandlerError(
                f"Failed to select card {card_id}",
                hint="The card may not be visible in the current browser search results.",
                cardId=card_id,
                browserOpen=True,
            )
    else:
        logger.warning("Browser doesn't have expected selection methods, card selection may not work")
        raise HandlerError(
            "Browser card selection not supported in this Anki version",
            hint="This Anki version may not support programmatic card selection in the browser.",
            cardId=card_id,
            browserOpen=True,
        )

    browser.activateWindow()

    return {
        "success": True,
        "selected": True,
        "cardId": card_id,
        "browserOpen": True,
        "message": f"Successfully selected card {card_id} in Card Browser",
        "hint": f'The card is now selected. To edit the note behind it, get its note ID with find_notes using the query "cid:{card_id}", then pass that note ID to gui_edit_note.',
    }
