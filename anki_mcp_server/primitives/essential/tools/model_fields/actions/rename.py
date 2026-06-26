"""Rename a field on a note type (model)."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col
from ..._model_helpers import get_model_copy_or_raise
from ._helpers import FULL_SYNC_WARNING, field_names, resolve_field_or_raise


def rename_field_impl(model_name: str, field_name: str, new_name: str) -> dict[str, Any]:
    col = get_col()

    model = get_model_copy_or_raise(col, model_name)

    target = new_name.strip()
    if not target:
        raise HandlerError(
            "New field name must not be empty",
            hint="Provide a non-empty new field name",
            model_name=model_name,
            field_name=field_name,
        )

    # --- Pre-flight validation (before touching the backend) ---
    # Resolve from the SAME copy so the identity-based rename finds the field.
    field = resolve_field_or_raise(col, model, model_name, field_name)

    # Reject a collision with a DIFFERENT existing field (case-insensitive); otherwise
    # Anki would silently rename/de-duplicate to keep names unique, producing a name the
    # caller did not ask for. A pure case-change of the SAME field (e.g. "front" ->
    # "Front") is allowed.
    for other in field_names(model):
        if other == field_name:
            continue
        if other.lower() == target.lower():
            raise HandlerError(
                f'Field name "{target}" collides with an existing field in model "{model_name}"',
                hint="Anki treats field names case-insensitively for uniqueness. Choose a distinct name.",
                model_name=model_name,
                field_name=field_name,
                new_name=target,
                available=field_names(model),
            )

    # --- Mutate the copy, then persist ---
    col.models.rename_field(model, field, target)
    col.models.update_dict(model)

    return {
        "model_name": model_name,
        "old_field_name": field_name,
        "new_field_name": target,
        "fields": field_names(model),
        "message": f'Renamed field "{field_name}" to "{target}" in model "{model_name}"',
        "warning": (
            f"Card templates that reference {{{{{field_name}}}}} were NOT updated and will "
            f"break until you fix them. Update them to {{{{{target}}}}} with the "
            "update_model_templates tool. " + FULL_SYNC_WARNING
        ),
    }
