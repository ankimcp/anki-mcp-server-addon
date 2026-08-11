from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import get_col
from ....schema_state import FULL_SYNC_FLAG_DOC, attach_full_sync_flag
from ._model_helpers import (
    LATEX_POST_KEY,
    LATEX_PRE_KEY,
    LATEX_SVG_KEY,
    get_model_copy_or_raise,
)
from ._patch_helpers import (
    JSON_LITERAL_CAVEAT,
    apply_patch,
    require_patch_pair,
    select_mode,
)


@Tool(
    "update_model_styling",
    "Update the CSS styling (and optionally the LaTeX preamble) of an existing "
    "note type (model). Changes apply to all cards using this model.\n\n"
    "TWO MUTUALLY EXCLUSIVE CSS INPUT MODES -- use exactly one, or neither when "
    "only writing LaTeX fields:\n"
    "  1. FULL REPLACE: pass 'css' with the complete new stylesheet.\n"
    "  2. PATCH: pass 'old_str' + 'new_str' to replace a snippet in place, like "
    "a text editor's find-and-replace. 'old_str' must match the current CSS "
    "EXACTLY ONCE (whitespace is significant); 0 matches or 2+ matches is an "
    "error and nothing is written. On a 0-match error the response includes an "
    "excerpt of the current CSS so you can correct 'old_str' and retry without "
    "a separate read. Prefer PATCH for small edits -- it avoids resending the "
    "whole stylesheet and cannot silently clobber concurrent changes.\n"
    "  " + JSON_LITERAL_CAVEAT + "\n\n"
    "OPTIONAL LATEX PREAMBLE: 'latex_pre', 'latex_post' and 'latex_svg' set the "
    "LaTeX header/footer/SVG-rendering flag used when rendering [latex] and "
    "[$]...[/$] blocks. Only the ones you pass are written, and they combine "
    "with either CSS mode or can be sent alone. Use them to fix [latex]/TikZ "
    "rendering (e.g. adding \\usepackage{tikz} to 'latex_pre'). Most models "
    "never need these -- leave them out unless LaTeX is actually broken. Read "
    "the current values with model_styling(include_latex=true).\n\n"
    + FULL_SYNC_FLAG_DOC
    + " Editing CSS or the LaTeX preamble does not itself modify the schema, so "
      "this is typically false unless the collection was already dirty.",
    write=True,
)
def update_model_styling(
    model_name: str,
    css: str | None = None,
    old_str: str | None = None,
    new_str: str | None = None,
    latex_pre: str | None = None,
    latex_post: str | None = None,
    latex_svg: bool | None = None,
) -> dict[str, Any]:
    col = get_col()

    # --- Validate the input mode BEFORE touching the collection ---------------
    require_patch_pair(old_str, new_str, model_name=model_name)

    latex_requested = (
        latex_pre is not None or latex_post is not None or latex_svg is not None
    )

    mode = select_mode(
        full_present=css is not None,
        patch_present=old_str is not None,
        full_params="'css'",
        patch_params="'old_str'/'new_str'",
        # A LaTeX-only call touches no CSS at all, so "neither" is legal then.
        allow_neither=latex_requested,
        model_name=model_name,
    )

    model = get_model_copy_or_raise(col, model_name)

    old_css = model.get("css", "")
    old_css_length = len(old_css)

    if mode == "full":
        new_css = css or ""
    elif mode == "patch":
        # apply_patch raises before any mutation when the match is not unique.
        new_css = apply_patch(
            old_css,
            old_str or "",
            new_str or "",
            target=f'CSS of model "{model_name}"',
            read_hint="model_styling",
            model_name=model_name,
        )
    else:
        new_css = old_css

    if mode != "none":
        model["css"] = new_css

    updated_latex_fields: list[str] = []
    if latex_pre is not None:
        model[LATEX_PRE_KEY] = latex_pre
        updated_latex_fields.append("latex_pre")
    if latex_post is not None:
        model[LATEX_POST_KEY] = latex_post
        updated_latex_fields.append("latex_post")
    if latex_svg is not None:
        model[LATEX_SVG_KEY] = latex_svg
        updated_latex_fields.append("latex_svg")

    col.models.update_dict(model)

    css_length = len(new_css)

    if mode == "none":
        message = (
            f'Successfully updated LaTeX preamble for model "{model_name}" '
            f"(CSS unchanged)"
        )
    elif mode == "patch":
        message = f'Successfully patched CSS styling for model "{model_name}"'
    else:
        message = f'Successfully updated CSS styling for model "{model_name}"'

    response: dict[str, Any] = {
        "model_name": model_name,
        "css_length": css_length,
        "css_info": {
            "has_rtl_support": "direction: rtl" in new_css or "direction:rtl" in new_css,
            "has_card_styling": ".card" in new_css,
            "has_front_styling": ".front" in new_css,
            "has_back_styling": ".back" in new_css,
            "has_cloze_styling": ".cloze" in new_css,
        },
        "message": message,
    }

    if old_css:
        response["old_css_length"] = old_css_length
        response["css_length_change"] = css_length - old_css_length

    if mode == "patch":
        response["patched"] = True

    if updated_latex_fields:
        response["updated_latex_fields"] = updated_latex_fields

    # Single exit for all three modes (full / patch / latex-only), so every
    # success path carries the flag by construction.
    return attach_full_sync_flag(response, col)
