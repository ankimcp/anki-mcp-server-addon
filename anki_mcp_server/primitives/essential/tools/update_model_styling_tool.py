# primitives/essential/tools/update_model_styling_tool.py
from typing import Any, Callable, Coroutine

from ....handler_registry import register_handler


def _update_model_styling_handler(modelName: str, css: str) -> dict[str, Any]:
    """
    Handler for updating model CSS styling.

    This is a WRITE operation that modifies the collection, so it must be wrapped
    with requireReset/maybeReset for proper UI refresh.

    Args:
        modelName: Name of the model to update
        css: New CSS styling content

    Returns:
        dict: Update result with success status and CSS analysis

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
            "hint": "Model not found. Use modelNames tool to see available models.",
        }

    # Get old styling for comparison
    old_css = model.get("css", "")
    old_css_length = len(old_css)

    # Wrap the styling update in an edit session
    # Mark UI as needing refresh
    mw.requireReset()

    try:
        # Update the styling
        model["css"] = css

        # Save the model changes
        mw.col.models.update_dict(model)

        # Trigger UI refresh if collection is still available
        if mw.col:
            mw.maybeReset()

    except Exception as e:
        # Ensure UI refresh even on error
        if mw.col:
            mw.maybeReset()
        raise

    # Analyze CSS for useful info
    css_length = len(css)
    has_rtl = "direction: rtl" in css or "direction:rtl" in css
    has_card_class = ".card" in css
    has_front_class = ".front" in css
    has_back_class = ".back" in css
    has_cloze_class = ".cloze" in css

    response: dict[str, Any] = {
        "success": True,
        "modelName": modelName,
        "cssLength": css_length,
        "cssInfo": {
            "hasRtlSupport": has_rtl,
            "hasCardStyling": has_card_class,
            "hasFrontStyling": has_front_class,
            "hasBackStyling": has_back_class,
            "hasClozeStyling": has_cloze_class,
        },
        "message": f'Successfully updated CSS styling for model "{modelName}"',
    }

    if old_css:
        response["oldCssLength"] = old_css_length
        response["cssLengthChange"] = css_length - old_css_length

    return response


# Register handler for main thread execution
register_handler("updateModelStyling", _update_model_styling_handler)


def register_update_model_styling_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register updateModelStyling tool with the MCP server."""

    @mcp.tool(
        description=(
            "Update the CSS styling for an existing note type (model). "
            "This changes how cards of this type are rendered in Anki. "
            "Useful for adding RTL (Right-to-Left) support, changing fonts, colors, or layout. "
            "Changes apply to all cards using this model."
        )
    )
    async def updateModelStyling(
        modelName: str,
        css: str
    ) -> dict[str, Any]:
        """Update the CSS styling for an existing note type (model).

        Changes the visual appearance of all cards using this model by updating
        the CSS stylesheet. This is useful for customizing fonts, colors, layout,
        or adding RTL (Right-to-Left) support for languages like Hebrew or Arabic.

        Args:
            modelName: Name of the model to update (e.g., "Basic", "Basic RTL").
                Must be an existing model name - use modelNames tool to see available models.
            css: New CSS styling content. This will COMPLETELY REPLACE the existing CSS.
                For RTL languages, include "direction: rtl;" in .card class.
                Common CSS classes:
                - .card: Applies to all cards
                - .front: Front side of card
                - .back: Back side of card
                - .cloze: Cloze deletion styling

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - modelName (str): Name of the updated model
            - cssLength (int): Length of the new CSS in characters
            - cssInfo (dict): Analysis of CSS content:
                - hasRtlSupport (bool): Whether CSS includes RTL direction
                - hasCardStyling (bool): Whether CSS includes .card class
                - hasFrontStyling (bool): Whether CSS includes .front class
                - hasBackStyling (bool): Whether CSS includes .back class
                - hasClozeStyling (bool): Whether CSS includes .cloze class
            - oldCssLength (int, optional): Length of previous CSS
            - cssLengthChange (int, optional): Change in CSS length
            - message (str): Human-readable result message
            - error (str): Error message (if failed)
            - hint (str): Helpful hint for resolving errors (if failed)

        Raises:
            Exception: If model update fails on the main thread

        Examples:
            Add RTL support to a model:
            >>> await updateModelStyling(
            ...     modelName="Hebrew Basic",
            ...     css=".card { direction: rtl; text-align: right; font-size: 20px; }"
            ... )

            Customize font and colors:
            >>> await updateModelStyling(
            ...     modelName="Basic",
            ...     css=\"\"\"
            ...     .card {
            ...         font-family: Arial;
            ...         font-size: 20px;
            ...         text-align: center;
            ...         color: black;
            ...         background-color: white;
            ...     }
            ...     \"\"\"
            ... )

        Note:
            - This operation completely replaces existing CSS - make sure to include
              all necessary styles, not just the ones you want to change
            - Changes affect all cards using this model immediately
            - Invalid CSS won't cause errors but may render incorrectly
            - Use modelNames tool to see available models before updating
        """
        # Prepare arguments for main thread
        arguments = {
            "modelName": modelName,
            "css": css,
        }

        return await call_main_thread("updateModelStyling", arguments)
