from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError


@Tool(
    "gui_add_cards",
    "Open Anki Add Cards dialog. "
    "IMPORTANT: Only use when user explicitly requests opening the Add Cards dialog. "
    "This tool simply opens the dialog for manual note creation - "
    "it does not pre-fill any fields. For programmatic note creation, use add_note instead.",
    write=False,
    require_col=False,
)
def gui_add_cards() -> dict[str, Any]:
    from aqt import mw, dialogs

    if mw is None:
        raise HandlerError("Main window not available", hint="Make sure Anki is running")

    dialogs.open("AddCards", mw)

    return {"message": "Add Cards dialog opened successfully"}
