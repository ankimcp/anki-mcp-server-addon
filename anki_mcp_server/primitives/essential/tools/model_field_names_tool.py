# primitives/essential/tools/model_field_names_tool.py
"""Model field names tool - MCP tool and handler in one file."""

from typing import Any, Callable, Coroutine

from ....handler_registry import register_handler


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _model_field_names_handler(modelName: str) -> dict[str, Any]:
    """
    Get the field names for a specific note type (model).

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    Retrieves all field names for the specified model/note type. This is useful
    to know what fields are required when creating notes of this type.

    Args:
        modelName: The name of the model/note type to get fields for

    Returns:
        dict: Response with structure:
            - success (bool): True if operation succeeded
            - modelName (str): The model name queried
            - fieldNames (list[str]): List of field names in order
            - total (int): Number of fields
            - message (str): Human-readable result message
            - example (dict, optional): Example field values for common models
            - hint (str, optional): Usage hint for common models

    Raises:
        RuntimeError: If collection is not loaded

    Example:
        >>> _model_field_names_handler(modelName="Basic")
        {
            'success': True,
            'modelName': 'Basic',
            'fieldNames': ['Front', 'Back'],
            'total': 2,
            'message': 'Model "Basic" has 2 fields',
            'example': {'Front': 'Question or prompt text', 'Back': 'Answer or response text'},
            'hint': 'Use these field names as keys when creating notes with addNote tool'
        }

    Note:
        - Field names are case-sensitive
        - Field names are returned in the order they appear in the model
        - Returns None if model doesn't exist (indicated by returning None from Anki API)
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

    # Get field names from the model
    field_names = [field["name"] for field in model["flds"]]

    if len(field_names) == 0:
        return {
            "success": True,
            "modelName": modelName,
            "fieldNames": [],
            "total": 0,
            "message": f'Model "{modelName}" has no fields',
        }

    # Provide example based on common model types
    example_fields = None
    hint = None
    lower_model_name = modelName.lower()

    if "basic" in lower_model_name and "reversed" not in lower_model_name:
        example_fields = {
            "Front": "Question or prompt text",
            "Back": "Answer or response text",
        }
        hint = "Use these field names as keys when creating notes with addNote tool"
    elif "basic" in lower_model_name and "reversed" in lower_model_name:
        example_fields = {
            "Front": "First side of the card",
            "Back": "Second side of the card",
        }
        hint = "Use these field names as keys when creating notes with addNote tool"
    elif "cloze" in lower_model_name:
        example_fields = {
            "Text": "The {{c1::hidden}} text will be replaced with [...] on the card",
            "Extra": "Additional information or hints",
        }
        hint = "Use these field names as keys when creating notes with addNote tool"

    response: dict[str, Any] = {
        "success": True,
        "modelName": modelName,
        "fieldNames": field_names,
        "total": len(field_names),
        "message": f'Model "{modelName}" has {len(field_names)} field{"s" if len(field_names) != 1 else ""}',
    }

    if example_fields:
        response["example"] = example_fields
    if hint:
        response["hint"] = hint

    return response


# Register handler at import time
register_handler("modelFieldNames", _model_field_names_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_model_field_names_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register model field names tool with the MCP server."""

    @mcp.tool(
        description="Get the field names for a specific note type (model). Use this to know what fields are required when creating notes of this type."
    )
    async def modelFieldNames(modelName: str) -> dict[str, Any]:
        """Get the field names for a specific note type (model).

        Retrieves all field names for the specified model/note type. This is essential
        information when creating notes, as you need to provide values for all required
        fields. The field names are returned in the order they appear in the model.

        Args:
            modelName: The name of the model/note type to get fields for.
                Must match exactly (case-sensitive). Common examples include:
                "Basic", "Basic (and reversed card)", "Cloze", etc.

        Returns:
            Dictionary containing:
            - success (bool): True if operation succeeded, False if model not found
            - modelName (str): The model name queried
            - fieldNames (list[str]): List of field names in order (only if success=True)
            - total (int): Number of fields (only if success=True)
            - message (str): Human-readable result message
            - example (dict, optional): Example field values for common model types
            - hint (str, optional): Usage hint for creating notes
            - error (str, optional): Error message if model not found

        Raises:
            Exception: If the main thread returns an error response

        Example:
            >>> # Get field names for Basic model
            >>> result = await modelFieldNames(modelName="Basic")
            >>> print(result['fieldNames'])  # ['Front', 'Back']
            >>> print(result['example'])  # {'Front': 'Question...', 'Back': 'Answer...'}
            >>>
            >>> # Get field names for Cloze model
            >>> result = await modelFieldNames(modelName="Cloze")
            >>> print(result['fieldNames'])  # ['Text', 'Extra']

        Note:
            - Field names are case-sensitive
            - Field names must be used exactly as returned when creating notes
            - For common models (Basic, Cloze), example field values are provided
            - Use modelNames tool first to see available models if unsure
            - This operation accesses the Anki collection on the main thread
        """
        return await call_main_thread("modelFieldNames", {"modelName": modelName})
