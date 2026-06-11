from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


@Tool(
    "model_templates",
    "Get the card template HTML (Front and Back) for each card type in a note type (model). "
    "Returns the raw HTML templates used to render cards during review and editing.",
)
def model_templates(model_name: str) -> dict[str, Any]:
    col = get_col()

    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f'Model "{model_name}" not found',
            hint="Use model_names tool to see available models",
            model_name=model_name,
        )

    tmpls = model.get("tmpls", [])
    if not tmpls:
        raise HandlerError(
            f'Model "{model_name}" has no card templates',
            hint="This model exists but has no card templates defined",
            model_name=model_name,
        )

    templates: dict[str, dict[str, str]] = {}
    for tmpl in tmpls:
        name = tmpl.get("name", f"Card {tmpl.get('ord', '?')}")
        templates[name] = {
            "Front": tmpl.get("qfmt", ""),
            "Back": tmpl.get("afmt", ""),
        }

    return {
        "model_name": model_name,
        "templates": templates,
        "template_count": len(templates),
        "message": f'Retrieved {len(templates)} card template(s) for model "{model_name}"',
        "hint": "Front and Back fields contain the raw HTML/CSS templates used to render cards. "
                "Template placeholders like {{FieldName}} are substituted with field values at render time.",
    }
