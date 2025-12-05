"""List decks tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine
import logging

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _list_decks_handler(include_stats: bool = False) -> dict[str, Any]:
    """
    List all Anki decks with optional statistics.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        include_stats: If True, include card count statistics for each deck

    Returns:
        dict: Response with structure:
            - success (bool): Always True for successful operations
            - decks (list[DeckInfo]): List of deck information
            - total (int): Total number of decks
            - summary (dict, optional): Overall statistics if include_stats=True
            - message (str, optional): Info message if no decks found

    Raises:
        RuntimeError: If collection is not loaded
    """
    from aqt import mw

    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Get all deck names and IDs
    # all_names_and_ids returns a sequence of NamedTuples with 'name' and 'id'
    deck_name_id_pairs = mw.col.decks.all_names_and_ids()

    if not deck_name_id_pairs:
        return {
            "success": True,
            "message": "No decks found in Anki",
            "decks": [],
            "total": 0,
        }

    decks: list[dict[str, Any]] = []

    if include_stats:
        # Initialize summary counters
        summary = {
            "total_cards": 0,
            "new_cards": 0,
            "learning_cards": 0,
            "review_cards": 0,
        }

        # Get stats for each deck
        for deck_pair in deck_name_id_pairs:
            deck_name = deck_pair.name
            deck_id = deck_pair.id

            # Get deck statistics
            # Use the scheduler's counts for this specific deck
            try:
                # Get counts for this deck (new, learning, review)
                # counts() returns tuple: (new, lrn, rev)
                counts = mw.col.sched.counts_for_deck_today(deck_id)
                new_count = counts[0] if len(counts) > 0 else 0
                learn_count = counts[1] if len(counts) > 1 else 0
                review_count = counts[2] if len(counts) > 2 else 0

                # Get total cards in deck using card count
                total_cards = mw.col.db.scalar(
                    "SELECT count() FROM cards WHERE did = ? OR odid = ?",
                    deck_id, deck_id
                ) or 0

                deck_info: dict[str, Any] = {
                    "name": deck_name,
                    "stats": {
                        "deck_id": deck_id,
                        "name": deck_name,
                        "new_count": new_count,
                        "learn_count": learn_count,
                        "review_count": review_count,
                        "total_new": new_count,
                        "total_cards": total_cards,
                    }
                }

                # Update summary
                summary["total_cards"] += total_cards
                summary["new_cards"] += new_count
                summary["learning_cards"] += learn_count
                summary["review_cards"] += review_count

            except Exception as e:
                # If we can't get stats for a deck, include it without stats
                logger.warning(f"Could not get stats for deck {deck_name}: {e}")
                deck_info = {"name": deck_name}

            decks.append(deck_info)

        return {
            "success": True,
            "decks": decks,
            "total": len(decks),
            "summary": summary,
        }
    else:
        # Just return deck names without stats
        decks = [{"name": deck_pair.name} for deck_pair in deck_name_id_pairs]
        return {
            "success": True,
            "decks": decks,
            "total": len(decks),
        }


# Register handler at import time
register_handler("list_decks", _list_decks_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_list_decks_tool(
    mcp,
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register the list_decks MCP tool."""

    @mcp.tool(
        description="List all available Anki decks, optionally with statistics. Remember to sync first at the start of a review session for latest data."
    )
    async def list_decks(include_stats: bool = False) -> dict[str, Any]:
        """List all available Anki decks with optional statistics.

        Lists all decks in the current Anki collection. When include_stats is True,
        provides detailed card count information for each deck including new, learning,
        and review cards.

        Args:
            include_stats: If True, include card count statistics for each deck.
                Default is False for faster response when stats aren't needed.

        Returns:
            Dictionary containing:
            - success (bool): Always True for successful operations
            - decks (list): List of deck objects, each containing:
                - name (str): Deck name
                - stats (dict, optional): Statistics if include_stats=True, containing:
                    - deck_id (int): Unique deck identifier
                    - name (str): Deck name
                    - new_count (int): Number of new cards available for review
                    - learn_count (int): Number of cards in learning phase
                    - review_count (int): Number of cards due for review
                    - total_new (int): Total new cards (same as new_count)
                    - total_cards (int): Total number of cards in deck
            - total (int): Total number of decks
            - summary (dict, optional): Overall statistics if include_stats=True:
                - total_cards (int): Sum of all cards across all decks
                - new_cards (int): Sum of all new cards
                - learning_cards (int): Sum of all learning cards
                - review_cards (int): Sum of all review cards

        Raises:
            Exception: If the main thread returns an error response

        Example:
            >>> # Get just deck names
            >>> result = await list_decks()
            >>> print(result['decks'])  # [{'name': 'Default'}, {'name': 'Languages'}]
            >>>
            >>> # Get decks with statistics
            >>> result = await list_decks(include_stats=True)
            >>> for deck in result['decks']:
            ...     print(f"{deck['name']}: {deck['stats']['new_count']} new")

        Note:
            - This operation accesses the Anki collection on the main thread
            - For accurate data, sync before calling this at the start of a session
            - Stats include only cards that are currently due/available
            - Suspended and buried cards are not included in counts
        """
        return await call_main_thread("list_decks", {"include_stats": include_stats})
