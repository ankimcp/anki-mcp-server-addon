"""Get FSRS params tool - read FSRS parameters for all presets or a specific deck."""
from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ._fsrs_helpers import (
    detect_fsrs_version,
    get_fsrs_params_from_config,
    get_desired_retention,
    get_presets_with_decks,
)


@Tool(
    "get_fsrs_params",
    "Get FSRS scheduler parameters for Anki deck presets. "
    "Returns FSRS weights, desired retention, max interval, and other settings. "
    "If deck_name is empty, returns parameters for all presets with their associated decks. "
    "If deck_name is provided, returns parameters for the preset used by that deck.",
)
def get_fsrs_params(deck_name: str = "") -> dict[str, Any]:
    col = get_col()

    fsrs_enabled = col.get_config("fsrs", False)
    fsrs_version = detect_fsrs_version()

    if deck_name:
        return _get_params_for_deck(col, deck_name, fsrs_enabled, fsrs_version)
    else:
        return _get_all_params(col, fsrs_enabled, fsrs_version)


def _format_preset(
    config: dict,
    decks: list[dict[str, Any]],
    col: Any,
    fsrs_version: int | None,
) -> dict[str, Any]:
    param_version, weights = get_fsrs_params_from_config(config)

    desired_retention = config.get("desiredRetention", 0.9)
    if decks:
        desired_retention = get_desired_retention(col, decks[0]["id"])

    result = {
        "preset_name": config["name"],
        "preset_id": config["id"],
        "fsrs_weights": weights,
        "fsrs_param_version": param_version,
        "desired_retention": desired_retention,
        "max_interval": config.get("maxIvl", 36500),
        "decks": [d["name"] for d in decks],
    }

    param_search = config.get("paramSearch") or config.get("weightSearch")
    if param_search:
        result["param_search"] = param_search

    ignore_before = config.get("ignoreRevlogsBeforeDate")
    if ignore_before:
        result["ignore_revlogs_before_date"] = ignore_before

    easy_days = config.get("easyDaysPercentages")
    if easy_days:
        result["easy_days_percentages"] = easy_days

    return result


def _get_params_for_deck(
    col: Any, deck_name: str, fsrs_enabled: bool, fsrs_version: int | None
) -> dict[str, Any]:
    all_decks = col.decks.all_names_and_ids()
    target_deck = None
    for d in all_decks:
        if d.name.lower() == deck_name.lower():
            target_deck = d
            break

    if target_deck is None:
        raise HandlerError(
            f"Deck not found: {deck_name}",
            hint="Check spelling or use list_decks to see available decks",
        )

    config = col.decks.config_dict_for_deck_id(target_deck.id)

    all_configs_with_decks = get_presets_with_decks(col)
    decks_using_preset = []
    for entry in all_configs_with_decks:
        if entry["config"]["id"] == config["id"]:
            decks_using_preset = entry["decks"]
            break

    preset_info = _format_preset(config, decks_using_preset, col, fsrs_version)

    return {
        "fsrs_enabled": fsrs_enabled,
        "fsrs_version": fsrs_version,
        "deck_name": deck_name,
        "preset": preset_info,
    }


def _get_all_params(
    col: Any, fsrs_enabled: bool, fsrs_version: int | None
) -> dict[str, Any]:
    presets_with_decks = get_presets_with_decks(col)

    presets = []
    for entry in presets_with_decks:
        config = entry["config"]
        decks = entry["decks"]
        presets.append(_format_preset(config, decks, col, fsrs_version))

    return {
        "fsrs_enabled": fsrs_enabled,
        "fsrs_version": fsrs_version,
        "presets": presets,
        "total_presets": len(presets),
    }
