"""Remove a field from a note type (model)."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col
from ..._model_helpers import get_model_copy_or_raise
from ._helpers import FULL_SYNC_WARNING, field_names, resolve_field_or_raise


def remove_field_impl(model_name: str, field_name: str) -> dict[str, Any]:
    col = get_col()

    model = get_model_copy_or_raise(col, model_name)

    # --- Pre-flight validation (before touching the backend) ---
    # Resolve from the SAME copy so the identity-based remove finds the field.
    field = resolve_field_or_raise(col, model, model_name, field_name)

    if len(model.get("flds", [])) <= 1:
        raise HandlerError(
            f'Cannot remove the last field of model "{model_name}"',
            hint="A note type must keep at least one field. Add another field before removing this one.",
            model_name=model_name,
            field_name=field_name,
        )

    # --- Mutate the copy, then persist ---
    col.models.remove_field(model, field)
    col.models.update_dict(model)

    return {
        "model_name": model_name,
        "field_name": field_name,
        "fields": field_names(model),
        "message": f'Removed field "{field_name}" from model "{model_name}"',
        "warning": FULL_SYNC_WARNING,
    }
