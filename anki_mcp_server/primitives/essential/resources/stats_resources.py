# primitives/essential/resources/stats_resources.py
"""Stats resources - today's stats, forecast, and collection statistics."""

from typing import Any

from anki_mcp_server.resource_decorator import Resource


# ============================================================================
# TODAY'S STUDY STATISTICS
# ============================================================================
@Resource(
    "anki://stats/today",
    "Get today's study statistics including cards studied, time spent, and ratings breakdown.",
    name="stats_today",
    title="Today's Study Statistics",
)
def stats_today() -> dict[str, Any]:
    """Get today's study statistics.

    Returns statistics for the current study day, including cards studied,
    time spent, and breakdown by rating.

    Returns:
        dict: Today's statistics with:
            - cards_studied (int): Number of cards reviewed today
            - cards_remaining (dict): Counts by type {new, learning, review}
            - time_spent_minutes (float): Total study time in minutes
            - learning_reviews (int): Learning reviews done today
            - reviews_done (int): Review cards answered today
            - again_count (int): Number of 'Again' ratings today
            - ratings (dict): Breakdown by rating {again, hard, good, easy}
            - study_day (str): Current study day in YYYY-MM-DD format

    Example:
        >>> stats = await read_resource("anki://stats/today")
        >>> print(f"Studied {stats['cards_studied']} cards in {stats['time_spent_minutes']:.1f} min")

    Note:
        - Statistics reset at the configured day boundary (usually 4am)
        - Includes all decks unless filtered
    """
    from aqt import mw

    col = mw.col

    # Get day cutoff timestamp (start of today in Anki's time)
    day_cutoff = col.sched.day_cutoff
    day_start_ms = (day_cutoff - 86400) * 1000  # Previous cutoff is start of today

    # Query revlog for today's reviews
    # revlog.id is millisecond timestamp of review
    reviews_today = col.db.all(
        """
        SELECT ease, time, type FROM revlog
        WHERE id >= ?
        """,
        day_start_ms,
    )

    # Count ratings
    ratings = {"again": 0, "hard": 0, "good": 0, "easy": 0}
    total_time_ms = 0
    learning_reviews = 0
    reviews_done = 0

    for ease, time_ms, review_type in reviews_today:
        total_time_ms += time_ms

        # ease: 1=Again, 2=Hard, 3=Good, 4=Easy
        if ease == 1:
            ratings["again"] += 1
        elif ease == 2:
            ratings["hard"] += 1
        elif ease == 3:
            ratings["good"] += 1
        elif ease == 4:
            ratings["easy"] += 1

        # review_type: 0=learn, 1=review, 2=relearn, 3=filtered, 4=manual
        if review_type == 0:
            learning_reviews += 1
        elif review_type == 1:
            reviews_done += 1

    cards_studied = len(reviews_today)
    time_spent_minutes = total_time_ms / 60000  # Convert ms to minutes

    # Get remaining cards for today using scheduler counts
    counts = col.sched.counts()
    cards_remaining = {
        "new": counts[0] if len(counts) > 0 else 0,
        "learning": counts[1] if len(counts) > 1 else 0,
        "review": counts[2] if len(counts) > 2 else 0,
    }

    # Format current study day
    from datetime import datetime, timezone

    study_day = datetime.fromtimestamp(day_cutoff - 86400, tz=timezone.utc).strftime(
        "%Y-%m-%d"
    )

    return {
        "cards_studied": cards_studied,
        "cards_remaining": cards_remaining,
        "time_spent_minutes": round(time_spent_minutes, 2),
        "learning_reviews": learning_reviews,
        "reviews_done": reviews_done,
        "again_count": ratings["again"],
        "ratings": ratings,
        "study_day": study_day,
    }


