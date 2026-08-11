from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ....schema_state import FULL_SYNC_FLAG_DOC, attach_full_sync_flag
from ._model_helpers import get_model_copy_or_raise
from ._patch_helpers import (
    JSON_LITERAL_CAVEAT,
    apply_patch,
    reject_empty_full_input,
    require_patch_pair,
    select_mode,
)

# Public template-side names mapped onto Anki's internal template dict keys.
_SIDE_KEYS = {"Front": "qfmt", "Back": "afmt"}


@Tool(
    "update_model_templates",
    "Update the card template HTML (Front/Back) of a note type (model). "
    "Changes apply to all future renders; existing cards keep their generated "
    "content but will use the new template on next render.\n\n"
    "TWO MUTUALLY EXCLUSIVE INPUT MODES -- use exactly one:\n"
    "  1. FULL REPLACE: pass 'templates' as {card template name: {'Front': html, "
    "'Back': html}}. Front/Back are optional per template but replace the whole "
    "side. Multiple templates can be updated in one call.\n"
    "  2. PATCH: pass 'template_name' + 'side' ('Front' or 'Back') + 'old_str' + "
    "'new_str' to replace a snippet in place, like a text editor's "
    "find-and-replace. 'old_str' must match that side's current HTML EXACTLY "
    "ONCE (whitespace is significant); 0 matches or 2+ matches is an error and "
    "nothing is written. On a 0-match error the response includes an excerpt of "
    "the current HTML so you can correct 'old_str' and retry without a separate "
    "read. Patch mode touches exactly one side of one template. Prefer PATCH "
    "for small edits -- it avoids resending the whole template and cannot "
    "silently clobber concurrent changes.\n"
    "  " + JSON_LITERAL_CAVEAT + "\n\n"
    "Use model_templates to read current template names and HTML.\n\n"
    + FULL_SYNC_FLAG_DOC
    + " Editing the HTML of existing card templates does not itself modify the "
      "schema, so this is typically false unless the collection was already "
      "dirty.",
    write=True,
)
def update_model_templates(
    model_name: str,
    templates: dict[str, dict[str, str]] | None = None,
    template_name: str | None = None,
    side: str | None = None,
    old_str: str | None = None,
    new_str: str | None = None,
) -> dict[str, Any]:
    col = get_col()

    # --- Validate the input mode BEFORE touching the collection ---------------
    require_patch_pair(old_str, new_str, model_name=model_name)

    patch_present = (
        template_name is not None or side is not None or old_str is not None
    )
    # Mode is selected on PRESENCE, not truthiness, so `templates={}` picks full
    # mode and is then rejected as "provided but empty" -- the mistake the
    # caller actually made -- instead of being misreported as "no input
    # provided". update_note_fields applies the identical rule.
    mode = select_mode(
        full_present=templates is not None,
        patch_present=patch_present,
        full_params="'templates'",
        patch_params="'template_name'/'side'/'old_str'/'new_str'",
        model_name=model_name,
    )
    if mode == "full":
        reject_empty_full_input(
            templates or {},
            full_params="'templates'",
            what="card template name",
            model_name=model_name,
        )

    model = get_model_copy_or_raise(col, model_name)

    # Build a name -> template lookup for the model's existing templates
    existing_tmpls: dict[str, dict] = {}
    for t in model.get("tmpls", []):
        existing_tmpls[t.get("name", "")] = t

    if not existing_tmpls:
        raise HandlerError(
            f'Model "{model_name}" has no card templates to update',
            hint="This model exists but has no card templates defined",
            model_name=model_name,
        )

    if mode == "patch":
        result = _patch_template(
            col, model, existing_tmpls, model_name,
            template_name, side, old_str or "", new_str or "",
        )
    else:
        result = _replace_templates(
            col, model, existing_tmpls, model_name, templates or {},
        )

    # Attached once here rather than in each branch helper, so patch and
    # full-replace results cannot diverge on the flag.
    return attach_full_sync_flag(result, col)


