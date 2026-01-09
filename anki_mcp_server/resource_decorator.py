from typing import Any, Callable, Optional
import inspect
import logging
import re

from .handler_registry import register_handler
from .handler_wrappers import (
    HandlerError,  # noqa: F401 - re-exported for convenience
    _error_handler,
    _require_col,
)

logger = logging.getLogger(__name__)

# Global registry storing all resources registered via @Resource decorator
# Key: uri, Value: dict with uri, name, description, title, original (unwrapped), is_template, template_params
_registry: dict[str, dict[str, Any]] = {}

# Pattern to extract template variables from URI (e.g., {deck_id} from anki://deck/{deck_id}/stats)
_TEMPLATE_PARAM_PATTERN = re.compile(r"\{(\w+)\}")


# ------------------------------------------------------------------------------
# Resource - Decorator class that registers functions as MCP resources
# ------------------------------------------------------------------------------
# Usage:
#   @Resource("anki://system-info", "Description for AI", name="system_info")
#   def system_info() -> dict[str, Any]:
#       ...
#
#   # Template resources with URI parameters:
#   @Resource("anki://deck/{deck_id}/stats", "Get deck statistics", name="deck_stats")
#   def deck_stats(deck_id: str) -> dict[str, Any]:
#       ...
#
# Parameters:
#   - uri: Resource URI exposed to MCP clients (e.g., "anki://system-info")
#          Can include template variables like {deck_id}
#   - description: Shown to AI to understand what the resource provides
#   - name: Unique handler identifier (required - explicit, not derived from URI)
#   - title: Optional human-readable display name
#   - require_col: If True (default), checks collection is open before running
#
# Resources are read-only by definition - no write lock needed.
#
# What happens at import time:
#   1. Parses template variables from URI if present
#   2. Wraps with _require_col if require_col=True (collection check)
#   3. Wraps with _error_handler (catches exceptions, returns JSON)
#   4. Registers handler for main-thread dispatch
#   5. Stores in _registry for later MCP registration
# ------------------------------------------------------------------------------
class Resource:
    """Decorator for MCP resources.

    Resources provide read-only data access to AI assistants. They run on the
    Qt main thread with automatic error handling and collection checking.

    Usage:
        @Resource(
            "anki://system-info",
            "Get Anki system information",
            name="system_info"
        )
        def system_info() -> dict[str, Any]:
            from aqt import mw
            return {"anki_version": mw.col.version}

        @Resource(
            "anki://deck/{deck_id}/stats",
            "Get statistics for a specific deck",
            name="deck_stats",
            title="Deck Statistics"
        )
        def deck_stats(deck_id: str) -> dict[str, Any]:
            # deck_id is extracted from the URI template
            ...

    Args:
        uri: Resource URI exposed to MCP clients (e.g., "anki://system-info")
             Can include template variables like {deck_id}
        description: Explanation of what the resource provides (shown to AI)
        name: Unique handler identifier for main-thread dispatch (required)
        title: Optional human-readable display name (defaults to None)
        require_col: Whether to check collection is open (default True)
    """

    def __init__(
        self,
        uri: str,
        description: str,
        name: str,
        *,
        title: Optional[str] = None,
        require_col: bool = True,
    ):
        self.uri = uri
        self.description = description
        self.name = name
        self.title = title
        self.require_col = require_col

        # Parse template variables from URI
        self.template_params = _TEMPLATE_PARAM_PATTERN.findall(uri)
        self.is_template = len(self.template_params) > 0

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Register the decorated function as an MCP resource."""
        # Prevent duplicate registration
        if self.uri in _registry:
            raise ValueError(f"Resource already registered: {self.uri}")

        if self.name in [meta["name"] for meta in _registry.values()]:
            raise ValueError(f"Resource handler name already registered: {self.name}")

        # Stack wrappers from inside out
        # Execution order: _error_handler -> _require_col -> func
        wrapped = func

        if self.require_col:
            wrapped = _require_col(wrapped)  # Check collection is open

        wrapped = _error_handler(wrapped)  # Outermost: catch all exceptions

        # Preserve original metadata for MCP schema generation
        wrapped.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        wrapped.__annotations__ = getattr(func, "__annotations__", {})
        wrapped.__doc__ = func.__doc__

        # Register handler for main-thread dispatch (RequestProcessor uses this)
        register_handler(self.name, wrapped)

        # Store for MCP resource creation later
        _registry[self.uri] = {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "title": self.title,
            "original": func,
            "is_template": self.is_template,
            "template_params": self.template_params,
        }

        logger.debug("Registered resource: %s (handler: %s, template: %s)",
                     self.uri, self.name, self.is_template)
        return func  # Return original so it can be called directly for testing


# ------------------------------------------------------------------------------
# register_resources - Create MCP resources from registry
# ------------------------------------------------------------------------------
# Called once at server startup. Iterates through all registered resources
# and creates async MCP wrappers that bridge to main thread via queue.
# ------------------------------------------------------------------------------
def register_resources(mcp: Any, call_main_thread: Callable[..., Any]) -> None:
    """Register all resources with MCP server.

    Iterates through resources collected by @Resource decorators and registers
    them with the FastMCP server instance.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Async function to bridge calls to Anki's main thread
    """
    for uri, meta in _registry.items():
        _make_mcp_resource(mcp, call_main_thread, uri, meta)
        logger.debug("Created MCP resource: %s", uri)


# ------------------------------------------------------------------------------
# _make_mcp_resource - Create single async MCP resource wrapper
# ------------------------------------------------------------------------------
# Creates an async function that:
#   1. Receives kwargs from MCP client (including template params for templates)
#   2. Calls call_main_thread() to dispatch to Qt main thread
#   3. Returns result back to MCP client
#
# For template resources, URI parameters are passed as function arguments.
#
# The wrapper copies the original function's signature so MCP can
# generate the correct JSON schema for resource parameters.
#
# CRITICAL: We must set __signature__ BEFORE calling mcp.resource()
# because FastMCP reads the signature immediately during registration.
# Using @mcp.resource() decorator syntax would capture wrong signature.
# ------------------------------------------------------------------------------
def _make_mcp_resource(
    mcp: Any,
    call_main_thread: Callable[..., Any],
    uri: str,
    meta: dict[str, Any],
) -> None:
    """Create a single MCP resource wrapper.

    Args:
        mcp: FastMCP server instance
        call_main_thread: Async function to bridge calls to main thread
        uri: Resource URI (may be a template)
        meta: Resource metadata containing name, description, title, original, is_template, template_params
    """
    original = meta["original"]
    sig = inspect.signature(original)
    handler_name = meta["name"]  # Capture in closure for async wrapper

    async def wrapper(**kwargs: Any) -> Any:
        return await call_main_thread(handler_name, kwargs)

    # Set signature FIRST, before MCP registration
    wrapper.__name__ = handler_name
    wrapper.__doc__ = original.__doc__
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = getattr(original, "__annotations__", {}).copy()

    # THEN register with MCP (reads correct signature now)
    mcp.resource(uri, description=meta["description"], title=meta["title"])(wrapper)
