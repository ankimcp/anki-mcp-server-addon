from typing import Any, Optional
import re

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col


@Tool(
    "createModel",
    "Create a new note type (model) in Anki with custom fields, card templates, and styling. Useful for creating specialized models like RTL (Right-to-Left) language models for Hebrew, Arabic, etc. Each model defines the structure of notes and how cards are generated from them.",
    write=True,
)
def create_model(
    model_name: str,
    in_order_fields: list[str],
    card_templates: list[dict[str, str]],
    css: Optional[str] = None,
    is_cloze: bool = False,
) -> dict[str, Any]:
    col = get_col()

    if not model_name or not model_name.strip():
        raise HandlerError("Model name cannot be empty")

    if not in_order_fields or len(in_order_fields) == 0:
        raise HandlerError("At least one field is required")

    if not card_templates or len(card_templates) == 0:
        raise HandlerError("At least one card template is required")

    existing_model = col.models.by_name(model_name)
    if existing_model is not None:
        raise HandlerError(
            f'Model "{model_name}" already exists',
            hint="A model with this name already exists. Use a different name or use modelNames tool to see existing models.",
            model_name=model_name,
        )

    for field_name in in_order_fields:
        if not field_name or not field_name.strip():
            raise HandlerError("Field names cannot be empty")

    for i, template in enumerate(card_templates):
        if "Name" not in template or not template["Name"]:
            raise HandlerError(f"Template {i} missing required 'Name' field")
        if "Front" not in template or not template["Front"]:
            raise HandlerError(f"Template {i} ('{template.get('Name', 'unnamed')}') missing required 'Front' field")
        if "Back" not in template or not template["Back"]:
            raise HandlerError(f"Template {i} ('{template.get('Name', 'unnamed')}') missing required 'Back' field")

    # Validate field references in templates (warning only)
    warnings: list[str] = []
    field_set = set(in_order_fields)

    special_fields = {
        "FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card",
        "CardFlag", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"
    }

    for template in card_templates:
        template_content = f"{template['Front']} {template['Back']}"
        field_refs = re.findall(r'\{\{([^}]+)\}\}', template_content)

        for ref in field_refs:
            field_name = ref.strip()
            if (field_name in special_fields or
                field_name.startswith("cloze:") or
                field_name.startswith("c")):
                continue

            if field_name not in field_set:
                warnings.append(
                    f'Template "{template["Name"]}" references field "{{{{{field_name}}}}}" '
                    f'which is not in in_order_fields'
                )

    mm = col.models

    if is_cloze:
        model = mm.new_cloze(model_name)
    else:
        model = mm.new(model_name)

    for field in model["flds"][:]:
        mm.remove_field(model, field)

    for field_name in in_order_fields:
        field = mm.new_field(field_name)
        mm.add_field(model, field)

    for template in model["tmpls"][:]:
        mm.remove_template(model, template)

    for template_dict in card_templates:
        template = mm.new_template(template_dict["Name"])
        template["qfmt"] = template_dict["Front"]
        template["afmt"] = template_dict["Back"]
        mm.add_template(model, template)

    if css:
        model["css"] = css

    mm.add(model)
    mm.save(model)

    model_id = model.get("id")

    response: dict[str, Any] = {
        "model_name": model_name,
        "model_id": model_id,
        "fields": in_order_fields,
        "template_count": len(card_templates),
        "has_css": bool(css),
        "is_cloze": is_cloze,
        "message": (
            f'Successfully created model "{model_name}" with '
            f'{len(in_order_fields)} fields and {len(card_templates)} template(s)'
        ),
    }

    if warnings:
        response["warnings"] = warnings
        response["message"] += ". Note: Some warnings were detected (see warnings field)."

    return response
