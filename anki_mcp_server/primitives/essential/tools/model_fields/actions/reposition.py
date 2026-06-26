"""Reposition a field within a note type (model)."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col
from ..._model_helpers import get_model_copy_or_raise
from ._helpers import FULL_SYNC_WARNING, field_names, resolve_field_or_raise


def reposition_field_impl(model_name: str, field_name: str, index: int) -> dict[str, Any]:
    col = get_col()

    model = get_model_copy_or_raise(col, model_name)

    # --- Pre-flight validation (before touching the backend) ---
    # Resolve from the SAME copy so the identity-based reposition finds the field.
    field = resolve_field_or_raise(col, model, model_name, field_name)

    field_count = len(model.get("flds", []))
    if not (0 <= index <= field_count - 1):
        raise HandlerError(
            f"index {index} is out of range",
            hint=f"index must be between 0 and {field_count - 1} (inclusive) for this model",
            model_name=model_name,
            field_name=field_name,
            field_count=field_count,
            available=field_names(model),
        )

    # --- Mutate the copy, then persist ---
    col.models.reposition_field(model, field, index)
    col.models.update_dict(model)

    return {
        "model_name": model_name,
        "field_name": field_name,
        "index": index,
        "fields": field_names(model),
        "message": f'Moved field "{field_name}" to position {index} in model "{model_name}"',
        "warning": FULL_SYNC_WARNING,
    }
