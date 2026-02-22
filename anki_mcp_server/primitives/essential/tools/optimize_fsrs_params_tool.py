"""Optimize FSRS params tool - run FSRS parameter optimization via Anki backend."""
from typing import Any
from datetime import datetime, timezone
import math

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ._fsrs_helpers import (
    detect_fsrs_version,
    find_preset_by_name,
    get_fsrs_params_from_config,
)


def _get_anki_point_version() -> int:
    try:
        from anki.utils import point_version
        return point_version()
    except ImportError:
        try:
            from anki.buildinfo import version
            parts = version.split(".")
            if len(parts) >= 2:
                return int(parts[0]) * 10000 + int(parts[1]) * 100
        except Exception:
            pass
    return 0


def _get_relearning_steps_in_day(deck_config: dict) -> int:
    # Ported from Anki TS: FsrsOptions.svelte
    num_steps = 0
    accumulated_time = 0
    for step in deck_config.get("lapse", {}).get("delays", []):
        accumulated_time += step
        if accumulated_time >= 24 * 60:
            break
        num_steps += 1
    return num_steps


def _fsrs_params_equal(params1: list[float], params2: list[float]) -> bool:
    if len(params1) != len(params2):
        return False
    return all(
        math.isclose(a, b, abs_tol=6e-5, rel_tol=0.0)
        for a, b in zip(params1, params2)
    )


@Tool(
    "optimize_fsrs_params",
    "Run FSRS parameter optimization for a deck config preset using Anki's built-in optimizer. "
    "This analyzes review history to find optimal FSRS weights. "
    "Set apply_results=False (default) for a dry run that shows what the optimized params would be. "
    "Set apply_results=True to save the optimized parameters. "
    "This operation runs synchronously and typically takes 5-30 seconds depending on review history size.",
    write=True,
)
def optimize_fsrs_params(preset_name: str, apply_results: bool = False) -> dict[str, Any]:
    col = get_col()

    config = find_preset_by_name(col, preset_name)
    if config is None:
        all_configs = col.decks.all_config()
        available = [c["name"] for c in all_configs]
        raise HandlerError(
            f"Preset not found: {preset_name}",
            hint=f"Available presets: {', '.join(available)}",
        )

    fsrs_enabled = col.get_config("fsrs", False)
    if not fsrs_enabled:
        raise HandlerError(
            "FSRS is not enabled",
            hint="Enable FSRS in Anki's deck options before optimizing parameters.",
        )

    current_version, current_params = get_fsrs_params_from_config(config)

    config_name_escaped = config["name"].replace("\\", "\\\\").replace('"', '\\"')
    search = config.get("paramSearch") or config.get("weightSearch") or f'preset:"{config_name_escaped}" -is:suspended'

    ignore_before_str = config.get("ignoreRevlogsBeforeDate") or "1970-01-01"
    try:
        ignore_before_date = datetime.fromisoformat(ignore_before_str).replace(tzinfo=timezone.utc)
        ignore_before_ms = int(ignore_before_date.timestamp() * 1000)
    except ValueError:
        ignore_before_ms = 0

    anki_version = _get_anki_point_version()
    extra_kwargs = {}

    if anki_version >= 250200:
        extra_kwargs["num_of_relearning_steps"] = _get_relearning_steps_in_day(config)

    if anki_version >= 250700:
        extra_kwargs["health_check"] = False

    try:
        from anki import scheduler_pb2  # noqa: F401
        response = col.backend.compute_fsrs_params(
            search=search,
            current_params=current_params,
            ignore_revlogs_before_ms=ignore_before_ms,
            **extra_kwargs,
        )
    except Exception as e:
        raise HandlerError(
            f"FSRS optimization failed: {e}",
            hint="Ensure the preset has enough review history. At least 400 reviews are recommended.",
        )

    optimized_params = list(response.params)
    fsrs_items = getattr(response, "fsrs_items", None)

    already_optimal = not optimized_params or _fsrs_params_equal(current_params, optimized_params)

    result = {
        "preset_name": preset_name,
        "preset_id": config["id"],
        "current_params": current_params,
        "optimized_params": optimized_params,
        "already_optimal": already_optimal,
        "search_query": search,
    }

    if fsrs_items is not None:
        result["fsrs_items"] = fsrs_items

    if apply_results and not already_optimal and optimized_params:
        fsrs_version = detect_fsrs_version()
        param_key = f"fsrsParams{fsrs_version}" if fsrs_version else "fsrsParams6"

        config = col.decks.get_config(config["id"])
        config[param_key] = optimized_params
        col.decks.update_config(config)

        result["applied"] = True
        result["param_key"] = param_key
    else:
        result["applied"] = False
        if already_optimal:
            result["message"] = "Parameters are already optimal."
        elif not apply_results:
            result["message"] = "Dry run complete. Set apply_results=True to save."

    return result
