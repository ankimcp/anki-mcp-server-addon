from typing import Any, Callable, Coroutine

from ..tool_decorator import register_tools

# Import all essential tools - @Tool decorator registers them at import time
from .essential.tools import sync_tool  # noqa: F401
from .essential.tools import create_deck_tool  # noqa: F401
from .essential.tools import find_notes_tool  # noqa: F401
from .essential.tools import notes_info_tool  # noqa: F401
from .essential.tools import list_decks_tool  # noqa: F401
from .essential.tools import add_note_tool  # noqa: F401
from .essential.tools import model_names_tool  # noqa: F401
from .essential.tools import model_field_names_tool  # noqa: F401
from .essential.tools import model_styling_tool  # noqa: F401
from .essential.tools import update_model_styling_tool  # noqa: F401
from .essential.tools import delete_notes_tool  # noqa: F401
from .essential.tools import update_note_fields_tool  # noqa: F401
from .essential.tools import get_due_cards_tool  # noqa: F401
from .essential.tools import create_model_tool  # noqa: F401
from .essential.tools import present_card_tool  # noqa: F401
from .essential.tools import rate_card_tool  # noqa: F401
from .essential.tools import store_media_file_tool  # noqa: F401
from .essential.tools import get_media_files_names_tool  # noqa: F401
from .essential.tools import delete_media_file_tool  # noqa: F401

# Import all GUI tools - @Tool decorator registers them at import time
from .gui.tools import gui_add_cards_tool  # noqa: F401
from .gui.tools import gui_browse_tool  # noqa: F401
from .gui.tools import gui_current_card_tool  # noqa: F401
from .gui.tools import gui_deck_browser_tool  # noqa: F401
from .gui.tools import gui_edit_note_tool  # noqa: F401
from .gui.tools import gui_select_card_tool  # noqa: F401
from .gui.tools import gui_show_answer_tool  # noqa: F401
from .gui.tools import gui_show_question_tool  # noqa: F401
from .gui.tools import gui_undo_tool  # noqa: F401


def register_all_tools(
    mcp,
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    register_tools(mcp, call_main_thread)