# ============================================================================
# 30-DAY REVIEW FORECAST
# ============================================================================
@Resource(
    "anki://stats/forecast",
    "Get 30-day review forecast showing expected due cards per day.",
    name="stats_forecast",
    title="30-Day Review Forecast",
)
def stats_forecast() -> dict[str, Any]:
    """Get 30-day review forecast.

    Projects how many cards will be due each day for the next 30 days
    based on current scheduling data. Helps plan study time.

    Returns:
        dict: Forecast data with:
            - forecast (list): Array of {day: int, due: int} for days 1-30
            - total_due (int): Sum of all due cards over 30 days
            - peak_day (dict): Day with most cards {day: int, due: int}
            - average_per_day (float): Average cards due per day
            - generated_at (str): ISO timestamp when forecast was generated

    Example:
        >>> forecast = await read_resource("anki://stats/forecast")
        >>> tomorrow = forecast['forecast'][0]
        >>> print(f"Tomorrow: {tomorrow['due']} cards due")

    Note:
        - Forecast is based on current card intervals and due dates
        - Does not account for new cards that will be introduced
        - Accuracy decreases further into the future
    """
    from datetime import datetime, timezone

    from aqt import mw

    col = mw.col

    # Get today's day number (days since collection creation epoch)
    today = col.sched.today

    # Query cards table for due dates over next 30 days
    # For review queue (queue=2), due is day number
    forecast_data: list[dict[str, int]] = []
    total_due = 0

    # Single query for all 30 days
    due_by_day = col.db.all(
        """
        SELECT due - ?, count() FROM cards
        WHERE queue = 2 AND due > ? AND due <= ?
        GROUP BY due
        """,
        today, today, today + 30,
    )

    # Convert to dict for easy lookup
    due_map = {day_offset: count for day_offset, count in due_by_day}

    # Build forecast with 0 for days with no due cards
    for day_offset in range(1, 31):
        due_count = due_map.get(day_offset, 0)
        forecast_data.append({"day": day_offset, "due": due_count})
        total_due += due_count

    # Find peak day
    peak = max(forecast_data, key=lambda x: x["due"]) if forecast_data else {"day": 0, "due": 0}

    return {
        "forecast": forecast_data,
        "total_due": total_due,
        "peak_day": {"day": peak["day"], "due": peak["due"]},
        "average_per_day": round(total_due / 30, 2) if total_due else 0.0,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ============================================================================
# COLLECTION STATISTICS
# ============================================================================
@Resource(
    "anki://stats/collection",
    "Get overall collection statistics including total notes, cards, and state breakdown.",
    name="stats_collection",
    title="Collection Statistics",
)
def stats_collection() -> dict[str, Any]:
    """Get overall collection statistics.

    Provides a comprehensive overview of the entire Anki collection
    including totals, card states, and deck information.

    Returns:
        dict: Collection statistics with:
            - total_notes (int): Total number of notes in collection
            - total_cards (int): Total number of cards in collection
            - total_decks (int): Total number of decks
            - total_models (int): Total number of note types (models)
            - cards_by_state (dict): Card counts by queue state
                - new (int): Cards never studied
                - learning (int): Cards in learning phase
                - review (int): Graduated cards in review
                - suspended (int): Manually suspended cards
                - buried (int): Temporarily hidden cards
            - cards_by_type (dict): Card counts by type
                - new (int): New cards (type=0)
                - learning (int): Learning cards (type=1)
                - review (int): Review cards (type=2)
                - relearning (int): Relearning cards (type=3)
            - mature_cards (int): Cards with interval >= 21 days
            - young_cards (int): Review cards with interval < 21 days
            - average_ease (float): Average ease factor (as percentage, e.g., 250.0)
            - total_reviews (int): Total reviews ever done

    Example:
        >>> stats = await read_resource("anki://stats/collection")
        >>> print(f"Collection: {stats['total_notes']} notes, {stats['total_cards']} cards")

    Note:
        - 'Mature' cards have interval >= 21 days (Anki's standard threshold)
        - Average ease excludes new cards (which have ease factor 0)
    """
    from aqt import mw

    col = mw.col

    # Total counts
    total_notes = col.note_count()
    total_cards = col.card_count()
    total_decks = len(col.decks.all_names_and_ids())
    total_models = len(col.models.all_names_and_ids())

    # Cards by queue state
    # queue: 0=new, 1=learning, 2=review, 3=day-learn, -1=suspended, -2/-3=buried
    queue_counts = col.db.all(
        """
        SELECT queue, count() FROM cards GROUP BY queue
        """
    )

    cards_by_state = {
        "new": 0,
        "learning": 0,
        "review": 0,
        "suspended": 0,
        "buried": 0,
    }

    for queue, count in queue_counts:
        if queue == 0:
            cards_by_state["new"] = count
        elif queue in (1, 3):  # learning and day-learning
            cards_by_state["learning"] += count
        elif queue == 2:
            cards_by_state["review"] = count
        elif queue == -1:
            cards_by_state["suspended"] = count
        elif queue in (-2, -3):  # user-buried and scheduler-buried
            cards_by_state["buried"] += count

    # Cards by type
    type_counts = col.db.all(
        """
        SELECT type, count() FROM cards GROUP BY type
        """
    )

    cards_by_type = {
        "new": 0,
        "learning": 0,
        "review": 0,
        "relearning": 0,
    }

    for card_type, count in type_counts:
        if card_type == 0:
            cards_by_type["new"] = count
        elif card_type == 1:
            cards_by_type["learning"] = count
        elif card_type == 2:
            cards_by_type["review"] = count
        elif card_type == 3:
            cards_by_type["relearning"] = count

    # Mature vs young cards (mature = interval >= 21 days)
    mature_cards = col.db.scalar(
        """
        SELECT count() FROM cards WHERE ivl >= 21
        """
    ) or 0

    young_cards = col.db.scalar(
        """
        SELECT count() FROM cards WHERE ivl > 0 AND ivl < 21
        """
    ) or 0

    # Average ease factor (excluding new cards which have factor=0)
    # Factor is stored in permille (2500 = 250%)
    avg_ease_permille = col.db.scalar(
        """
        SELECT avg(factor) FROM cards WHERE factor > 0
        """
    )
    average_ease = round(avg_ease_permille / 10, 1) if avg_ease_permille else 0.0

    # Total reviews ever
    total_reviews = col.db.scalar(
        """
        SELECT count() FROM revlog
        """
    ) or 0

    return {
        "total_notes": total_notes,
        "total_cards": total_cards,
        "total_decks": total_decks,
        "total_models": total_models,
        "cards_by_state": cards_by_state,
        "cards_by_type": cards_by_type,
        "mature_cards": mature_cards,
        "young_cards": young_cards,
        "average_ease": average_ease,
        "total_reviews": total_reviews,
    }
