from typing import Any, Callable, NamedTuple, Optional
from functools import wraps
import inspect
import logging

from mcp.types import ToolAnnotations

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
# Key: tool name, Value: metadata dict (see `_register`)
_registry: dict[str, dict[str, Any]] = {}


# ------------------------------------------------------------------------------
# Tool - Decorator class that registers functions as MCP tools
# ------------------------------------------------------------------------------
# Usage:
#   @Tool("tool_name", "Description for AI", title="Tool Name", write=True)
#   def my_tool(arg: str) -> dict:
#       ...
#
# Parameters:
#   - name: Unique tool identifier exposed to MCP clients
#   - description: Shown to AI to understand when/how to use the tool
#   - title: Required, keyword-only. Human-readable title shown by MCP
#     clients (e.g. "Add Note"). Sent both as the Tool's top-level title and
#     as ToolAnnotations.title. Empty/whitespace is a definition error.
#   - write: If True, marks the tool as mutating the collection. Drives the
#     MCP readOnlyHint (readOnlyHint = not write) and is the destructive
#     precondition below. Also gates whether _write_lock refreshes Anki's UI
#     after the handler runs -- see refresh_ui.
#   - destructive_hint: MCP destructiveHint, only meaningful when write=True.
#     None (default) resolves to True for write tools -- the conservative
#     choice, since destructiveHint=False promises *only additive* updates.
#     Set False only for purely additive tools (e.g. add_note). Passing any
#     value on a write=False tool is a definition error; read-only tools
#     always send destructiveHint=False on the wire.
#   - idempotent_hint: MCP idempotentHint, only meaningful when write=True.
#     None (default) omits it on the wire (MCP then assumes False). Set True
#     only when repeating the same call with the same arguments has no
#     additional effect (e.g. create_deck, delete_notes). Passing any value
#     on a write=False tool is a definition error.
#   - open_world_hint: MCP openWorldHint. Default False (the MCP default of
#     True is wrong for a local-collection server); set True only for tools
#     that reach external entities (AnkiWeb sync, URL fetches).
#   - refresh_ui: Only meaningful when write=True. If True (default), calls
#     mw.reset() after the handler to refresh open deck browser/overview/
#     reviewer screens. Set False for a tool that already refreshes the UI
#     itself (e.g. gui_answer_card, whose reviewer op is its own async
#     CollectionOp) -- an extra mw.reset() there would race it.
#   - require_col: If True (default), checks collection is open before running
#   - destructive: If True, the tool is hidden from MCP clients unless the
#     operator opts in via the enabled_destructive_tools config allow-list.
#     Requires write=True (a destructive tool that doesn't modify the
#     collection is a definition error).
#   - opt_in: If True, the tool is hidden from MCP clients unless the
#     operator opts in via the enabled_opt_in_tools config allow-list. Unlike
#     destructive, this does not require write=True -- it's for tools that
#     are opt-in for reasons other than being dangerous (e.g. new/experimental
#     surface area). Same allow-list syntax as enabled_destructive_tools.
#
# destructive vs destructive_hint: these are separate concepts and must not
# be conflated. destructive=True is an operator-facing visibility gate (hidden
# from tools/list unless opted in via enabled_destructive_tools) reserved for
# a handful of high-risk primitives. destructive_hint is the MCP
# destructiveHint annotation, a much wider client-facing notion covering
# anything that isn't purely additive (most write tools). destructive=True
# with destructive_hint=False is a contradiction and raises ValueError.
#
# What happens at import time:
#   1. Wraps with _write_lock if write=True (Anki UI refresh, unless
#      refresh_ui=False)
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
        title: str,
        write: bool = False,
        refresh_ui: bool = True,
        require_col: bool = True,
        destructive: bool = False,
        opt_in: bool = False,
        destructive_hint: Optional[bool] = None,
        idempotent_hint: Optional[bool] = None,
        open_world_hint: bool = False,
    ):
        if not isinstance(title, str) or not title.strip():
            raise ValueError(
                f"Tool '{name}': title must be a non-empty string "
                f"(shown to MCP clients as the tool's human-readable name)"
            )
        if destructive and not write:
            raise ValueError(
                f"Tool '{name}': destructive=True requires write=True "
                f"(a destructive tool that doesn't modify the collection "
                f"is a definition error)"
            )
        if destructive_hint is not None and not write:
            raise ValueError(
                f"Tool '{name}': destructive_hint requires write=True "
                f"(MCP destructiveHint is only meaningful for tools that "
                f"modify the collection)"
            )
        if idempotent_hint is not None and not write:
            raise ValueError(
                f"Tool '{name}': idempotent_hint requires write=True "
                f"(MCP idempotentHint is only meaningful for tools that "
                f"modify the collection)"
            )
        if destructive and destructive_hint is False:
            raise ValueError(
                f"Tool '{name}': destructive=True contradicts "
                f"destructive_hint=False"
            )
        self.name = name
        self.description = description
        self.title = title
        self.write = write
        self.destructive_hint = (
            (True if destructive_hint is None else destructive_hint)
            if write
            else None
        )
        self.idempotent_hint = idempotent_hint
        self.open_world_hint = open_world_hint
        self.refresh_ui = refresh_ui
        self.require_col = require_col
        self.destructive = destructive
        self.opt_in = opt_in

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
            wrapped = _write_lock(wrapped, refresh_ui=self.refresh_ui)

        if self.require_col:
            wrapped = _require_col(wrapped)  # Check collection is open

        wrapped = _error_handler(wrapped)  # Outermost: catch all exceptions

        # Preserve original signature for MCP schema generation
        wrapped.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        wrapped.__annotations__ = getattr(func, "__annotations__", {})

        # Register for main-thread dispatch (RequestProcessor uses this)
        register_handler(self.name, wrapped)

        # Store for MCP tool creation later
        # "title"/"write"/"destructive_hint"/"idempotent_hint"/"open_world_hint"
        # become the MCP ToolAnnotations; "destructive" gates registration behind
        # enabled_destructive_tools.
        _registry[self.name] = {
            "name": self.name,
            "description": self.description,
            "title": self.title,
            "original": func,
            "write": self.write,
            "destructive": self.destructive,
            "opt_in": self.opt_in,
            "destructive_hint": self.destructive_hint,
            "idempotent_hint": self.idempotent_hint,
            "open_world_hint": self.open_world_hint,
        }


