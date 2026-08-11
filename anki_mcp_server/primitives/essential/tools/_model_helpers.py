"""Model (notetype) helper utilities used by model-mutating tools."""
import copy
from typing import Any

from ....handler_wrappers import HandlerError

# Notetype dict keys for the LaTeX preamble. These are Anki's schema11 key names
# (see rslib/src/notetype/schema11.rs RESERVED_NOTETYPE_KEYS) -- note the
# inconsistent casing: "latexPre"/"latexPost" are camelCase but "latexsvg" is
# all lowercase. Defined once here because both the reader (model_styling) and
# the writer (update_model_styling) must agree on them exactly.
LATEX_PRE_KEY = "latexPre"
LATEX_POST_KEY = "latexPost"
LATEX_SVG_KEY = "latexsvg"


def get_model_copy_or_raise(col: Any, model_name: str) -> dict:
    """Return a deepcopy of the named notetype (model), raising HandlerError if absent.

    col.models.by_name() returns a live reference to Anki's cached notetype dict;
    mutating that reference can leak partial state into the in-memory cache if the
    update is later rejected or abandoned (see issue #47). Callers that intend to
    mutate-then-update_dict() must work on a copy. This helper centralizes both the
    not-found check and the defensive deepcopy so model-mutating tools never touch
    the live cache.
    """
    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f'Model "{model_name}" not found',
            hint="Use model_names tool to see available models",
            model_name=model_name,
        )
    return copy.deepcopy(model)
