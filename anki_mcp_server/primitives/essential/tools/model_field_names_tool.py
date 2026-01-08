from typing import Any

from ....tool_decorator import Tool, ToolError, get_col


@Tool(
    "modelFieldNames",
    "Get the field names for a specific note type (model). Use this to know what fields are required when creating notes of this type.",
)
def model_field_names(model_name: str) -> dict[str, Any]:
    col = get_col()

    model = col.models.by_name(model_name)
    if model is None:
        raise ToolError(
            f'Model "{model_name}" not found',
            hint="Use modelNames tool to see available models",
            model_name=model_name,
        )

    field_names = [field["name"] for field in model["flds"]]

    if len(field_names) == 0:
        return {
            "model_name": model_name,
            "field_names": [],
            "total": 0,
            "message": f'Model "{model_name}" has no fields',
        }

    # Provide example based on common model types
    example_fields = None
    hint = None
    lower_model_name = model_name.lower()

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
        "model_name": model_name,
        "field_names": field_names,
        "total": len(field_names),
        "message": f'Model "{model_name}" has {len(field_names)} field{"s" if len(field_names) != 1 else ""}',
    }

    if example_fields:
        response["example"] = example_fields
    if hint:
        response["hint"] = hint

    return response