# ------------------------------------------------------------------------------
# _write_lock - Refresh Anki's UI after a write operation
# ------------------------------------------------------------------------------
# Calls mw.reset() after the handler runs -- on success AND on error, since a
# handler that raised may still have written (e.g. change_note_type_tool.py
# mutates then calls _verify(), which re-reads and can raise after the
# mutation already happened) -- so open deck browser/overview/reviewer screens
# pick up the change either way. mw.requireReset()/mw.maybeReset() are NOT
# used here (see below). Only applied when write=True in @Tool decorator.
#
# mw.requireReset()/mw.maybeReset() (aqt/main.py) are obsolete no-ops in
# current Anki: maybeReset() is `pass`, and requireReset() prints a
# deprecation notice plus a full stack trace to stdout before calling
# mw.reset() anyway -- so every write tool was dumping a stack trace per call
# for no behavioral benefit. mw.reset() itself is still the supported
# non-CollectionOp way to trigger a UI refresh (no warnings, no printing): it
# fires gui_hooks.operation_did_execute with an OpChanges that has every field
# set, which is exactly what the deck browser/overview/reviewer screens check
# to decide whether to rebuild. CollectionOp is not used instead because it is
# async (backend call on a worker thread, completion delivered via a Qt
# signal); our handlers already run synchronously on the main thread via the
# queue bridge, so there is no async boundary for it to wrap.
# ------------------------------------------------------------------------------
def _write_lock(func: Callable[..., Any], refresh_ui: bool = True) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        mw = _get_mw()

        # Guard against edge case where write=True but require_col=False
        if mw is None:
            raise HandlerError("Main window not available", hint="Make sure Anki is fully loaded")

        try:
            result = func(*args, **kwargs)
        finally:
            # A handler that raised may still have written (change_note_type
            # verifies after the backend call), so refresh either way. Guard
            # against the closed-for-full-sync case (mw.col non-None with
            # db=None -- see handler_wrappers.py's _check_col_available), and
            # never let a refresh failure mask the handler's own exception.
            # refresh_ui=False skips this entirely -- for a tool whose write
            # already refreshes the UI itself (see the Tool docstring above).
            if refresh_ui and mw.col is not None and getattr(mw.col, "db", None) is not None:
                try:
                    mw.reset()
                except Exception:
                    logger.exception("mw.reset() after write failed")

        return result

    return wrapper


