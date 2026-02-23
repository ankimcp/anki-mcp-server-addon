"""Get card memory state tool - read FSRS memory state for individual cards."""
from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)

_QUEUE_NAMES = {
    -1: "suspended",
    -2: "sibling_buried",
    -3: "manually_buried",
    0: "new",
    1: "learning",
    2: "review",
    3: "day_learning",
    4: "preview",
}

_TYPE_NAMES = {
    0: "new",
    1: "learning",
    2: "review",
    3: "relearning",
}


@Tool(
    "get_card_memory_state",
    "Get FSRS memory state (stability, difficulty, retrievability) for one or more cards. "
    "Requires FSRS to be enabled. Returns per-card memory state along with scheduling info. "
    "Use recompute=True to recalculate from the review log (slower but ensures accuracy).",
)
def get_card_memory_state(card_ids: list[int], recompute: bool = False) -> dict[str, Any]:
    col = get_col()

    if not card_ids:
        raise HandlerError(
            "No card IDs provided",
            hint="Provide at least one card ID. Use findNotes or card_management to find card IDs.",
        )

    fsrs_enabled = col.get_config("fsrs", False)
    if not fsrs_enabled:
        raise HandlerError(
            "FSRS is not enabled",
            hint="Enable FSRS in Anki's deck options before using this tool.",
        )

    cards = []
    not_found = []

    for cid in card_ids:
        try:
            card = col.get_card(cid)
        except Exception:
            not_found.append(cid)
            continue

        card_info = _extract_card_state(col, card, recompute)
        cards.append(card_info)

    result = {
        "cards": cards,
        "total": len(cards),
    }

    if not_found:
        result["not_found"] = not_found

    return result


def _extract_card_state(col: Any, card: Any, recompute: bool) -> dict[str, Any]:
    cid = card.id

    info = {
        "card_id": cid,
        "interval": card.ivl,
        "due": card.due,
        "queue": _QUEUE_NAMES.get(card.queue, f"unknown({card.queue})"),
        "type": _TYPE_NAMES.get(card.type, f"unknown({card.type})"),
        "reps": card.reps,
        "lapses": card.lapses,
    }

    memory_state = None
    if recompute:
        try:
            memory_state = col.compute_memory_state(cid)
        except AttributeError:
            logger.debug("col.compute_memory_state not available, falling back to card.memory_state")
            memory_state = getattr(card, "memory_state", None)
        except Exception:
            logger.debug("Failed to compute memory state for card %d", cid, exc_info=True)
            memory_state = getattr(card, "memory_state", None)
    else:
        memory_state = getattr(card, "memory_state", None)

    if memory_state is not None:
        info["stability"] = getattr(memory_state, "stability", None)
        info["difficulty"] = getattr(memory_state, "difficulty", None)

        stability = info.get("stability")
        if stability and stability > 0 and card.ivl > 0 and card.type == 2:
            today = col.sched.today
            elapsed_days = today - (card.due - card.ivl)
            info["elapsed_days"] = elapsed_days
            if elapsed_days >= 0:
                # FSRS-5+ power-forgetting curve: R = (1 + elapsed/9S)^-1
                info["retrievability"] = round(
                    (1 + elapsed_days / (9 * stability)) ** (-1), 4
                )
    else:
        info["stability"] = None
        info["difficulty"] = None

    return info
