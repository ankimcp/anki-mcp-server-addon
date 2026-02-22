"""FSRS helper utilities used by FSRS tools."""
from typing import Any
import re
import logging

logger = logging.getLogger(__name__)

MIN_FSRS_VERSION = 4


def detect_fsrs_version() -> int | None:
    """Detect the highest FSRS version supported by the current Anki build."""
    try:
        from anki import deck_config_pb2
        field_names = set(
            deck_config_pb2.DeckConfig.Config.DESCRIPTOR.fields_by_name.keys()
        )
        return max(
            (
                int(m.group(1))
                for name in field_names
                if (m := re.search(r"fsrs_params_(\d+)", name))
            ),
            default=None,
        )
    except Exception:
        logger.debug("Could not detect FSRS version via protobuf", exc_info=True)
        return None


def get_fsrs_params_from_config(config: dict) -> tuple[int | None, list[float]]:
    """Read FSRS weights from a deck config dict, trying versioned keys with fallback."""
    fsrs_version = detect_fsrs_version()
    max_version = fsrs_version if fsrs_version is not None else 6

    for version in range(max_version, MIN_FSRS_VERSION - 1, -1):
        params = config.get(f"fsrsParams{version}")
        if params and len(params) > 0:
            return version, list(params)

    legacy = config.get("fsrsWeights")
    if legacy and len(legacy) > 0:
        return None, list(legacy)

    return None, []


def find_preset_by_name(col: Any, name: str) -> dict | None:
    for conf in col.decks.all_config():
        if conf["name"].lower() == name.lower():
            return conf
    return None


def get_desired_retention(col: Any, did: int) -> float:
    # Per-deck overrides are stored as a percentage (0-100)
    deck = col.decks.get(did)
    if deck is not None:
        per_deck = deck.get("desiredRetention")
        if per_deck is not None:
            return per_deck / 100.0

    config = col.decks.config_dict_for_deck_id(did)
    return config.get("desiredRetention", 0.9)


def get_presets_with_decks(col: Any) -> list[dict[str, Any]]:
    """Map each deck config preset to its associated decks."""
    all_configs = col.decks.all_config()
    all_decks = col.decks.all_names_and_ids()

    config_map = {conf["id"]: conf for conf in all_configs}
    config_decks = {cid: [] for cid in config_map}

    for deck_pair in all_decks:
        did = deck_pair.id
        deck_name = deck_pair.name
        try:
            deck_conf = col.decks.config_dict_for_deck_id(did)
            cid = deck_conf["id"]
            if cid in config_decks:
                config_decks[cid].append({"name": deck_name, "id": did})
        except Exception:
            logger.debug("Could not get config for deck %s", deck_name, exc_info=True)

    return [
        {"config": conf, "decks": config_decks.get(cid, [])}
        for cid, conf in config_map.items()
    ]
