# primitives/prompts.py
"""Central prompt registration module."""

# Import all prompts
from .essential.prompts.review_session_prompt import register_review_session_prompt


def register_all_prompts(mcp) -> None:
    """Register all MCP prompts with the server.

    Note:
        Prompts don't need call_main_thread as they just generate
        text templates - they don't access Anki data directly.

    Args:
        mcp: FastMCP server instance
    """
    # Register essential prompts
    register_review_session_prompt(mcp)