# ------------------------------------------------------------------------------
# Config aliases for the retired multi-action tools
# ------------------------------------------------------------------------------
# card_management, tag_management, filtered_deck and model_fields were split
# into one tool per action. Existing disabled_tools / enabled_destructive_tools
# / enabled_opt_in_tools entries keep working through this table:
#   - "old_tool:action" behaves exactly like the new tool's name.
#   - "old_tool" in disabled_tools behaves like listing every tool it was split
#     into.
#   - "old_tool" in an allow-list opts in nothing, as before: a whole-tool entry
#     never opted in a multi-action tool's gated actions.
# Each distinct alias gets a one-time deprecation warning naming its
# replacement. TOOL_ALIASES is the single source of truth; the whole-tool
# groups are derived from it. It is also the only meaning a "tool:action"
# entry still has -- any other one matches nothing.
# ------------------------------------------------------------------------------
TOOL_ALIASES: dict[str, str] = {
    "card_management:reposition": "card_reposition",
    "card_management:change_deck": "card_change_deck",
    "card_management:bury": "card_bury",
    "card_management:unbury": "card_unbury",
    "card_management:suspend": "card_suspend",
    "card_management:unsuspend": "card_unsuspend",
    "card_management:set_flag": "card_set_flag",
    "card_management:set_due_date": "card_set_due_date",
    "card_management:forget_cards": "card_forget",
    "tag_management:add_tags": "tag_add",
    "tag_management:remove_tags": "tag_remove",
    "tag_management:replace_tags": "tag_replace",
    "tag_management:get_tags": "tag_list",
    "tag_management:clear_unused_tags": "tag_clear_unused",
    "tag_management:batch_tags": "tag_batch",
    "filtered_deck:create_or_update": "filtered_deck_upsert",
    "filtered_deck:rebuild": "filtered_deck_rebuild",
    "filtered_deck:empty": "filtered_deck_empty",
    "filtered_deck:delete": "filtered_deck_delete",
    "filtered_deck:get_info": "filtered_deck_get_info",
    "model_fields:add": "model_field_add",
    "model_fields:rename": "model_field_rename",
    "model_fields:reposition": "model_field_reposition",
    "model_fields:remove": "model_field_remove",
}

_GROUP_ALIASES: dict[str, tuple[str, ...]] = {
    group: tuple(
        new for old, new in TOOL_ALIASES.items() if old.split(":", 1)[0] == group
    )
    for group in dict.fromkeys(old.split(":", 1)[0] for old in TOOL_ALIASES)
}

# Deprecation warnings already printed in this process (one per distinct alias
# per config key).
_reported_alias_warnings: set[str] = set()


class _AliasResolution(NamedTuple):
    plain: list[str]  # entries that are not aliases, in config order
    replacements: list[str]  # new tool names the alias entries stand for
    deprecations: list[str]  # one warning per distinct alias entry


