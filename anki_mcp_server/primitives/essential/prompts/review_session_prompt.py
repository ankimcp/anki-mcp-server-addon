# primitives/essential/prompts/review_session_prompt.py
"""Review session prompt - guides LLM through conducting Anki review sessions."""

from ....prompt_decorator import Prompt


# ============================================================================
# MCP PROMPT - Runs in background thread
# ============================================================================

@Prompt(
    "review_session",
    "Creates a structured prompt for conducting an Anki review session. "
    "Use this prompt to guide the LLM through presenting cards, collecting answers, "
    "and rating cards appropriately. Helps maintain consistent review workflow."
)
def review_session(
    deck_name: str = "Default",
    card_limit: int = 20,
    review_style: str = "interactive"
) -> str:
    """Generate a review session prompt for Anki.

    Creates a structured prompt that guides the LLM through conducting
    an effective Anki review session with the user.

    Args:
        deck_name: Name of the deck to review (default: "Default")
        card_limit: Maximum number of cards to review in this session
        review_style: Review approach - "interactive" for Q&A,
                     "quick" for rapid-fire mode, or
                     "voice" for voice-only mode (skips images/audio)

    Returns:
        A formatted prompt string with review session instructions

    Example:
        >>> prompt = await get_prompt("review_session", {
        ...     "deck_name": "Spanish Vocabulary",
        ...     "card_limit": 10,
        ...     "review_style": "interactive"
        ... })
    """
    if review_style == "voice":
        style_instructions = """
VOICE REVIEW MODE:
- This is a voice-only session - the user cannot see the screen
- Use skip_images=True and skip_audio=True when calling get_due_cards
  (cards with media will be temporarily buried and skipped)
- Read the card question aloud clearly
- Wait for the user's verbal answer
- Read the correct answer and evaluate their response
- If a card contains text that references an image (e.g., "What is shown above?"),
  skip it naturally - it will be buried automatically
- Rate cards based on quality of verbal recall:
  * Again (1): Completely forgot or major errors
  * Hard (2): Struggled but got it eventually
  * Good (3): Correct with reasonable effort
  * Easy (4): Instant, effortless recall
- CRITICAL: At the end of the session, call card_management with
  action="unbury" and deck_name to restore all skipped media cards"""
    elif review_style == "quick":
        style_instructions = """
QUICK REVIEW MODE:
- Present cards rapidly with minimal discussion
- Show question, wait for user signal, show answer
- Rate based on user's quick self-assessment (Again/Hard/Good/Easy)
- Aim for efficient coverage without deep exploration"""
    else:
        style_instructions = """
INTERACTIVE REVIEW MODE:
- Present each card's question and wait for the user's answer
- After they respond, reveal the answer and discuss if needed
- Help them understand concepts they struggle with
- Provide mnemonics or explanations when helpful
- Rate cards based on quality of their recall:
  * Again (1): Completely forgot or major errors
  * Hard (2): Struggled but got it eventually
  * Good (3): Correct with reasonable effort
  * Easy (4): Instant, effortless recall"""

    return f"""You are helping the user conduct an Anki review session.

SESSION PARAMETERS:
- Deck: "{deck_name}"
- Cards to review: up to {card_limit}
- Style: {review_style}
{style_instructions}

WORKFLOW:
1. First, sync to get latest data: Use the sync tool
2. Get the next due card: Use get_due_cards with deck_name="{deck_name}"
   - get_due_cards returns ONE card at a time in true scheduler order{'''
   - Use skip_images=True and skip_audio=True to filter out media cards''' if review_style == 'voice' else ''}
3. Use present_card to show the question to the user
4. Wait for their response
5. Use present_card with show_answer=True to reveal the answer
6. Evaluate their response and suggest a rating (1-4)
7. Wait for user confirmation, then use rate_card to record their performance
8. Repeat from step 2 to get the next card
9. Continue until no more cards are due or {card_limit} cards reviewed

IMPORTANT GUIDELINES:
- Always sync before starting to ensure up-to-date card data
- Be encouraging but honest about mistakes
- If the user wants to stop early, that's fine - sync before ending
- Track progress: "Card X of Y completed"
- At the end, summarize the session (cards reviewed, performance distribution){'''
- CRITICAL: At session end, call card_management with action="unbury" and
  deck_name="''' + deck_name + '''" to restore all skipped media cards''' if review_style == 'voice' else ''}

RATING GUIDE:
- Use the rating that best reflects the user's actual recall
- Don't inflate ratings to make them feel better
- Struggling learners benefit from honest ratings (it schedules more reviews)
- If unsure, rate "Hard" rather than "Again" for partial knowledge

Begin by syncing and fetching the first due card for the "{deck_name}" deck."""
