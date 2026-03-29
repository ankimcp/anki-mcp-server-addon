"""Get info about filtered decks action."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col
from ..models import ORDER_MAP

# Reverse lookup: int -> human-readable order name
_REVERSE_ORDER_MAP: dict[int, str] = {v: k for k, v in ORDER_MAP.items()}


def get_info_impl(deck_ids: list[int]) -> dict[str, Any]:
    if not deck_ids:
        raise HandlerError(
            "deck_ids cannot be empty",
            hint="Provide at least one deck ID",
            code="validation_error",
        )

    if len(deck_ids) > 50:
        raise HandlerError(
            f"Maximum 50 deck IDs per request (requested: {len(deck_ids)})",
            hint="Split your request into smaller batches",
            code="limit_exceeded",
        )

    col = get_col()

    decks_data: list[dict[str, Any]] = []
    not_found = 0

    for deck_id in deck_ids:
        deck = col.decks.get(deck_id)
        if not deck or deck["id"] != deck_id:
            not_found += 1
            continue

        is_filtered = bool(deck.get("dyn"))

        if is_filtered:
            terms = deck.get("terms", [])
            search_terms = [
                {
                    "search": t[0] if isinstance(t, list) else t.get("search", ""),
                    "limit": t[1] if isinstance(t, list) else t.get("limit", 0),
                    "order": _resolve_order(
                        t[2] if isinstance(t, list) else t.get("order", 0)
                    ),
                }
                for t in terms
            ]
            reschedule = bool(deck.get("resched", True))
        else:
            search_terms = []
            reschedule = False

        card_count = len(col.decks.cids(deck_id, children=False))

        decks_data.append({
            "deck_id": deck_id,
            "name": deck["name"],
            "search_terms": search_terms,
            "reschedule": reschedule,
            "card_count": card_count,
            "is_filtered": is_filtered,
        })

    count = len(decks_data)
    if not_found > 0:
        message = (
            f"Retrieved info for {count} deck(s). "
            f"{not_found} deck(s) not found."
        )
    else:
        message = f"Retrieved info for {count} deck(s)"

    return {
        "decks": decks_data,
        "count": count,
        "not_found": not_found,
        "message": message,
    }


def _resolve_order(order_value: int) -> str:
    """Convert integer order value to human-readable string."""
    return _REVERSE_ORDER_MAP.get(order_value, f"unknown({order_value})")
