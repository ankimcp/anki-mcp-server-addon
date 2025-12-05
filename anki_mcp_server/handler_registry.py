"""Central registry for tool handlers.

Handlers register themselves at import time. The RequestProcessor
uses this registry to dispatch tool requests to the correct handler.
"""
from typing import Any, Callable

_handlers: dict[str, Callable[..., Any]] = {}


def register_handler(name: str, handler: Callable[..., Any]) -> None:
    """Register a handler function for a tool name."""
    if name in _handlers:
        raise ValueError(f"Handler already registered: {name}")
    _handlers[name] = handler


def get_handler(name: str) -> Callable[..., Any]:
    """Get handler by name. Raises KeyError if not found."""
    if name not in _handlers:
        raise KeyError(f"Unknown handler: {name}")
    return _handlers[name]


def execute(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Execute a tool handler with arguments."""
    handler = get_handler(tool_name)
    return handler(**arguments)
