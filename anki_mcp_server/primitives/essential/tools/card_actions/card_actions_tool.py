"""Multi-action tool for card organization operations."""
from typing import Any, Literal, Optional

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError

from .actions.reposition import reposition_impl
from .actions.change_deck import change_deck_impl


@Tool(
    "card_actions",
    """Manage card organization with two actions:

    - reposition: Reposition NEW cards in the review queue (set learning order).
      Parameters: card_ids (required), starting_from (default 0), step_size (default 1),
                  randomize (default false), shift_existing (default false)
      Note: Only works on NEW cards (queue=0). Non-new cards are silently skipped.
      Example: Reorganize imported cards to match textbook chapter order.

    - changeDeck: Move cards to a different deck (creates deck if needed).
      Parameters: card_ids (required), deck (required - target deck name)
      Note: Works with ANY card type. Use '::' for nested decks (e.g., "Spanish::Verbs").
      Example: Organize cards by chapter/topic into separate decks.""",
    write=True,
)
def card_actions(
    action: Literal["reposition", "changeDeck"],
    # Shared params
    card_ids: list[int],
    # [reposition] params
    starting_from: int = 0,
    step_size: int = 1,
    randomize: bool = False,
    shift_existing: bool = False,
    # [changeDeck] params
    deck: Optional[str] = None,
) -> dict[str, Any]:
    """Dispatcher for card action operations.

    Args:
        action: Action to perform ("reposition" or "changeDeck")
        card_ids: Card IDs to operate on (required for all actions)
        starting_from: [reposition] Starting position (default 0)
        step_size: [reposition] Increment between cards (default 1)
        randomize: [reposition] Shuffle before assigning positions (default False)
        shift_existing: [reposition] Push other cards forward (default False)
        deck: [changeDeck] Target deck name (required for changeDeck)

    Returns:
        Dict with action-specific results

    Raises:
        HandlerError: If parameters are invalid or action is unknown
    """
    if not card_ids:
        raise HandlerError(
            "card_ids is required and cannot be empty",
            hint="Provide at least one card ID",
            action=action,
        )

    if action == "reposition":
        return reposition_impl(
            card_ids=card_ids,
            starting_from=starting_from,
            step_size=step_size,
            randomize=randomize,
            shift_existing=shift_existing,
        )
    elif action == "changeDeck":
        if not deck:
            raise HandlerError(
                "deck is required for changeDeck action",
                hint="Provide the target deck name (e.g., 'Spanish' or 'Spanish::Verbs')",
                action=action,
            )
        return change_deck_impl(card_ids=card_ids, deck=deck)
    else:
        raise HandlerError(f"Unknown action: {action}")