def _resolve_aliases(
    entries: list[str],
    config_key: str,
    *,
    gate_key: str | None = None,
) -> _AliasResolution:
    """Map retired multi-action tool names in a config list to the new tools.

    No side effects -- printing is left to ``_report_deprecated_aliases``.

    Args:
        entries: Raw config entries.
        config_key: Config field name used as the warning prefix.
        gate_key: None for disabled_tools, where a whole-tool alias stands
            for every tool of its group. For an allow-list, the registry meta
            key of its gate (``"destructive"`` or ``"opt_in"``): a whole-tool
            alias there stands for nothing, and its warning names only the
            group's tools that gate actually hides.
    """
    plain: list[str] = []
    replacements: list[str] = []
    deprecations: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry in TOOL_ALIASES:
            targets: tuple[str, ...] = (TOOL_ALIASES[entry],)
        elif entry in _GROUP_ALIASES:
            targets = _GROUP_ALIASES[entry] if gate_key is None else ()
        else:
            plain.append(entry)
            continue
        if entry in seen:
            continue
        seen.add(entry)
        replacements.extend(t for t in targets if t not in replacements)
        if targets:
            names = ", ".join(f"'{t}'" for t in targets)
            deprecations.append(
                f"{config_key}: '{entry}' is deprecated -- use {names} instead"
            )
        else:
            gated = [
                t for t in _GROUP_ALIASES[entry]
                if t in _registry and _registry[t][gate_key]
            ]
            if gated:
                names = ", ".join(f"'{t}'" for t in gated)
                deprecations.append(
                    f"{config_key}: '{entry}' is deprecated and has no effect "
                    f"(a whole-tool entry never opted in its actions) -- list "
                    f"{names} instead"
                )
            else:
                label = gate_key.replace("_", "-")
                deprecations.append(
                    f"{config_key}: '{entry}' is deprecated and has no effect "
                    f"(none of its tools are {label}) -- remove the entry"
                )
    return _AliasResolution(plain, replacements, deprecations)


def _report_deprecated_aliases(resolution: _AliasResolution) -> None:
    """Print each alias deprecation warning once per process."""
    for msg in resolution.deprecations:
        if msg not in _reported_alias_warnings:
            _reported_alias_warnings.add(msg)
            print(f"AnkiMCP: {msg}")


def _effective_entries(resolution: _AliasResolution) -> list[str]:
    """The tool names a config list stands for: its plain entries plus the
    tools its alias entries resolve to. Alias entries themselves are valid by
    construction, so this is also what gets checked for typos / no-ops.
    """
    return [*resolution.plain, *resolution.replacements]


# ------------------------------------------------------------------------------
# Tool filtering helpers
# ------------------------------------------------------------------------------
# These functions support the disabled_tools config feature, which lets users
# hide whole tools to reduce token consumption for AI clients, and the
# enabled_destructive_tools / enabled_opt_in_tools allow-lists.
# ------------------------------------------------------------------------------


def _validate_tool_entries(
    entries: list[str],
    config_key: str = "disabled_tools",
) -> list[str]:
    """Validate config entries against the registry.

    Returns one warning per distinct entry that matches no registered tool.
    Pure logic -- no side effects. Alias entries must already have been
    resolved (see ``_effective_entries``): a "tool:action" entry that reaches
    this point is not an alias, so it matches nothing.

    Args:
        entries: Tool names to check.
        config_key: Config field name used as the warning prefix
            (e.g., "disabled_tools" or "enabled_destructive_tools").
    """
    warnings: list[str] = []
    for name in sorted(set(entries)):
        if name in _registry:
            continue
        if ":" in name:
            warnings.append(
                f"{config_key}: '{name}' does not match any registered tool "
                f"(typo?) -- 'tool:action' entries are no longer supported; "
                f"every action is its own tool now"
            )
        else:
            warnings.append(
                f"{config_key}: '{name}' does not match any registered tool (typo?)"
            )
    return warnings


def _warn_unknown_disabled(entries: list[str]) -> None:
    """Print warnings for disabled_tools entries that match no registered tool."""
    for msg in _validate_tool_entries(entries):
        print(f"AnkiMCP: {msg}")


