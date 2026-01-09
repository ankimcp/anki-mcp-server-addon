from typing import Any, Callable, Optional
import inspect
import logging

logger = logging.getLogger(__name__)

# Global registry storing all prompts registered via @Prompt decorator
# Key: prompt name, Value: dict with name, description, title, func
_registry: dict[str, dict[str, Any]] = {}


# ------------------------------------------------------------------------------
# Prompt - Decorator class that registers functions as MCP prompts
# ------------------------------------------------------------------------------
# Usage:
#   @Prompt("prompt_name", "Description for AI")
#   def my_prompt(arg: str = "default") -> str:
#       return f"Template with {arg}"
#
# Parameters:
#   - name: Unique prompt identifier exposed to MCP clients
#   - description: Shown to AI to understand when/how to use the prompt
#
# What happens at import time:
#   1. Validates name is not already registered
#   2. Stores function and metadata in _registry
#   3. Returns original function unchanged
#
# Unlike @Tool(), prompts don't need:
#   - Main thread bridging (they don't access mw.col)
#   - Error handling wrappers (they just generate text)
#   - Write locks (they don't modify state)
# ------------------------------------------------------------------------------
class Prompt:
    """Decorator for MCP prompts.

    Prompts generate text templates for AI assistants. They don't access
    Anki's collection or modify state, so no main thread bridging is needed.

    Usage:
        @Prompt("name", "description")
        def my_prompt(arg: str = "default") -> str:
            return f"Template with {arg}"

        @Prompt("name", "description", title="Human-Readable Name")
        def my_prompt(arg: str = "default") -> str:
            return f"Template with {arg}"

    Args:
        name: Unique identifier for the prompt
        description: Explanation of what the prompt does (shown to AI)
        title: Optional human-readable display name (defaults to None)
    """

    def __init__(self, name: str, description: str, title: Optional[str] = None):
        self.name = name
        self.description = description
        self.title = title

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Register the decorated function as an MCP prompt."""
        if self.name in _registry:
            raise ValueError(f"Prompt already registered: {self.name}")

        _registry[self.name] = {
            "name": self.name,
            "description": self.description,
            "title": self.title,
            "func": func,
        }

        logger.debug("Registered prompt: %s", self.name)
        return func


# ------------------------------------------------------------------------------
# register_prompts - Create MCP prompts from registry
# ------------------------------------------------------------------------------
# Called once at server startup. Iterates through all registered prompts
# and creates MCP wrappers with correct signatures.
# ------------------------------------------------------------------------------
def register_prompts(mcp: Any) -> None:
    """Register all prompts with MCP server.

    Iterates through prompts collected by @Prompt decorators and registers
    them with the FastMCP server instance.

    Args:
        mcp: FastMCP server instance
    """
    for name, meta in _registry.items():
        _make_mcp_prompt(mcp, name, meta)
        logger.debug("Created MCP prompt: %s", name)


# ------------------------------------------------------------------------------
# _make_mcp_prompt - Create single MCP prompt wrapper
# ------------------------------------------------------------------------------
# Creates a wrapper function that:
#   1. Receives kwargs from MCP client
#   2. Calls the original prompt function
#   3. Returns the generated text
#
# The wrapper copies the original function's signature so MCP can
# generate the correct JSON schema for prompt parameters.
#
# CRITICAL: We must set __signature__ BEFORE calling mcp.prompt()
# because FastMCP reads the signature immediately during registration.
# Using @mcp.prompt() decorator syntax would capture wrong signature.
# ------------------------------------------------------------------------------
def _make_mcp_prompt(mcp: Any, name: str, meta: dict[str, Any]) -> None:
    """Create a single MCP prompt wrapper.

    Args:
        mcp: FastMCP server instance
        name: Prompt name for the wrapper
        meta: Prompt metadata containing func, description, and title
    """
    func = meta["func"]
    sig = inspect.signature(func)

    async def wrapper(**kwargs: Any) -> Any:
        try:
            result = func(**kwargs)
            # Support both sync and async prompt functions
            if inspect.iscoroutine(result):
                return await result
            return result
        except Exception as e:
            logger.exception("Prompt error in %s: %s", name, e)
            return f"Error generating prompt '{name}': {type(e).__name__}: {e}"

    # Set signature FIRST, before MCP registration
    wrapper.__name__ = name
    wrapper.__doc__ = func.__doc__
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = getattr(func, "__annotations__", {}).copy()

    # THEN register with MCP (reads correct signature now)
    mcp.prompt(name=name, description=meta["description"], title=meta["title"])(wrapper)
