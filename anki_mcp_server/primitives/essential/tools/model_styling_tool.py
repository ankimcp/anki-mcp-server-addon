from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


@Tool(
    "modelStyling",
    "Get the CSS styling for a specific note type (model). This CSS is used when rendering cards of this type.",
)
def model_styling(model_name: str) -> dict[str, Any]:
    col = get_col()

    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f'Model "{model_name}" not found',
            hint="Use modelNames tool to see available models",
            model_name=model_name,
        )

    css = model.get("css", "")
    if not css:
        raise HandlerError(
            f'Model "{model_name}" has no styling',
            hint="This model exists but has no CSS styling defined",
            model_name=model_name,
        )

    return {
        "model_name": model_name,
        "css": css,
        "css_info": {
            "length": len(css),
            "has_card_styling": ".card" in css,
            "has_front_styling": ".front" in css,
            "has_back_styling": ".back" in css,
            "has_cloze_styling": ".cloze" in css,
        },
        "message": f'Retrieved CSS styling for model "{model_name}"',
        "hint": "This CSS is automatically applied when cards of this type are rendered in Anki",
    }
