from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col


@Tool(
    "updateModelStyling",
    "Update the CSS styling for an existing note type (model). This changes how cards of this type are rendered in Anki. Useful for adding RTL (Right-to-Left) support, changing fonts, colors, or layout. Changes apply to all cards using this model.",
    write=True,
)
def update_model_styling(model_name: str, css: str) -> dict[str, Any]:
    col = get_col()

    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f'Model "{model_name}" not found',
            hint="Model not found. Use modelNames tool to see available models.",
            model_name=model_name,
        )

    old_css = model.get("css", "")
    old_css_length = len(old_css)

    model["css"] = css
    col.models.update_dict(model)

    css_length = len(css)

    response: dict[str, Any] = {
        "model_name": model_name,
        "css_length": css_length,
        "css_info": {
            "has_rtl_support": "direction: rtl" in css or "direction:rtl" in css,
            "has_card_styling": ".card" in css,
            "has_front_styling": ".front" in css,
            "has_back_styling": ".back" in css,
            "has_cloze_styling": ".cloze" in css,
        },
        "message": f'Successfully updated CSS styling for model "{model_name}"',
    }

    if old_css:
        response["old_css_length"] = old_css_length
        response["css_length_change"] = css_length - old_css_length

    return response
