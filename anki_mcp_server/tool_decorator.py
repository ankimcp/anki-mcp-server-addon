from typing import Any, Callable, Optional
from functools import wraps
import inspect
import logging

from .handler_registry import register_handler
from .handler_wrappers import (
    HandlerError,
    _error_handler,
    _require_col,
    _get_mw,
    get_mw,
    get_col,
)

logger = logging.getLogger(__name__)

# Global registry storing all tools registered via @Tool decorator
# Key: tool name, Value: dict with name, description, original (unwrapped)
_registry: dict[str, dict[str, Any]] = {}


# ------------------------------------------------------------------------------
# Tool - Decorator class that registers functions as MCP tools
# ------------------------------------------------------------------------------
# Usage:
#   @Tool("tool_name", "Description for AI", write=True)
#   def my_tool(arg: str) -> dict:
#       ...
#
# Parameters:
#   - name: Unique tool identifier exposed to MCP clients
#   - description: Shown to AI to understand when/how to use the tool
#   - write: If True, wraps with _write_lock for Anki's undo system
#   - require_col: If True (default), checks collection is open before running
#
# What happens at import time:
#   1. Wraps with _write_lock if write=True (Anki undo handling)
#   2. Wraps with _require_col if require_col=True (collection check)
#   3. Wraps with _error_handler (catches exceptions, returns JSON)
#   4. Registers handler for main-thread dispatch
#   5. Stores in _registry for later MCP registration
# ------------------------------------------------------------------------------
class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        handler: Optional[Callable[..., Any]] = None,
        *,
        write: bool = False,
        require_col: bool = True,
    ):
        self.name = name
        self.description = description
        self.write = write
        self.require_col = require_col

        # Support both @Tool(...) decorator and Tool(..., handler=fn) direct call
        if handler is not None:
            self._register(handler)

    # Called when used as @Tool(...) decorator
    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        self._register(func)
        return func  # Return original so it can be called directly for testing

    def _register(self, func: Callable[..., Any]) -> None:
        # Prevent duplicate registration (would cause confusing behavior)
        if self.name in _registry:
            raise ValueError(f"Tool already registered: {self.name}")

        # Stack wrappers from inside out
        # Execution order: _error_handler -> _require_col -> _write_lock -> func
        wrapped = func

        if self.write:
            wrapped = _write_lock(wrapped)  # Handle Anki's undo system

        if self.require_col:
            wrapped = _require_col(wrapped)  # Check collection is open

        wrapped = _error_handler(wrapped)  # Outermost: catch all exceptions

        # Preserve original signature for MCP schema generation
        wrapped.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        wrapped.__annotations__ = getattr(func, "__annotations__", {})

        # Register for main-thread dispatch (RequestProcessor uses this)
        register_handler(self.name, wrapped)

        # Store for MCP tool creation later
        _registry[self.name] = {
            "name": self.name,
            "description": self.description,
            "original": func,
        }


# ------------------------------------------------------------------------------
# _write_lock - Handle Anki's undo system for write operations
# ------------------------------------------------------------------------------
# Calls mw.requireReset() before and mw.maybeReset() after the operation.
# This ensures Anki's UI updates and undo stack is properly maintained.
# Only applied when write=True in @Tool decorator.
# ------------------------------------------------------------------------------
def _write_lock(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        mw = _get_mw()

        # Guard against edge case where write=True but require_col=False
        if mw is None:
            raise HandlerError("Main window not available", hint="Make sure Anki is fully loaded")

        try:
            mw.requireReset()  # Mark that we're about to modify collection
            return func(*args, **kwargs)
        finally:
            # Reset UI state after write, even if exception occurred
            if mw is not None and mw.col is not None:
                mw.maybeReset()

    return wrapper


# ------------------------------------------------------------------------------
# register_tools - Create MCP tools from registry
# ------------------------------------------------------------------------------
# Called once at server startup. Iterates through all registered tools
# and creates async MCP wrappers that bridge to main thread via queue.
# ------------------------------------------------------------------------------
def register_tools(mcp: Any, call_main_thread: Callable[..., Any]) -> None:
    for name, meta in _registry.items():
        _make_mcp_tool(mcp, call_main_thread, name, meta)


# ------------------------------------------------------------------------------
# _make_mcp_tool - Create single async MCP tool wrapper
# ------------------------------------------------------------------------------
# Creates an async function that:
#   1. Receives kwargs from MCP client
#   2. Calls call_main_thread() to dispatch to Qt main thread
#   3. Returns result back to MCP client
#
# The wrapper copies the original function's signature so MCP can
# generate the correct JSON schema for tool parameters.
# ------------------------------------------------------------------------------
def _make_mcp_tool(
    mcp: Any,
    call_main_thread: Callable[..., Any],
    name: str,
    meta: dict[str, Any],
) -> None:
    original = meta["original"]
    sig = inspect.signature(original)
    tool_name = name  # Capture in closure for async wrapper

    async def wrapper(**kwargs: Any) -> Any:
        return await call_main_thread(tool_name, kwargs)

    # Copy metadata for MCP introspection
    wrapper.__name__ = name
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = getattr(original, "__annotations__", {}).copy()

    # Register with FastMCP
    mcp.tool(description=meta["description"])(wrapper)
