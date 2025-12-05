"""Create model tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine, Optional
import re
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _create_model_handler(
    modelName: str,
    inOrderFields: list[str],
    cardTemplates: list[dict[str, str]],
    css: Optional[str] = None,
    isCloze: bool = False,
) -> dict[str, Any]:
    """
    Create a new note type (model) in Anki with custom fields and templates.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        modelName: Unique name for the new model (e.g., "Basic RTL", "Advanced Vocabulary")
        inOrderFields: Field names in order (e.g., ["Front", "Back"]). At least one required.
        cardTemplates: List of card template dictionaries, each containing:
            - Name: Template name (e.g., "Card 1")
            - Front: Front template HTML with field placeholders (e.g., "{{Front}}")
            - Back: Back template HTML (e.g., "{{FrontSide}}<hr id=answer>{{Back}}")
        css: Optional CSS styling for cards. For RTL, use "direction: rtl;" in .card class
        isCloze: Whether to create as cloze deletion model (default: False)

    Returns:
        dict: Creation result with structure:
            - success (bool): Whether the operation succeeded
            - modelName (str): Name of the created model
            - modelId (int | None): ID of the created model (if available)
            - fields (list[str]): List of field names
            - templateCount (int): Number of card templates
            - hasCss (bool): Whether CSS was provided
            - isCloze (bool): Whether this is a cloze model
            - message (str): Human-readable result message
            - warnings (list[str], optional): Field reference warnings if any
            - error (str): Error message (if failed)
            - hint (str): Helpful hint for resolving errors (if failed)

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If validation fails (empty fields, templates, etc.)
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Validate inputs
    if not modelName or not modelName.strip():
        raise ValueError("Model name cannot be empty")

    if not inOrderFields or len(inOrderFields) == 0:
        raise ValueError("At least one field is required")

    if not cardTemplates or len(cardTemplates) == 0:
        raise ValueError("At least one card template is required")

    # Check if model already exists
    existing_model = mw.col.models.by_name(modelName)
    if existing_model is not None:
        return {
            "success": False,
            "error": f'Model "{modelName}" already exists',
            "modelName": modelName,
            "hint": "A model with this name already exists. Use a different name or use modelNames tool to see existing models.",
        }

    # Validate field names
    for field_name in inOrderFields:
        if not field_name or not field_name.strip():
            raise ValueError("Field names cannot be empty")

    # Validate template structure
    for i, template in enumerate(cardTemplates):
        if "Name" not in template or not template["Name"]:
            raise ValueError(f"Template {i} missing required 'Name' field")
        if "Front" not in template or not template["Front"]:
            raise ValueError(f"Template {i} ('{template.get('Name', 'unnamed')}') missing required 'Front' field")
        if "Back" not in template or not template["Back"]:
            raise ValueError(f"Template {i} ('{template.get('Name', 'unnamed')}') missing required 'Back' field")

    # Validate field references in templates (warning only, not error)
    warnings: list[str] = []
    field_set = set(inOrderFields)

    # Special Anki fields that should be excluded from validation
    special_fields = {
        "FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card",
        "CardFlag", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"
    }

    for template in cardTemplates:
        template_content = f"{template['Front']} {template['Back']}"
        # Find all {{FieldName}} references using regex
        field_refs = re.findall(r'\{\{([^}]+)\}\}', template_content)

        for ref in field_refs:
            field_name = ref.strip()
            # Skip special Anki fields and cloze references
            if (field_name in special_fields or
                field_name.startswith("cloze:") or
                field_name.startswith("c")):
                continue

            if field_name not in field_set:
                warnings.append(
                    f'Template "{template["Name"]}" references field "{{{{{field_name}}}}}" '
                    f'which is not in inOrderFields'
                )

    # WRITE operation - wrap with edit session
    try:
        mw.requireReset()

        # Get model manager
        mm = mw.col.models

        # Create new model
        if isCloze:
            model = mm.new_cloze(modelName)
        else:
            model = mm.new(modelName)

        # Remove default fields
        for field in model["flds"][:]:
            mm.remove_field(model, field)

        # Add custom fields
        for field_name in inOrderFields:
            field = mm.new_field(field_name)
            mm.add_field(model, field)

        # Remove default templates
        for template in model["tmpls"][:]:
            mm.remove_template(model, template)

        # Add custom templates
        for template_dict in cardTemplates:
            template = mm.new_template(template_dict["Name"])
            template["qfmt"] = template_dict["Front"]
            template["afmt"] = template_dict["Back"]
            mm.add_template(model, template)

        # Set CSS if provided
        if css:
            model["css"] = css

        # Save the model
        mm.add(model)
        mm.save(model)

        model_result = model

    finally:
        if mw.col:
            mw.maybeReset()

    # Get the model ID
    model_id = model_result.get("id")

    # Build success response
    response: dict[str, Any] = {
        "success": True,
        "modelName": modelName,
        "modelId": model_id,
        "fields": inOrderFields,
        "templateCount": len(cardTemplates),
        "hasCss": bool(css),
        "isCloze": isCloze,
        "message": (
            f'Successfully created model "{modelName}" with '
            f'{len(inOrderFields)} fields and {len(cardTemplates)} template(s)'
        ),
    }

    if warnings:
        response["warnings"] = warnings
        response["message"] += ". Note: Some warnings were detected (see warnings field)."

    return response


