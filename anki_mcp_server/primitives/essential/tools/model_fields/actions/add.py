"""Add a field to a note type (model)."""
from typing import Any, Optional

from ......handler_wrappers import HandlerError, get_col
from ..._model_helpers import get_model_copy_or_raise
from ._helpers import FULL_SYNC_WARNING, field_names


def add_field_impl(model_name: str, field_name: str, index: Optional[int] = None) -> dict[str, Any]:
    col = get_col()

    model = get_model_copy_or_raise(col, model_name)
    existing = field_names(model)

    name = field_name.strip()
    if not name:
        raise HandlerError(
            "Field name must not be empty",
            hint="Provide a non-empty field name",
            model_name=model_name,
        )

    # --- Pre-flight validation (before touching the backend) ---
    # Reject exact duplicates and case-insensitive collisions up front; otherwise Anki
    # would silently rename/de-duplicate the field to keep names unique, producing a
    # name the caller did not ask for.
    lowered = {n.lower() for n in existing}
    if name in existing:
        raise HandlerError(
            f'Field "{name}" already exists in model "{model_name}"',
            hint="Choose a different field name, or use the rename action to change an existing field",
            model_name=model_name,
            field_name=name,
            available=existing,
        )
    if name.lower() in lowered:
        raise HandlerError(
            f'Field name "{name}" collides with an existing field (case-insensitive) in model "{model_name}"',
            hint="Anki treats field names case-insensitively for uniqueness. Choose a distinct name.",
            model_name=model_name,
            field_name=name,
            available=existing,
        )

    # index is an insertion point: 0..len inclusive (len == append at the end).
    if index is not None and not (0 <= index <= len(existing)):
        raise HandlerError(
            f"index {index} is out of range",
            hint=f"index must be between 0 and {len(existing)} (inclusive) to add a field; "
                 "omit it to append at the end",
            model_name=model_name,
            field_count=len(existing),
            available=existing,
        )

    # --- Mutate the copy, then persist ---
    new_field = col.models.new_field(name)
    col.models.add_field(model, new_field)  # appended at the end
    if index is not None and index != len(existing):
        # new_field is the same object that was appended, so identity-based
        # reposition resolves it correctly.
        col.models.reposition_field(model, new_field, index)

    col.models.update_dict(model)

    return {
        "model_name": model_name,
        "field_name": name,
        "fields": field_names(model),
        "message": f'Added field "{name}" to model "{model_name}"',
        "warning": FULL_SYNC_WARNING,
    }
