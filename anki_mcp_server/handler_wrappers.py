# handler_wrappers.py
"""Shared wrappers and helpers for tool and resource handlers.

This module provides common functionality used by both @Tool and @Resource decorators:
- Error handling wrapper (catches exceptions and formats error messages)
- Collection availability check wrapper
- Main window and collection access helpers

Error Handling Strategy:
    Handler functions can raise HandlerError for structured errors with hints,
    or any other exception for unexpected failures. The _error_handler wrapper
    catches HandlerError, formats the message with hints/context, and re-raises
    as a plain Exception. All exceptions are then caught by request_processor
    on the main thread, serialized into a ToolResponse, and re-raised on the
    background thread where FastMCP catches them and sets isError=True.
"""

from typing import Any, Callable, Optional
from functools import wraps
import logging

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# HandlerError - Custom exception for handler failures with structured error info
# ------------------------------------------------------------------------------
# Raise this in tool/resource functions to return a clean error to the AI client.
# - message: What went wrong
# - hint: Actionable suggestion for the AI (optional)
# - **data: Extra context like filename, query, etc. (optional)
#
# Example: raise HandlerError("Deck not found", hint="Check spelling", deck_name="Spansh")
# ------------------------------------------------------------------------------
class HandlerError(Exception):
    """Structured error for tool and resource handlers.

    Raise this exception to signal an error to the AI client with optional
    hints and additional context data. The error message will be formatted
    with hints and context, then re-raised as a plain Exception for
    request_processor to catch and serialize.

    Args:
        message: Description of what went wrong
        hint: Actionable suggestion for the AI (optional)
        **data: Extra context like filename, query, etc. (optional)

    Example:
        raise HandlerError(
            "Deck not found",
            hint="Check spelling or use list_decks to see available decks",
            deck_name="Spansh"
        )
    """
    def __init__(self, message: str, hint: Optional[str] = None, **data: Any):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.data = data


# ------------------------------------------------------------------------------
# _error_handler - Outermost wrapper that catches exceptions
# ------------------------------------------------------------------------------
# Catches HandlerError and formats message with hints/context, then re-raises.
# The exception will be caught by request_processor and serialized to ToolResponse,
# then re-raised on the background thread where FastMCP will catch it and set
# isError=True in the MCP protocol response.
# ------------------------------------------------------------------------------
def _error_handler(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a handler to catch exceptions and format error messages.

    Catches HandlerError and other exceptions, formats them with hints/context,
    and re-raises. The exception will be caught by request_processor and
    serialized to ToolResponse, then re-raised on the background thread where
    FastMCP will catch it and set isError=True.

    Args:
        func: The handler function to wrap

    Returns:
        Wrapped function that formats and re-raises exceptions
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except HandlerError as e:
            # Log for debugging
            logger.warning("Handler error: %s (hint: %s)", e.message, e.hint)
            # Format message with hint and context for AI client
            msg = e.message
            if e.hint:
                msg += f" (hint: {e.hint})"
            if e.data:
                msg += f" (context: {e.data})"
            raise Exception(msg)
        except Exception as e:
            # Log full traceback for debugging
            logger.exception("Unexpected handler error: %s", e)
            raise  # Re-raise as-is, request_processor will catch it

    return wrapper


# ------------------------------------------------------------------------------
# _require_col - Check that Anki collection is open
# ------------------------------------------------------------------------------
# Raises HandlerError if mw (main window) or mw.col (collection) is None.
# Most handlers need the collection - this is enabled by default.
# ------------------------------------------------------------------------------
def _require_col(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a handler to check that Anki collection is available.

    Raises HandlerError if the main window or collection is not available.
    Most tools and resources need the collection, so this is enabled by default.

    Args:
        func: The handler function to wrap

    Returns:
        Wrapped function that checks collection before executing
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        mw = _get_mw()
        if mw is None or mw.col is None:
            raise HandlerError("Collection not available", hint="Open a profile in Anki first")
        return func(*args, **kwargs)

    return wrapper


# ------------------------------------------------------------------------------
# _get_mw - Internal helper to get main window (single import point for aqt)
# ------------------------------------------------------------------------------
def _get_mw() -> Any:
    """Get Anki main window (internal helper).

    Returns:
        Main window instance or None if not available
    """
    from aqt import mw
    return mw


# ------------------------------------------------------------------------------
# get_mw - Helper to get main window with validation
# ------------------------------------------------------------------------------
# Use this in tool functions that need mw but not necessarily col.
# Raises HandlerError if Anki main window not available.
# ------------------------------------------------------------------------------
def get_mw() -> Any:
    """Get Anki main window with validation.

    Use this in handler functions that need the main window but not
    necessarily the collection.

    Returns:
        Main window instance

    Raises:
        HandlerError: If main window is not available
    """
    mw = _get_mw()
    if mw is None:
        raise HandlerError("Main window not available", hint="Make sure Anki is fully loaded")
    return mw


# ------------------------------------------------------------------------------
# get_col - Helper to get collection with validation
# ------------------------------------------------------------------------------
# Use this in tool functions instead of accessing mw.col directly.
# Raises HandlerError with helpful hint if collection not available.
# ------------------------------------------------------------------------------
def get_col() -> Any:
    """Get Anki collection with validation.

    Use this in handler functions instead of accessing mw.col directly.
    Provides a helpful hint if the collection is not available.

    Returns:
        Collection instance

    Raises:
        HandlerError: If collection is not available
    """
    mw = _get_mw()
    if mw is None or mw.col is None:
        raise HandlerError("Collection not available", hint="Open a profile in Anki first")
    return mw.col
