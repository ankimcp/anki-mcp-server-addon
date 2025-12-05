# primitives/tools.py
"""Central tool registration module."""

from typing import Any, Callable, Coroutine

# Import all essential tools (this triggers handler registration at import time)
from .essential.tools.sync_tool import register_sync_tools
from .essential.tools.create_deck_tool import register_create_deck_tools
from .essential.tools.find_notes_tool import register_find_notes_tool
from .essential.tools.notes_info_tool import register_notes_info_tool
from .essential.tools.list_decks_tool import register_list_decks_tool
from .essential.tools.add_note_tool import register_add_note_tool
from .essential.tools.model_names_tool import register_model_names_tool
from .essential.tools.model_field_names_tool import register_model_field_names_tool
from .essential.tools.model_styling_tool import register_model_styling_tool
from .essential.tools.update_model_styling_tool import register_update_model_styling_tool
from .essential.tools.delete_notes_tool import register_delete_notes_tool
from .essential.tools.update_note_fields_tool import register_update_note_fields_tool
from .essential.tools.get_due_cards_tool import register_get_due_cards_tool
from .essential.tools.create_model_tool import register_create_model_tool
from .essential.tools.present_card_tool import register_present_card_tool
from .essential.tools.rate_card_tool import register_rate_card_tool
from .essential.tools.store_media_file_tool import register_store_media_file_tool
from .essential.tools.get_media_files_names_tool import register_get_media_files_names_tool
from .essential.tools.delete_media_file_tool import register_delete_media_file_tool

# Import all GUI tools (this triggers handler registration at import time)
from .gui.tools.gui_current_card_tool import register_gui_current_card_tool
from .gui.tools.gui_add_cards_tool import register_gui_add_cards_tool
from .gui.tools.gui_browse_tool import register_gui_browse_tool
from .gui.tools.gui_deck_browser_tool import register_gui_deck_browser_tool
from .gui.tools.gui_show_answer_tool import register_gui_show_answer_tool
from .gui.tools.gui_show_question_tool import register_gui_show_question_tool
from .gui.tools.gui_edit_note_tool import register_gui_edit_note_tool
from .gui.tools.gui_select_card_tool import register_gui_select_card_tool
from .gui.tools.gui_undo_tool import register_gui_undo_tool


def register_all_tools(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register all MCP tools with the server.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Async function to bridge calls to Anki's main thread
    """
    # Register essential tools
    register_sync_tools(mcp, call_main_thread)
    register_create_deck_tools(mcp, call_main_thread)
    register_find_notes_tool(mcp, call_main_thread)
    register_notes_info_tool(mcp, call_main_thread)
    register_list_decks_tool(mcp, call_main_thread)
    register_add_note_tool(mcp, call_main_thread)
    register_model_names_tool(mcp, call_main_thread)
    register_model_field_names_tool(mcp, call_main_thread)
    register_model_styling_tool(mcp, call_main_thread)
    register_update_model_styling_tool(mcp, call_main_thread)
    register_delete_notes_tool(mcp, call_main_thread)
    register_update_note_fields_tool(mcp, call_main_thread)
    register_get_due_cards_tool(mcp, call_main_thread)
    register_create_model_tool(mcp, call_main_thread)
    register_present_card_tool(mcp, call_main_thread)
    register_rate_card_tool(mcp, call_main_thread)
    register_store_media_file_tool(mcp, call_main_thread)
    register_get_media_files_names_tool(mcp, call_main_thread)
    register_delete_media_file_tool(mcp, call_main_thread)

    # Register GUI tools
    register_gui_current_card_tool(mcp, call_main_thread)
    register_gui_add_cards_tool(mcp, call_main_thread)
    register_gui_browse_tool(mcp, call_main_thread)
    register_gui_deck_browser_tool(mcp, call_main_thread)
    register_gui_show_answer_tool(mcp, call_main_thread)
    register_gui_show_question_tool(mcp, call_main_thread)
    register_gui_edit_note_tool(mcp, call_main_thread)
    register_gui_select_card_tool(mcp, call_main_thread)
    register_gui_undo_tool(mcp, call_main_thread)
