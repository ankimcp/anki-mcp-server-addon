# primitives/gui/tools/gui_undo_tool.py
"""GUI Undo tool - undo the last action in Anki."""

from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _gui_undo_handler() -> dict[str, Any]:
    """
    Undo the last action in Anki.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Checks if there's an undo operation available and performs it if possible.
    Returns information about whether the undo was successful.

    Returns:
        dict: Result with structure:
            - success (bool): Whether the operation completed without errors
            - undone (bool): Whether an action was actually undone
            - message (str): Human-readable result message
            - hint (str): Helpful hint about the result

    Raises:
        RuntimeError: If collection is not loaded or main window not available

    Note:
        - Uses col.undo_status() to check if undo is available
        - Calls mw.undo() to perform the actual undo operation
        - mw.undo() runs the operation in the background via CollectionOp
        - The operation shows a tooltip notification to the user
        - If there's nothing to undo, returns success but undone=False
    """
    from aqt import mw

    # Check if main window is available
    if mw is None:
        raise RuntimeError("Anki main window not available")

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    try:
        # Check if there's something to undo
        undo_status = mw.col.undo_status()

        if not undo_status or not undo_status.undo:
            # Nothing to undo
            logger.info("No undo operation available")
            return {
                "success": True,
                "undone": False,
                "message": "Nothing to undo",
                "hint": "There are no recent actions to undo in Anki.",
            }

        # Perform the undo operation
        # Note: mw.undo() runs asynchronously via CollectionOp and shows a tooltip
        # It will silently handle UndoEmpty exceptions if they occur
        mw.undo()

        logger.info("Undo operation initiated successfully")
        return {
            "success": True,
            "undone": True,
            "message": "Last action undone successfully",
            "hint": "The previous action has been reversed. Check Anki GUI to verify.",
        }

    except Exception as e:
        logger.error(f"Failed to undo action: {e}", exc_info=True)
        return {
            "success": False,
            "undone": False,
            "error": f"Failed to undo action: {str(e)}",
            "hint": "Make sure Anki is running and the GUI is visible",
        }


# Register handler at import time
register_handler("gui_undo", _gui_undo_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_gui_undo_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register gui_undo tool with the MCP server."""

    @mcp.tool(
        description=(
            "Undo the last action or card in Anki. Returns true if undo succeeded, false otherwise. "
            "IMPORTANT: Only use when user explicitly requests undoing an action. "
            "This tool is for note editing/creation workflows, NOT for review sessions. "
            "Use this to undo mistakes in note creation, editing, or card management."
        )
    )
    async def gui_undo() -> dict[str, Any]:
        """Undo the last action in Anki.

        Checks if there's an undo operation available and performs it if possible.
        This is equivalent to pressing Ctrl+Z or clicking the Undo menu item in Anki.

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation completed without errors
            - undone (bool): Whether an action was actually undone (false if nothing to undo)
            - message (str): Human-readable result message
            - hint (str): Helpful hint about the result or next steps
            - error (str): Error message (if failed)

        Raises:
            Exception: If the undo operation fails on the main thread

        Examples:
            Successfully undo an action:
            >>> result = await gui_undo()
            >>> if result['undone']:
            ...     print(result['message'])  # "Last action undone successfully"
            ... else:
            ...     print(result['hint'])  # "There are no recent actions to undo..."

            Handle the response:
            >>> result = await gui_undo()
            >>> if result['success'] and result['undone']:
            ...     print("Action undone - check the GUI")
            ... elif result['success'] and not result['undone']:
            ...     print("Nothing to undo")
            ... else:
            ...     print(f"Error: {result['error']}")

        Note:
            - This operation runs on Anki's main thread
            - The undo operation is performed asynchronously via Anki's CollectionOp
            - A tooltip notification is shown to the user in the Anki GUI
            - Only use when the user explicitly requests undoing an action
            - If there's nothing to undo, returns success=True but undone=False
            - The operation can undo various actions like:
              - Note additions/deletions
              - Card state changes
              - Field edits
              - Deck operations
              - Tag modifications
            - Make sure Anki is running and the GUI is visible
            - This is NOT for undoing review ratings - use separate review tools
        """
        return await call_main_thread("gui_undo", {})