# Register handler at import time
register_handler("createModel", _create_model_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_create_model_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register createModel tool with the MCP server."""

    @mcp.tool(
        description=(
            "Create a new note type (model) in Anki with custom fields, card templates, and styling. "
            "Useful for creating specialized models like RTL (Right-to-Left) language models for Hebrew, Arabic, etc. "
            "Each model defines the structure of notes and how cards are generated from them."
        )
    )
    async def createModel(
        modelName: str,
        inOrderFields: list[str],
        cardTemplates: list[dict[str, str]],
        css: Optional[str] = None,
        isCloze: bool = False,
    ) -> dict[str, Any]:
        """Create a new note type (model) in Anki.

        Args:
            modelName: Unique name for the new model (e.g., "Basic RTL", "Advanced Vocabulary")
            inOrderFields: Field names in order (e.g., ["Front", "Back"]). At least one field required.
            cardTemplates: Card templates (at least one required). Each template generates one card per note.
                Each template must have:
                - Name: Template name (e.g., "Card 1")
                - Front: Front template HTML with field placeholders (e.g., "{{Front}}")
                - Back: Back template HTML with field placeholders (e.g., "{{FrontSide}}<hr id=answer>{{Back}}")
            css: Optional CSS styling for cards. For RTL languages, include "direction: rtl;" in .card class.
            isCloze: Create as cloze deletion model (default: false)

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - modelName (str): Name of the created model
            - modelId (int): ID of the created model (if available)
            - fields (list[str]): List of field names
            - templateCount (int): Number of card templates
            - hasCss (bool): Whether CSS was provided
            - isCloze (bool): Whether this is a cloze model
            - message (str): Human-readable result message
            - warnings (list[str], optional): Field reference warnings if any
            - error (str): Error message (if failed)
            - hint (str): Helpful hint for resolving errors (if failed)

        Raises:
            Exception: If model creation fails on the main thread

        Note:
            The tool validates field references in templates and provides warnings
            for fields that are referenced but not defined in inOrderFields.
            Special Anki fields (FrontSide, Tags, Type, Deck, etc.) are excluded
            from validation.

        Examples:
            Basic model:
            >>> await createModel(
            ...     modelName="Simple Flash Card",
            ...     inOrderFields=["Front", "Back"],
            ...     cardTemplates=[{
            ...         "Name": "Card 1",
            ...         "Front": "{{Front}}",
            ...         "Back": "{{FrontSide}}<hr id=answer>{{Back}}"
            ...     }]
            ... )

            RTL model with styling:
            >>> await createModel(
            ...     modelName="Hebrew Basic",
            ...     inOrderFields=["Hebrew", "English"],
            ...     cardTemplates=[{
            ...         "Name": "Hebrew to English",
            ...         "Front": "{{Hebrew}}",
            ...         "Back": "{{FrontSide}}<hr id=answer>{{English}}"
            ...     }],
            ...     css=".card { direction: rtl; text-align: right; }"
            ... )

            Cloze model:
            >>> await createModel(
            ...     modelName="My Cloze",
            ...     inOrderFields=["Text", "Extra"],
            ...     cardTemplates=[{
            ...         "Name": "Cloze",
            ...         "Front": "{{cloze:Text}}",
            ...         "Back": "{{cloze:Text}}<br>{{Extra}}"
            ...     }],
            ...     isCloze=True
            ... )
        """
        # Prepare arguments for main thread
        arguments = {
            "modelName": modelName,
            "inOrderFields": inOrderFields,
            "cardTemplates": cardTemplates,
            "css": css,
            "isCloze": isCloze,
        }

        return await call_main_thread("createModel", arguments)
