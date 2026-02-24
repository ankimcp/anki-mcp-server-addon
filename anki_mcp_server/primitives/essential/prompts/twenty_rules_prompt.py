# primitives/essential/prompts/twenty_rules_prompt.py
"""Twenty rules prompt - guides LLM to create effective Anki flashcards."""

from pathlib import Path

from ....prompt_decorator import Prompt

_CONTENT_FILE = Path(__file__).parent / "twenty_rules_content.md"

try:
    _CONTENT = _CONTENT_FILE.read_text(encoding="utf-8")
except OSError:
    _CONTENT = (
        "Error: The twenty rules content file (twenty_rules_content.md) could not be loaded. "
        "The AnkiMCP Server addon may be installed incorrectly. "
        "Please reinstall from AnkiWeb (code 124672614) or from a GitHub release. "
        "For reference, these rules are based on Dr. Piotr Wozniak's "
        '"Twenty Rules of Formulating Knowledge": '
        "https://www.supermemo.com/en/blog/twenty-rules-of-formulating-knowledge"
    )


# ============================================================================
# MCP PROMPT - Runs in background thread
# ============================================================================

@Prompt(
    "twenty_rules",
    "Twenty rules of formulating knowledge for effective Anki flashcard creation "
    "based on Dr. Piotr Wozniak's SuperMemo research"
)
def twenty_rules() -> str:
    """Generate a prompt based on Dr. Wozniak's twenty rules of formulating knowledge.

    Returns a structured prompt that guides the LLM to help users create
    high-quality Anki flashcards following SuperMemo's evidence-based principles.

    Returns:
        A formatted prompt string with the twenty rules and workflow guidance
    """
    return _CONTENT
