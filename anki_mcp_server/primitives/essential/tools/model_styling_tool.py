"""Model styling tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine

from ....handler_registry import register_handler


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _model_styling_handler(modelName: str) -> dict[str, Any]:
    """
    Get the CSS styling for a specific note type (model).

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    This is a READ operation - no edit session needed.

    Args:
        modelName: The name of the model/note type to get styling for

    Returns:
        dict: Response containing:
            - success (bool): True if operation succeeded
            - modelName (str): The model name queried
            - css (str): The complete CSS styling for this model
            - cssInfo (dict): Metadata about the CSS:
                - length (int): Length of CSS in characters
                - hasCardStyling (bool): Whether CSS contains .card class
                - hasFrontStyling (bool): Whether CSS contains .front class
                - hasBackStyling (bool): Whether CSS contains .back class
                - hasClozeStyling (bool): Whether CSS contains .cloze class
            - message (str): Human-readable status message
            - hint (str): Usage information

    Raises:
        RuntimeError: If collection is not loaded
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Get the model by name
    model = mw.col.models.by_name(modelName)

    if model is None:
        return {
            "success": False,
            "error": f'Model "{modelName}" not found',
            "modelName": modelName,
            "hint": "Use modelNames tool to see available models",
        }

    # Get CSS from the model
    # In Anki's model structure, CSS is stored in the "css" field
    css = model.get("css", "")

    if not css:
        return {
            "success": False,
            "error": f'Model "{modelName}" has no styling',
            "modelName": modelName,
            "hint": "This model exists but has no CSS styling defined",
        }

    # Parse CSS to find key styling elements
    css_length = len(css)
    has_card_class = ".card" in css
    has_front_class = ".front" in css
    has_back_class = ".back" in css
    has_cloze_class = ".cloze" in css

    return {
        "success": True,
        "modelName": modelName,
        "css": css,
        "cssInfo": {
            "length": css_length,
            "hasCardStyling": has_card_class,
            "hasFrontStyling": has_front_class,
            "hasBackStyling": has_back_class,
            "hasClozeStyling": has_cloze_class,
        },
        "message": f'Retrieved CSS styling for model "{modelName}"',
        "hint": "This CSS is automatically applied when cards of this type are rendered in Anki",
    }


# Register handler at import time
register_handler("modelStyling", _model_styling_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_model_styling_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register model styling tool with the MCP server."""

    @mcp.tool(
        description="Get the CSS styling for a specific note type (model). This CSS is used when rendering cards of this type."
    )
    async def modelStyling(modelName: str) -> dict[str, Any]:
        """Get the CSS styling for a specific note type (model).

        Retrieves the CSS styling that is automatically applied when cards of the
        specified model type are rendered in Anki. This includes styling for card
        elements like .card, .front, .back, and .cloze classes.

        Args:
            modelName: The name of the model/note type to get styling for.
                      Must be an exact match (case-sensitive). Use modelNames
                      tool to see available models.

        Returns:
            Dictionary containing:
            - success (bool): True if operation succeeded
            - modelName (str): The model name queried
            - css (str): The complete CSS styling for this model
            - cssInfo (dict): Metadata about the CSS:
                - length (int): Length of CSS in characters
                - hasCardStyling (bool): Whether CSS contains .card class
                - hasFrontStyling (bool): Whether CSS contains .front class
                - hasBackStyling (bool): Whether CSS contains .back class
                - hasClozeStyling (bool): Whether CSS contains .cloze class
            - message (str): Human-readable status message
            - hint (str): Usage information

        Raises:
            Exception: If model not found or main thread returns an error

        Example:
            >>> result = await modelStyling(modelName="Basic")
            >>> print(result['css'])
            '.card { font-family: arial; font-size: 20px; ... }'
            >>> print(result['cssInfo'])
            {'length': 245, 'hasCardStyling': True, 'hasFrontStyling': True, ...}

        Note:
            - This operation accesses the Anki collection on the main thread
            - The CSS is applied automatically when cards are displayed
            - Model names are case-sensitive
            - Returns error if model doesn't exist or has no styling
            - Use modelNames tool first to see available models
        """
        return await call_main_thread("modelStyling", {"modelName": modelName})
