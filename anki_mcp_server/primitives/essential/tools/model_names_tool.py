"""Model names tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine

from ....handler_registry import register_handler


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _model_names_handler() -> dict[str, Any]:
    """
    Get a list of all available note type (model) names in Anki.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    This is a READ-only operation - no edit session needed.

    Returns:
        dict: Response containing:
            - success (bool): True if operation succeeded
            - modelNames (list[str]): List of all model/note type names
            - total (int): Number of models available
            - message (str): Human-readable status message
            - commonTypes (dict): Quick reference for common model types:
                - basic (str | None): "Basic" if available, None otherwise
                - basicReversed (str | None): "Basic (and reversed card)" if available
                - cloze (str | None): "Cloze" if available

    Raises:
        RuntimeError: If collection is not loaded
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Get all model names
    # all_names_and_ids returns a sequence of NamedTuples with 'name' and 'id'
    model_name_id_pairs = mw.col.models.all_names_and_ids()

    # Extract just the names
    model_names_list = [pair.name for pair in model_name_id_pairs]

    if not model_names_list:
        return {
            "success": True,
            "message": "No note types found in Anki",
            "modelNames": [],
            "total": 0,
            "commonTypes": {
                "basic": None,
                "basicReversed": None,
                "cloze": None,
            }
        }

    # Build common types reference
    common_types = {
        "basic": "Basic" if "Basic" in model_names_list else None,
        "basicReversed": "Basic (and reversed card)" if "Basic (and reversed card)" in model_names_list else None,
        "cloze": "Cloze" if "Cloze" in model_names_list else None,
    }

    return {
        "success": True,
        "modelNames": model_names_list,
        "total": len(model_names_list),
        "message": f"Found {len(model_names_list)} note types",
        "commonTypes": common_types,
    }


# Register handler at import time
register_handler("modelNames", _model_names_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_model_names_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register model names tool with the MCP server."""

    @mcp.tool(
        description="Get a list of all available note type (model) names in Anki. Use this to see what note types are available before creating notes."
    )
    async def modelNames() -> dict[str, Any]:
        """Get a list of all available note type (model) names in Anki.

        Retrieves all available note types (also called models) from the Anki collection.
        These are the templates used for creating notes, such as "Basic", "Cloze", etc.
        You must use a valid model name when creating new notes.

        Returns:
            Dictionary containing:
            - success (bool): True if operation succeeded
            - modelNames (list[str]): List of all model/note type names
            - total (int): Number of models available
            - message (str): Human-readable status message
            - commonTypes (dict): Quick reference for common model types:
                - basic (str | None): "Basic" if available, None otherwise
                - basicReversed (str | None): "Basic (and reversed card)" if available
                - cloze (str | None): "Cloze" if available

        Raises:
            Exception: If the main thread returns an error response

        Example:
            >>> result = await modelNames()
            >>> print(result['modelNames'])
            ['Basic', 'Basic (and reversed card)', 'Cloze', 'Custom Model']
            >>> print(result['commonTypes'])
            {'basic': 'Basic', 'basicReversed': 'Basic (and reversed card)', 'cloze': 'Cloze'}

        Note:
            - This operation accesses the Anki collection on the main thread
            - The returned model names are case-sensitive
            - Use these names exactly when creating notes with addNote tool
            - Custom models created by users will also appear in this list
            - Empty list indicates no models are configured (unusual situation)
        """
        return await call_main_thread("modelNames", {})