def validate_disabled_tools(disabled_list: list[str]) -> list[str]:
    """Validate disabled_tools config entries against registered tools.

    Parses the raw config list and checks each entry against the tool
    registry. Returns warning messages for unrecognized entries.

    Called on the main thread at startup before the MCP server starts; the
    resulting warning dialog is deferred until after start, so it never blocks it.

    Args:
        disabled_list: Raw ``disabled_tools`` config entries
            (e.g., ``["sync", "card_bury"]``).

    Returns:
        List of warning messages for unrecognized entries.
        Empty list if everything is valid.
    """
    if not disabled_list:
        return []
    resolution = _resolve_aliases(disabled_list, "disabled_tools")
    return [
        *resolution.deprecations,
        *_validate_tool_entries(_effective_entries(resolution)),
    ]


def _validate_enabled_gate_tools(
    enabled_list: list[str],
    *,
    config_key: str,
    meta_key: str,
    article_label: str,
) -> list[str]:
    """Shared logic behind validate_enabled_destructive_tools and
    validate_enabled_opt_in_tools -- both allow-lists have the identical
    two-problem-class shape (typo, and no-op opt-in of something not gated).

    Args:
        enabled_list: Raw config entries (e.g. ``["change_note_type"]``).
        config_key: Config field name used as the warning prefix.
        meta_key: Registry meta key for the gate flag
            (``"destructive"`` or ``"opt_in"``).
        article_label: Human-readable adjective for the warning text, WITH its
            article (``"a destructive"`` or ``"an opt-in"``) -- kept separate
            from meta_key so the message reads naturally (and grammatically)
            regardless of the registry key's exact spelling.

    Returns:
        List of warning messages. Empty list if everything is valid.
    """
    if not enabled_list:
        return []
    resolution = _resolve_aliases(enabled_list, config_key, gate_key=meta_key)
    entries = _effective_entries(resolution)

    # Class 1: unknown tool names (typos)
    warnings = [
        *resolution.deprecations,
        *_validate_tool_entries(entries, config_key=config_key),
    ]

    # Class 2: real tools that aren't marked for this gate (no-ops)
    for name in sorted(set(entries)):
        meta = _registry.get(name)
        if meta is not None and not meta[meta_key]:
            warnings.append(
                f"{config_key}: no-op: '{name}' is not {article_label} tool"
            )

    return warnings


def validate_enabled_destructive_tools(enabled_list: list[str]) -> list[str]:
    """Validate enabled_destructive_tools config entries against registered tools.

    Checks each entry for two problem classes:
    1. The entry matches no registered tool (typo).
    2. The entry matches a real tool that is NOT marked destructive --
       the entry is a no-op (nothing to opt in to).

    Called on the main thread at startup before the MCP server starts; the
    resulting warning dialog is deferred until after start, so it never blocks it.

    Args:
        enabled_list: Raw ``enabled_destructive_tools`` config entries
            (e.g., ``["change_note_type", "model_field_remove"]``).

    Returns:
        List of warning messages. Empty list if everything is valid.
    """
    return _validate_enabled_gate_tools(
        enabled_list,
        config_key="enabled_destructive_tools",
        meta_key="destructive",
        article_label="a destructive",
    )


def validate_enabled_opt_in_tools(enabled_list: list[str]) -> list[str]:
    """Validate enabled_opt_in_tools config entries against registered tools.

    Mirrors validate_enabled_destructive_tools -- same two problem classes
    (typo, no-op opt-in of a tool that isn't gated by opt_in=True).

    Args:
        enabled_list: Raw ``enabled_opt_in_tools`` config entries
            (e.g., ``["gui_answer_card"]``).

    Returns:
        List of warning messages. Empty list if everything is valid.
    """
    return _validate_enabled_gate_tools(
        enabled_list,
        config_key="enabled_opt_in_tools",
        meta_key="opt_in",
        article_label="an opt-in",
    )