def _patch_template(
    col: Any,
    model: dict,
    existing_tmpls: dict[str, dict],
    model_name: str,
    template_name: str | None,
    side: str | None,
    old_str: str,
    new_str: str,
) -> dict[str, Any]:
    """Apply an exactly-once find-and-replace to one side of one card template."""
    if template_name is None or side is None:
        missing = [
            n for n, v in (("template_name", template_name), ("side", side))
            if v is None
        ]
        raise HandlerError(
            f"Patch mode requires {' and '.join(missing)}",
            hint="Pass template_name (the card template to edit), side "
                 "('Front' or 'Back'), old_str and new_str.",
            code="validation_error",
            model_name=model_name,
        )

    if side not in _SIDE_KEYS:
        raise HandlerError(
            f'Invalid side "{side}"',
            hint=f"side must be one of: {', '.join(sorted(_SIDE_KEYS))} "
                 f"(case-sensitive).",
            code="validation_error",
            model_name=model_name,
            valid_sides=sorted(_SIDE_KEYS),
        )

    if template_name not in existing_tmpls:
        available = ", ".join(sorted(existing_tmpls.keys()))
        raise HandlerError(
            f'Card template "{template_name}" not found in model "{model_name}"',
            hint=f"Available card templates for this model: {available}. "
                 f"Use model_templates to see current template names.",
            code="not_found",
            model_name=model_name,
            available=list(existing_tmpls.keys()),
        )

    tmpl = existing_tmpls[template_name]
    key = _SIDE_KEYS[side]
    current = tmpl.get(key, "")

    # Raises before any mutation when the match is not unique -- and because the
    # model is a deepcopy, a raise here cannot leak into Anki's notetype cache.
    patched = apply_patch(
        current,
        old_str,
        new_str,
        target=f'{side} of card template "{template_name}" in model "{model_name}"',
        read_hint="model_templates",
        model_name=model_name,
        template_name=template_name,
        side=side,
    )

    tmpl[key] = patched
    col.models.update_dict(model)

    return {
        "model_name": model_name,
        "template_count": 1,
        "updated_templates": [template_name],
        "patched": True,
        "side": side,
        "content_length": len(patched),
        "content_length_change": len(patched) - len(current),
        "message": f'Patched {side} of card template "{template_name}" '
                   f'in model "{model_name}"',
        "hint": "Template changes take effect immediately for new card renders. "
                "Existing cards will use the updated templates the next time they are displayed.",
    }


def _replace_templates(
    col: Any,
    model: dict,
    existing_tmpls: dict[str, dict],
    model_name: str,
    templates: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Replace whole Front/Back sides for one or more card templates."""
    # --- Pre-pass: reject unrecognized field keys before any mutation ---
    invalid_keys: list[tuple[str, str]] = []

    for card_name, fields in templates.items():
        for key in fields:
            if key not in _SIDE_KEYS:
                invalid_keys.append((card_name, key))

    if invalid_keys:
        detail = "; ".join(f'"{key}" in template "{tmpl}"' for tmpl, key in invalid_keys)
        raise HandlerError(
            f"Unrecognized template key(s): {detail}",
            hint=f"Valid template keys are: {', '.join(sorted(_SIDE_KEYS))}. "
                 "Only 'Front' and 'Back' are accepted (case-sensitive — 'front' or 'Answer' are rejected).",
            model_name=model_name,
            invalid_keys=[{"template": tmpl, "key": key} for tmpl, key in invalid_keys],
            valid_keys=sorted(_SIDE_KEYS),
        )

    # --- Pre-pass: reject unknown card-template names before any mutation ---
    # Validate every requested name up front so a mixed valid+unknown request
    # is rejected as a whole, never applying a partial set of updates.
    not_found = [card_name for card_name in templates if card_name not in existing_tmpls]

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

    updated_count = 0
    updated_names: list[str] = []

    for card_name, fields in templates.items():
        tmpl = existing_tmpls[card_name]
        modified = False
        for public_side, key in _SIDE_KEYS.items():
            if public_side in fields:
                tmpl[key] = fields[public_side]
                modified = True

        if modified:
            updated_count += 1
            updated_names.append(card_name)

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
