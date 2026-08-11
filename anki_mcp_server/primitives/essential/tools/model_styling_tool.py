from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ._model_helpers import LATEX_POST_KEY, LATEX_PRE_KEY, LATEX_SVG_KEY


@Tool(
    "model_styling",
    "Get the CSS styling for a specific note type (model). This CSS is used when rendering cards of this type. "
    "Set include_latex=true to additionally return the model's LaTeX preamble "
    "('latex_pre', 'latex_post', 'latex_svg') -- the header/footer wrapped around "
    "[latex] and [$]...[/$] blocks when rendering them. Only ask for it when "
    "diagnosing or fixing LaTeX/TikZ rendering; it is omitted by default.",
)
def model_styling(model_name: str, include_latex: bool = False) -> dict[str, Any]:
    col = get_col()

    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f'Model "{model_name}" not found',
            hint="Use model_names tool to see available models",
            model_name=model_name,
        )

    css = model.get("css", "")
    # The "no styling" error is about the CSS payload only. The LaTeX preamble
    # is stored independently of the CSS, so when the caller explicitly asked
    # for it this raise would hide readable data -- a model whose CSS was
    # cleared (update_model_styling(css="")) would become unreadable for LaTeX
    # diagnosis. Skip it and return an empty css alongside the LaTeX fields.
    if not css and not include_latex:
        raise HandlerError(
            f'Model "{model_name}" has no styling',
            hint="This model exists but has no CSS styling defined",
            model_name=model_name,
        )

    message = (
        f'Retrieved CSS styling for model "{model_name}"'
        if css
        else f'Model "{model_name}" has no CSS styling; returning its LaTeX preamble only'
    )

    response: dict[str, Any] = {
        "model_name": model_name,
        "css": css,
        "css_info": {
            "length": len(css),
            "has_card_styling": ".card" in css,
            "has_front_styling": ".front" in css,
            "has_back_styling": ".back" in css,
            "has_cloze_styling": ".cloze" in css,
        },
        "message": message,
        "hint": "This CSS is automatically applied when cards of this type are rendered in Anki",
    }

    # Opt-in only: the default response must stay exactly as it was, so these
    # keys are added ONLY when explicitly requested. Defaults mirror Anki's own
    # (serde #[serde(default)] on latex_pre/latex_post, false on latexsvg) so a
    # notetype dict missing the keys reads back as empty/false rather than KeyError.
    if include_latex:
        response["latex_pre"] = model.get(LATEX_PRE_KEY, "")
        response["latex_post"] = model.get(LATEX_POST_KEY, "")
        response["latex_svg"] = bool(model.get(LATEX_SVG_KEY, False))

    return response