# ------------------------------------------------------------------------------
# register_tools - Create MCP tools from registry
# ------------------------------------------------------------------------------
# Called once at server startup. Iterates through all registered tools
# and creates async MCP wrappers that bridge to main thread via queue.
# Applies disabled_tools filtering to skip tools. Applies the
# enabled_destructive_tools allow-list: destructive tools are hidden unless
# explicitly opted in (exact match on the tool name). Applies the
# enabled_opt_in_tools allow-list the same way, independently of the
# destructive gate. Alias entries (TOOL_ALIASES) are resolved to the tools
# they stand for first.
# ------------------------------------------------------------------------------
def register_tools(
    mcp: Any,
    call_main_thread: Callable[..., Any],
    disabled_tools: list[str] | None = None,
    enabled_destructive_tools: list[str] | None = None,
    enabled_opt_in_tools: list[str] | None = None,
) -> None:
    disabled_list = disabled_tools or []
    destructive_list = enabled_destructive_tools or []
    opt_in_list = enabled_opt_in_tools or []
    disabled_res = _resolve_aliases(disabled_list, "disabled_tools")
    destructive_res = _resolve_aliases(
        destructive_list, "enabled_destructive_tools", gate_key="destructive"
    )
    opt_in_res = _resolve_aliases(
        opt_in_list, "enabled_opt_in_tools", gate_key="opt_in"
    )
    for resolution in (disabled_res, destructive_res, opt_in_res):
        _report_deprecated_aliases(resolution)

    disabled = set(_effective_entries(disabled_res))
    opted_destructive = set(_effective_entries(destructive_res))
    opted_in = set(_effective_entries(opt_in_res))

    for name, meta in _registry.items():
        if name in disabled:
            logger.info("Tool disabled by config: %s", name)
            continue
        # Destructive gate: hidden unless opted in by the operator
        if meta["destructive"] and name not in opted_destructive:
            logger.info(
                "Tool hidden (destructive, not opted in via "
                "enabled_destructive_tools): %s", name,
            )
            continue
        # Opt-in gate: hidden unless opted in by the operator
        if meta["opt_in"] and name not in opted_in:
            logger.info(
                "Tool hidden (opt-in, not opted in via "
                "enabled_opt_in_tools): %s", name,
            )
            continue
        _make_mcp_tool(mcp, call_main_thread, name, meta)

    # Warn about typos / unknown names after registration
    if disabled:
        _warn_unknown_disabled(_effective_entries(disabled_res))


# ------------------------------------------------------------------------------
# _build_tool_annotations - MCP ToolAnnotations from a _registry entry
# ------------------------------------------------------------------------------
# readOnlyHint is derived from write. destructiveHint is always an explicit
# bool: a read-only tool is non-destructive, so it sends False (directory
# scanners require the field on every tool). idempotentHint is only meaningful
# for write tools, so it is omitted (None) for read-only ones and for write
# tools that don't declare it.
# ------------------------------------------------------------------------------
def _build_tool_annotations(meta: dict[str, Any]) -> ToolAnnotations:
    write = meta["write"]
    return ToolAnnotations(
        title=meta["title"],
        readOnlyHint=not write,
        destructiveHint=meta["destructive_hint"] if write else False,
        idempotentHint=meta["idempotent_hint"] if write else None,
        openWorldHint=meta["open_world_hint"],
    )


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
# Registers with the title at top level (2025-06-18 spec) AND inside
# ToolAnnotations.title (the older location) so either reader sees it.
# ------------------------------------------------------------------------------
def _make_mcp_tool(
    mcp: Any,
    call_main_thread: Callable[..., Any],
    name: str,
    meta: dict[str, Any],
) -> None:
    original = meta["original"]
    tool_name = name  # Capture in closure for async wrapper

    async def wrapper(**kwargs: Any) -> Any:
        return await call_main_thread(tool_name, kwargs)

    # Copy metadata for MCP introspection
    wrapper.__name__ = name
    wrapper.__signature__ = inspect.signature(original)  # type: ignore[attr-defined]
    wrapper.__annotations__ = getattr(original, "__annotations__", {}).copy()

    # Register with FastMCP
    mcp.tool(
        title=meta["title"],
        description=meta["description"],
        annotations=_build_tool_annotations(meta),
    )(wrapper)
