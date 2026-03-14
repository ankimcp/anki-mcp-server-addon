from typing import Annotated, Any, Callable, Optional, Union, get_args, get_origin
from functools import wraps
import inspect
import logging

from pydantic import Field

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
# Tool filtering helpers
# ------------------------------------------------------------------------------
# These functions support the disabled_tools config feature, which allows
# users to hide entire tools or specific actions from multi-action tools
# to reduce token consumption for AI clients.
# ------------------------------------------------------------------------------


def _parse_disabled(
    disabled_list: list[str],
) -> tuple[set[str], dict[str, set[str]]]:
    """Parse disabled_tools config into whole-tool and per-action sets.

    Entries without ':' disable the entire tool. Entries with ':' disable
    a specific action within a multi-action tool.

    Args:
        disabled_list: Config entries like ["sync", "card_management:bury"]

    Returns:
        Tuple of (whole_tool_names, {tool_name: {action_names}})
    """
    whole_tools: set[str] = set()
    action_map: dict[str, set[str]] = {}
    for entry in disabled_list:
        if ":" in entry:
            tool, action = entry.split(":", 1)
            action_map.setdefault(tool, set()).add(action)
        else:
            whole_tools.add(entry)
    return whole_tools, action_map


def _get_action_literal(model_cls: type) -> str | None:
    """Extract the Literal value from a Params model's ``action`` field.

    Args:
        model_cls: A Pydantic BaseModel subclass with a ``Literal["..."]`` action field.

    Returns:
        The literal string value, or None if not found.
    """
    action_field = model_cls.model_fields.get("action")
    if action_field and hasattr(action_field.annotation, "__args__"):
        return action_field.annotation.__args__[0]
    return None


def _filter_union_type(
    original_annotation: Any,
    disabled_actions: set[str],
) -> tuple[Any | None, list[type]]:
    """Rebuild Annotated[Union[...], Field(discriminator=...)] excluding disabled actions.

    Args:
        original_annotation: The Annotated[Union[Model1, Model2, ...], Field(...)] type.
        disabled_actions: Set of action literal values to remove.

    Returns:
        Tuple of (new_annotation_or_None, list_of_enabled_models).
        Returns (None, []) when all actions are disabled.
    """
    args = get_args(original_annotation)  # (Union[A, B, C], FieldInfo(...))
    union_type = args[0]
    union_members = get_args(union_type)

    enabled = [
        m for m in union_members
        if _get_action_literal(m) not in disabled_actions
    ]

    if not enabled:
        return None, []

    if len(enabled) == len(union_members):
        # Nothing was filtered, return original unchanged
        return original_annotation, list(enabled)

    if len(enabled) == 1:
        # Single member: Union[tuple([X])] collapses to X in Python's type
        # system, so Annotated[X, Field(discriminator="action")] would apply a
        # discriminator to a non-union type and break Pydantic schema generation.
        return Annotated[enabled[0], Field()], enabled

    new_union = Union[tuple(enabled)]
    return Annotated[new_union, Field(discriminator="action")], enabled


def _build_dynamic_description(
    base_description: str,
    enabled_models: list[type],
) -> str:
    """Build tool description from a base header and enabled action models.

    Each model is expected to have a ``_tool_description: ClassVar[str]``
    attribute containing the action's description line.

    Args:
        base_description: Short tool-level header (e.g., "Manage card organization").
        enabled_models: List of Pydantic model classes that are still enabled.

    Returns:
        Assembled description string with action count and bullet list.
    """
    action_lines = []
    for model in enabled_models:
        desc = getattr(model, "_tool_description", None)
        if not desc:
            raise ValueError(
                f"Params model '{model.__name__}' is missing _tool_description ClassVar. "
                f"Add: _tool_description: ClassVar[str] = \"action_name: Description.\""
            )
        action_lines.append(f"    - {desc}")

    count = len(action_lines)
    header = f"{base_description} with {count} action{'s' if count != 1 else ''}:"
    return header + "\n\n" + "\n\n".join(action_lines)


def _is_annotated_union(annotation: Any) -> bool:
    """Check if an annotation is Annotated[Union[...], Field(discriminator=...)].

    Used to detect multi-action tool parameters that support per-action filtering.
    """
    if get_origin(annotation) is not Annotated:
        return False
    args = get_args(annotation)
    if not args:
        return False
    inner = args[0]
    return get_origin(inner) is Union


def _get_base_description(original: Any) -> str | None:
    """Try to find _BASE_DESCRIPTION in the module where the tool function was defined."""
    module = inspect.getmodule(original)
    if module is not None:
        return getattr(module, "_BASE_DESCRIPTION", None)
    return None


