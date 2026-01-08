# @Tool() Decorator Pattern - Final Design

## Goal

Reduce ~190 lines per tool to ~15 lines while maintaining rich error handling.

## Before vs After

**Before (find_notes_tool.py - 192 lines):**
```python
from typing import Any, Callable, Coroutine
from ....handler_registry import register_handler

def _find_notes_handler(query: str) -> dict[str, Any]:
    """... 40 lines of docstring ..."""
    from aqt import mw
    if mw.col is None:
        raise RuntimeError("Collection not loaded")
    try:
        note_ids = list(mw.col.find_notes(query))
    except Exception as e:
        raise Exception(f"Search query failed: {str(e)}") from e
    return {"noteIds": note_ids, "count": len(note_ids), "query": query}

register_handler("find_notes", _find_notes_handler)

def register_find_notes_tool(mcp, call_main_thread):
    @mcp.tool(description="...")
    async def findNotes(query: str) -> dict[str, Any]:
        """... 30 lines of docstring ..."""
        try:
            result = await call_main_thread("find_notes", {"query": query})
            # ... 50 lines of response formatting ...
        except Exception as e:
            # ... 20 lines of error handling ...
```

**After (find_notes_tool.py - 17 lines):**
```python
from typing import Any
from ..tool_decorator import Tool, ToolError, col


@Tool(
    "findNotes",
    "Search for notes using Anki query syntax. Returns an array of note IDs. "
    'Examples: "deck:Spanish", "tag:verb", "is:due", "added:1"'
)
def find_notes(query: str) -> dict[str, Any]:
    note_ids = list(col().find_notes(query))

    if not note_ids:
        raise ToolError(
            "No notes found",
            hint="Try a broader query or check deck/tag names"
        )

    return {
        "noteIds": note_ids,
        "count": len(note_ids),
        "query": query
    }
```

## Architecture

### Stacked Wrappers

Each wrapper handles ONE concern. They stack in order:

```
Original function
       ↓
  _auto_response    →  Wraps return value in {"success": True, ...}
       ↓
  _write_lock       →  (if write=True) Anki write lock
       ↓
  _require_col      →  (if require_col=True) Check collection loaded
       ↓
  _error_handler    →  Catch exceptions → {"success": False, "error": ...}
       ↓
Final wrapped function (registered as handler)
```

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  find_notes_tool.py                                         │
│                                                             │
│  @Tool("findNotes", "Search notes...")                      │
│  def find_notes(query: str):                                │
│      return {"noteIds": [...]}                              │
│                                                             │
└───────────────────────┬─────────────────────────────────────┘
                        │ at import time
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Tool class _register()                                     │
│                                                             │
│  1. Wrap with _auto_response                                │
│  2. Wrap with _write_lock (if write=True)                   │
│  3. Wrap with _require_col (if require_col=True)            │
│  4. Wrap with _error_handler                                │
│  5. register_handler(name, wrapped)                         │
│  6. Store in _registry for MCP registration                 │
│                                                             │
└───────────────────────┬─────────────────────────────────────┘
                        │ at server start
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  register_tools(mcp, call_main_thread)                      │
│                                                             │
│  For each tool in _registry:                                │
│    Create async wrapper that calls call_main_thread()       │
│    Copy signature/annotations from original                 │
│    Register with mcp.tool()                                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Implementation

### tool_decorator.py

```python
from typing import Any, Callable
from functools import wraps
import inspect
import logging

from .handler_registry import register_handler

logger = logging.getLogger(__name__)

_registry: dict[str, dict] = {}


class ToolError(Exception):
    """Raise to return structured error with hint."""
    def __init__(self, message: str, hint: str = None, **data):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.data = data


class Tool:
    """Tool decorator.

    Usage:
        @Tool("tool-name", "Description for AI")
        def my_tool(arg: str) -> dict:
            return {"result": arg}
    """

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable = None,
        *,
        write: bool = False,
        require_col: bool = True,
    ):
        self.name = name
        self.description = description
        self.write = write
        self.require_col = require_col

        if handler is not None:
            self._register(handler)

    def __call__(self, func: Callable) -> Callable:
        self._register(func)
        return func

    def _register(self, func: Callable) -> None:
        wrapped = func
        wrapped = _auto_response(wrapped)

        if self.write:
            wrapped = _write_lock(wrapped)

        if self.require_col:
            wrapped = _require_col(wrapped)

        wrapped = _error_handler(wrapped)

        # Preserve metadata for MCP
        wrapped.__signature__ = inspect.signature(func)
        wrapped.__annotations__ = getattr(func, '__annotations__', {})

        register_handler(self.name, wrapped)
        _registry[self.name] = {
            "name": self.name,
            "description": self.description,
            "handler": wrapped,
            "original": func,
        }


def _require_col(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        from aqt import mw
        if mw is None or mw.col is None:
            raise ToolError("Collection not loaded", hint="Open a profile in Anki first")
        return func(*args, **kwargs)
    return wrapper


def _write_lock(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        from aqt import mw
        try:
            mw.requireReset()
            return func(*args, **kwargs)
        finally:
            if mw is not None and mw.col is not None:
                mw.maybeReset()
    return wrapper


def _error_handler(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ToolError as e:
            result = {"success": False, "error": e.message, **e.data}
            if e.hint:
                result["hint"] = e.hint
            return result
        except Exception as e:
            logger.exception("Tool error: %s", e)
            return {"success": False, "error": str(e)}
    return wrapper


def _auto_response(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)

        # Already formatted
        if isinstance(result, dict) and "success" in result:
            return result

        # None → success
        if result is None:
            return {"success": True}

        # List/tuple → wrap in result
        if isinstance(result, (list, tuple)):
            return {"success": True, "result": list(result)}

        # Dict → merge with success
        if isinstance(result, dict):
            return {"success": True, **result}

        # Anything else → wrap
        return {"success": True, "result": result}

    return wrapper


def register_tools(mcp, call_main_thread: Callable) -> None:
    """Register all @Tool decorated tools with MCP server."""
    for name, meta in _registry.items():
        _make_mcp_tool(mcp, call_main_thread, name, meta)


def _make_mcp_tool(mcp, call_main_thread, name: str, meta: dict) -> None:
    original = meta["original"]
    sig = inspect.signature(original)

    async def wrapper(**kwargs):
        return await call_main_thread(name, kwargs)

    wrapper.__name__ = name
    wrapper.__signature__ = sig
    wrapper.__annotations__ = getattr(original, '__annotations__', {}).copy()

    mcp.tool(description=meta["description"])(wrapper)


def col():
    """Get Anki collection."""
    from aqt import mw
    return mw.col
```

