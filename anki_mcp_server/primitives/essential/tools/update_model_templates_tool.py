from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


@Tool(
    "update_model_templates",
    "Update the card template HTML (Front/Back) for one or more card types in a note type (model). "
    "Provide the card template name(s) and their new Front and/or Back HTML. "
    "Changes apply to all future renders; existing cards keep their generated content "
    "but will use the new template on next render.",
    write=True,
)
def update_model_templates(model_name: str, templates: dict[str, dict[str, str]]) -> dict[str, Any]:
    col = get_col()

    model = col.models.by_name(model_name)
    if model is None:
        raise HandlerError(
            f'Model "{model_name}" not found',
            hint="Use model_names tool to see available models",
            model_name=model_name,
        )

    # Build a name→template lookup for the model's existing templates
    existing_tmpls: dict[str, dict] = {}
    for t in model.get("tmpls", []):
        existing_tmpls[t.get("name", "")] = t

    if not existing_tmpls:
        raise HandlerError(
            f'Model "{model_name}" has no card templates to update',
            hint="This model exists but has no card templates defined",
            model_name=model_name,
        )

    # --- Pre-pass: reject unrecognized field keys before any mutation ---
    _VALID_TEMPLATE_KEYS = {"Front", "Back"}
    invalid_keys: list[tuple[str, str]] = []

    for card_name, fields in templates.items():
        for key in fields:
            if key not in _VALID_TEMPLATE_KEYS:
                invalid_keys.append((card_name, key))

    if invalid_keys:
        detail = "; ".join(f'"{key}" in template "{tmpl}"' for tmpl, key in invalid_keys)
        raise HandlerError(
            f"Unrecognized template key(s): {detail}",
            hint=f"Valid template keys are: {', '.join(sorted(_VALID_TEMPLATE_KEYS))}. "
                 "Only 'Front' and 'Back' are accepted (case-sensitive — 'front' or 'Answer' are rejected).",
            model_name=model_name,
            invalid_keys=[{"template": tmpl, "key": key} for tmpl, key in invalid_keys],
            valid_keys=sorted(_VALID_TEMPLATE_KEYS),
        )

    updated_count = 0
    updated_names: list[str] = []
    not_found: list[str] = []

    for card_name, fields in templates.items():
        if card_name not in existing_tmpls:
            not_found.append(card_name)
            continue

        tmpl = existing_tmpls[card_name]
        modified = False
        if "Front" in fields:
            tmpl["qfmt"] = fields["Front"]
            modified = True
        if "Back" in fields:
            tmpl["afmt"] = fields["Back"]
            modified = True

        if modified:
            updated_count += 1
            updated_names.append(card_name)

    if not_found:
        available = ", ".join(sorted(existing_tmpls.keys()))
        raise HandlerError(
            f'Card template(s) not found in model "{model_name}": {", ".join(sorted(not_found))}',
            hint=f"Available card templates for this model: {available}. "
                 f"Use model_templates to see current template names.",
            model_name=model_name,
            not_found=not_found,
            available=list(existing_tmpls.keys()),
        )

    if updated_count == 0:
        raise HandlerError(
            f'No templates were updated for model "{model_name}"',
            hint="Provide at least one card template name with Front and/or Back HTML fields",
            model_name=model_name,
        )

    col.models.update_dict(model)

    return {
        "model_name": model_name,
        "template_count": updated_count,
        "updated_templates": sorted(updated_names),
        "message": f'Updated {updated_count} card template(s) for model "{model_name}"',
        "hint": "Template changes take effect immediately for new card renders. "
                "Existing cards will use the updated templates the next time they are displayed.",
    }
