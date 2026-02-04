"""Model names tool - get all available note type names."""
from typing import Any

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import get_col


@Tool(
    "modelNames",
    "Get a list of all available note type (model) names in Anki. Use this to see what note types are available before creating notes.",
)
def model_names() -> dict[str, Any]:
    col = get_col()

    model_name_id_pairs = col.models.all_names_and_ids()
    model_names_list = [pair.name for pair in model_name_id_pairs]

    if not model_names_list:
        return {
            "message": "No note types found in Anki",
            "modelNames": [],
            "total": 0,
            "commonTypes": {
                "basic": None,
                "basicReversed": None,
                "cloze": None,
            },
        }

    common_types = {
        "basic": "Basic" if "Basic" in model_names_list else None,
        "basicReversed": "Basic (and reversed card)" if "Basic (and reversed card)" in model_names_list else None,
        "cloze": "Cloze" if "Cloze" in model_names_list else None,
    }

    return {
        "modelNames": model_names_list,
        "total": len(model_names_list),
        "message": f"Found {len(model_names_list)} note types",
        "commonTypes": common_types,
    }