### Updated primitives/tools.py

```python
from ..tool_decorator import register_tools

# Import all tool modules - @Tool decorator registers them at import time
from .essential.tools import (
    sync_tool,
    create_deck_tool,
    find_notes_tool,
    notes_info_tool,
    list_decks_tool,
    add_note_tool,
    # ... etc
)

from .gui.tools import (
    gui_browse_tool,
    gui_add_cards_tool,
    # ... etc
)


def register_all_tools(mcp, call_main_thread) -> None:
    """Register all tools from @Tool registry."""
    register_tools(mcp, call_main_thread)
```

## Error Handling

### Three ways to handle errors:

**1. Let it raise (simplest):**
```python
@Tool("get-deck", "Get deck by name")
def get_deck(name: str):
    deck = col().decks.by_name(name)  # Raises if not found
    return {"id": deck["id"], "name": deck["name"]}
```
Wrapper catches exception → `{"success": False, "error": "..."}`

**2. ToolError with hint:**
```python
@Tool("get-deck", "Get deck by name")
def get_deck(name: str):
    deck = col().decks.by_name(name)
    if not deck:
        raise ToolError(
            f"Deck not found: {name}",
            hint="Use list-decks to see available decks"
        )
    return {"id": deck["id"], "name": deck["name"]}
```
→ `{"success": False, "error": "Deck not found: X", "hint": "Use list-decks..."}`

**3. ToolError with extra data:**
```python
@Tool("get-deck", "Get deck by name")
def get_deck(name: str):
    deck = col().decks.by_name(name)
    if not deck:
        all_decks = [d["name"] for d in col().decks.all()]
        raise ToolError(
            f"Deck not found: {name}",
            hint="Check spelling",
            available_decks=all_decks[:5]
        )
    return {"id": deck["id"], "name": deck["name"]}
```
→ `{"success": False, "error": "...", "hint": "...", "available_decks": [...]}`

## Response Formatting

The `_auto_response` wrapper handles all cases:

| Return value | Becomes |
|--------------|---------|
| `None` | `{"success": True}` |
| `{"foo": "bar"}` | `{"success": True, "foo": "bar"}` |
| `[1, 2, 3]` | `{"success": True, "result": [1, 2, 3]}` |
| `"hello"` | `{"success": True, "result": "hello"}` |
| `{"success": False, ...}` | unchanged (pass-through) |

## Migration Plan

### Phase 1: Infrastructure
1. Create `tool_decorator.py` with T class and wrappers
2. Update `handler_registry.py` to work with both old and new patterns
3. Test with one tool

### Phase 2: Migrate All Tools
1. Convert all 27 tools to @T pattern
2. Remove old registration code from `primitives/tools.py`
3. Remove individual `register_xxx_tool` functions

### Phase 3: Cleanup
1. Remove unused imports
2. Update CLAUDE.md with new pattern
3. Test all tools end-to-end

## File Changes

| Action | File |
|--------|------|
| CREATE | `anki_mcp_server/tool_decorator.py` |
| MODIFY | `anki_mcp_server/handler_registry.py` |
| MODIFY | `anki_mcp_server/primitives/tools.py` |
| REWRITE | All 27 tool files (~190 lines → ~15 lines each) |

## Checklist

- [x] Stacked wrappers design
- [x] Tool class with decorator mode
- [x] ToolError with hint support
- [x] _auto_response wrapper
- [x] _require_col wrapper
- [x] _write_lock wrapper
- [x] _error_handler wrapper
- [x] MCP registration from registry
- [x] Signature/annotation preservation
- [x] Migration plan
- [x] Error handling patterns documented
