"""Shared field-lookup and result-shaping helpers for the model_fields tool.

These helpers centralize two concerns shared by every action:

1. Resolving a field dict from the SAME model copy that will be mutated. Anki's
   ModelManager.remove_field / rename_field / reposition_field look the field up
   by OBJECT IDENTITY (flds.remove / flds.index), so the field dict MUST come from
   the model copy being mutated -- never a freshly reconstructed dict.
2. Producing the not-found HandlerError with an available-field-list, matching the
   shape used by update_model_templates_tool.py.
"""
from typing import Any

from ......handler_wrappers import HandlerError

# Advisory warning attached to schema-changing results (add / remove / rename / reposition).
# These mutations bump the notetype schema, which forces Anki into a one-way
# "full sync" the next time the collection syncs. This is advisory only -- the
# addon does not change any sync behavior, it just surfaces the consequence.
FULL_SYNC_WARNING = (
    "This is a schema change. Anki will require a one-way full sync on the next "
    "sync, which overwrites the collection on AnkiWeb (and other devices) with "
    "this one. Sync your other devices first if they hold unsynced changes."
)


def field_names(model: dict) -> list[str]:
    """Return the ordered list of field names for a notetype dict."""
    return [field.get("name", "") for field in model.get("flds", [])]


def resolve_field_or_raise(col: Any, model: dict, model_name: str, field_name: str) -> dict:
    """Return the field dict for ``field_name`` from this exact model copy.

    Raises HandlerError (with the available field list) if the field is absent.
    The returned dict is the live element of ``model["flds"]`` so it can be passed
    straight to ModelManager identity-based mutators.
    """
    # field_map(model) -> {name: (ord, FieldDict)} where FieldDict is the element
    # of the SAME model dict we pass in -- preserving object identity.
    field_map = col.models.field_map(model)
    entry = field_map.get(field_name)
    if entry is None:
        available = ", ".join(field_names(model))
        raise HandlerError(
            f'Field "{field_name}" not found in model "{model_name}"',
            hint=f"Available fields for this model: {available}. "
                 "Use model_field_names to see current field names.",
            model_name=model_name,
            field_name=field_name,
            available=field_names(model),
        )
    return entry[1]