def _validate_disabled_entries(
    disabled_whole: set[str],
    disabled_actions: dict[str, set[str]],
) -> list[str]:
    """Validate disabled tool/action names against the registry.

    Returns list of warning messages for entries that don't match any
    registered tool or action. Pure logic -- no side effects.
    """
    warnings: list[str] = []
    registered = set(_registry.keys())

    for name in sorted(disabled_whole):
        if name not in registered:
            warnings.append(
                f"disabled_tools: '{name}' does not match any registered tool (typo?)"
            )

    for tool_name in sorted(disabled_actions):
        actions = disabled_actions[tool_name]
        if tool_name not in registered:
            warnings.append(
                f"disabled_tools: '{tool_name}' (from action filter) "
                f"does not match any registered tool (typo?)"
            )
            continue
        # Check if the tool actually has a union-type param (multi-action)
        meta = _registry[tool_name]
        original = meta["original"]
        annotations = getattr(original, "__annotations__", {})
        # Find the union param annotation (usually the first one named 'params')
        union_ann = None
        for ann in annotations.values():
            if _is_annotated_union(ann):
                union_ann = ann
                break
        if union_ann is None:
            for action in sorted(actions):
                warnings.append(
                    f"disabled_tools: '{tool_name}:{action}' "
                    f"-- tool '{tool_name}' is not a multi-action tool"
                )
            continue
        # Check action names against actual union members
        args = get_args(union_ann)
        union_members = get_args(args[0])
        known_actions = {
            _get_action_literal(m) for m in union_members
        }
        for action in sorted(actions):
            if action not in known_actions:
                available = ", ".join(sorted(a for a in known_actions if a))
                warnings.append(
                    f"disabled_tools: '{tool_name}:{action}' "
                    f"-- action '{action}' not found in tool '{tool_name}' "
                    f"(available: {available})"
                )

    return warnings


def _warn_unknown_disabled(
    disabled_whole: set[str],
    disabled_actions: dict[str, set[str]],
) -> None:
    """Log warnings for disabled tool/action names that don't match any registered tool."""
    for msg in _validate_disabled_entries(disabled_whole, disabled_actions):
        print(f"AnkiMCP: {msg}")


def validate_disabled_tools(disabled_list: list[str]) -> list[str]:
    """Validate disabled_tools config entries against registered tools.

    Parses the raw config list and checks each entry against the tool
    registry. Returns warning messages for unrecognized entries.

    This is meant to be called from the main thread at startup, before
    the MCP server starts, so users get immediate feedback about typos.

    Args:
        disabled_list: Raw ``disabled_tools`` config entries
            (e.g., ``["sync", "card_management:bury"]``).

    Returns:
        List of warning messages for unrecognized entries.
        Empty list if everything is valid.
    """
    if not disabled_list:
        return []
    disabled_whole, disabled_actions = _parse_disabled(disabled_list)
    return _validate_disabled_entries(disabled_whole, disabled_actions)


# ------------------------------------------------------------------------------
# register_tools - Create MCP tools from registry
# ------------------------------------------------------------------------------
# Called once at server startup. Iterates through all registered tools
# and creates async MCP wrappers that bridge to main thread via queue.
# Applies disabled_tools filtering to skip whole tools or remove actions.
# ------------------------------------------------------------------------------
def register_tools(
    mcp: Any,
    call_main_thread: Callable[..., Any],
    disabled_tools: list[str] | None = None,
) -> None:
    disabled_whole, disabled_actions = _parse_disabled(disabled_tools or [])

    for name, meta in _registry.items():
        if name in disabled_whole:
            logger.info("Tool disabled by config: %s", name)
            continue
        tool_disabled_actions = disabled_actions.get(name, set())
        _make_mcp_tool(mcp, call_main_thread, name, meta, tool_disabled_actions)

    # Warn about typos / unknown names after registration
    if disabled_whole or disabled_actions:
        _warn_unknown_disabled(disabled_whole, disabled_actions)


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
#
# For multi-action tools with disabled actions, rebuilds the Pydantic
# discriminated union and description to exclude filtered actions.
# ------------------------------------------------------------------------------
def _make_mcp_tool(
    mcp: Any,
    call_main_thread: Callable[..., Any],
    name: str,
    meta: dict[str, Any],
    disabled_actions: set[str] | None = None,
) -> None:
    original = meta["original"]
    sig = inspect.signature(original)
    description = meta["description"]
    annotations = getattr(original, "__annotations__", {}).copy()
    tool_name = name  # Capture in closure for async wrapper

    # Detect multi-action tools (union param) and optionally filter actions
    for param_name, ann in annotations.items():
        if _is_annotated_union(ann):
            # Apply per-action filtering if any actions are disabled
            if disabled_actions:
                filtered_ann, enabled_models = _filter_union_type(ann, disabled_actions)

                if filtered_ann is None:
                    # All actions disabled -- skip entire tool
                    logger.info(
                        "Tool '%s' skipped: all actions disabled by config", name
                    )
                    return

                # Rebuild annotation and signature
                annotations[param_name] = filtered_ann

                # Build parameter list with updated annotation
                params = []
                for p in sig.parameters.values():
                    if p.name == param_name:
                        params.append(p.replace(annotation=filtered_ann))
                    else:
                        params.append(p)
                sig = sig.replace(parameters=params)

                disabled_names = ", ".join(sorted(disabled_actions))
                logger.info(
                    "Tool '%s': disabled actions [%s], %d action(s) remaining",
                    name, disabled_names, len(enabled_models),
                )
            else:
                # No filtering -- all union members are enabled
                union_args = get_args(ann)
                enabled_models = list(get_args(union_args[0]))

            # Always rebuild description from _BASE_DESCRIPTION + enabled models
            base_desc = _get_base_description(original)
            if base_desc is None:
                raise ValueError(
                    f"Multi-action tool '{name}' is missing _BASE_DESCRIPTION "
                    f"in its module. Add a module-level _BASE_DESCRIPTION constant."
                )
            description = _build_dynamic_description(
                base_desc, enabled_models,
            )

            break  # Only one union param per tool

    async def wrapper(**kwargs: Any) -> Any:
        return await call_main_thread(tool_name, kwargs)

    # Copy metadata for MCP introspection
    wrapper.__name__ = name
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = annotations

    # Register with FastMCP
    mcp.tool(description=description)(wrapper)
