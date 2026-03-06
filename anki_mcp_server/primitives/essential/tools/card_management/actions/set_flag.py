"""SetFlag action implementation for card_management tool."""
from typing import Any

from ......handler_wrappers import HandlerError, get_col


def set_flag_impl(card_ids: list[int], flag: int) -> dict[str, Any]:
    """Set or remove a colored flag on cards.

    Args:
        card_ids: Card IDs to flag
        flag: Flag value (0=none/remove, 1=red, 2=orange, 3=green, 4=blue, 5-7=custom)

    Returns:
        Dict with flagged count, card IDs, flag value, and message

    Raises:
        HandlerError: If flag value is not in range 0-7
    """
    if flag < 0 or flag > 7:
        raise HandlerError(
            f"Invalid flag value: {flag}",
            hint="Flag must be 0 (none/remove), 1 (red), 2 (orange), 3 (green), 4 (blue), or 5-7 (custom)",
            flag=flag,
        )

    col = get_col()
    result = col.set_user_flag_for_cards(flag, card_ids)

    flag_names = {0: "none", 1: "red", 2: "orange", 3: "green", 4: "blue"}
    flag_label = flag_names.get(flag, f"custom ({flag})")

    action = "Removed flag from" if flag == 0 else f"Set {flag_label} flag on"
    return {
        "flagged_count": result.count,
        "card_ids": card_ids,
        "flag": flag,
        "message": f"{action} {result.count} card(s)",
    }
