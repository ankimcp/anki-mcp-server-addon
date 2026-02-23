"""FSRS config resource."""
from typing import Any

from ....resource_decorator import Resource
from ....handler_wrappers import get_col
from ..tools._fsrs_helpers import (
    detect_fsrs_version,
    get_fsrs_params_from_config,
    get_presets_with_decks,
)


@Resource(
    "anki://fsrs/config",
    "FSRS configuration summary: enabled status, version, preset count, and parameter overview. "
    "Useful for quickly checking FSRS state without a full tool call.",
    name="fsrs_config",
    title="FSRS Configuration",
)
def fsrs_config() -> dict[str, Any]:
    col = get_col()

    fsrs_enabled = col.get_config("fsrs", False)
    fsrs_version = detect_fsrs_version()

    presets_with_decks = get_presets_with_decks(col)

    preset_summaries = []
    for entry in presets_with_decks:
        config = entry["config"]
        decks = entry["decks"]
        param_version, weights = get_fsrs_params_from_config(config)

        preset_summaries.append({
            "name": config["name"],
            "has_weights": len(weights) > 0,
            "param_count": len(weights),
            "param_version": param_version,
            "desired_retention": config.get("desiredRetention", 0.9),
            "deck_count": len(decks),
        })

    return {
        "fsrs_enabled": fsrs_enabled,
        "fsrs_version": fsrs_version,
        "total_presets": len(preset_summaries),
        "presets": preset_summaries,
    }
