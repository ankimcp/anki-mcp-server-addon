"""Set FSRS params tool - update FSRS parameters on a deck config preset."""
from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ._fsrs_helpers import (
    detect_fsrs_version,
    find_preset_by_name,
    get_fsrs_params_from_config,
)

_EXPECTED_PARAM_COUNTS = {
    5: 19,
    6: 19,
}


@Tool(
    "set_fsrs_params",
    "Update FSRS parameters on a deck config preset. "
    "Can set FSRS weights, desired retention (0.70-0.99), and/or max interval. "
    "At least one parameter must be changed. Returns old/new diff for each changed field. "
    "Use get_fsrs_params first to see current values.",
    write=True,
)
def set_fsrs_params(
    preset_name: str,
    fsrs_params: list[float] = [],
    desired_retention: float = -1.0,
    max_interval: int = -1,
) -> dict[str, Any]:
    col = get_col()

    config = find_preset_by_name(col, preset_name)
    if config is None:
        all_configs = col.decks.all_config()
        available = [c["name"] for c in all_configs]
        raise HandlerError(
            f"Preset not found: {preset_name}",
            hint=f"Available presets: {', '.join(available)}",
        )

    has_params = len(fsrs_params) > 0
    has_retention = desired_retention >= 0
    has_max_interval = max_interval >= 0

    if not has_params and not has_retention and not has_max_interval:
        raise HandlerError(
            "No changes specified",
            hint="Provide at least one of: fsrs_params, desired_retention, max_interval",
        )

    changes = {}

    if has_params:
        fsrs_version = detect_fsrs_version()
        expected_count = _EXPECTED_PARAM_COUNTS.get(fsrs_version, 19) if fsrs_version else 19

        if len(fsrs_params) != expected_count:
            raise HandlerError(
                f"Invalid parameter count: got {len(fsrs_params)}, expected {expected_count} for FSRS v{fsrs_version}",
                hint=f"FSRS v{fsrs_version} requires exactly {expected_count} parameters.",
            )

        _, old_params = get_fsrs_params_from_config(config)
        param_key = f"fsrsParams{fsrs_version}" if fsrs_version else "fsrsParams6"
        config[param_key] = fsrs_params
        changes["fsrs_params"] = {
            "old": old_params,
            "new": list(fsrs_params),
            "key": param_key,
        }

    if has_retention:
        if not (0.70 <= desired_retention <= 0.99):
            raise HandlerError(
                f"Invalid desired retention: {desired_retention}",
                hint="Desired retention must be between 0.70 and 0.99.",
            )

        old_retention = config.get("desiredRetention", 0.9)
        config["desiredRetention"] = desired_retention
        changes["desired_retention"] = {
            "old": old_retention,
            "new": desired_retention,
        }

    if has_max_interval:
        if max_interval < 1:
            raise HandlerError(
                f"Invalid max interval: {max_interval}",
                hint="Max interval must be at least 1 day.",
            )

        old_max_ivl = config.get("maxIvl", 36500)
        config["maxIvl"] = max_interval
        changes["max_interval"] = {
            "old": old_max_ivl,
            "new": max_interval,
        }

    col.decks.update_config(config)

    return {
        "preset_name": preset_name,
        "preset_id": config["id"],
        "changes": changes,
        "status": "updated",
    }
