"""Persistent tunnel status indicator for Anki's top toolbar.

Adds a ``● AnkiMCP`` item to the top toolbar (the
``Decks | Add | Browse | Stats | Sync`` strip) whose colored dot reflects the
current tunnel connection state. The item is always present — even for
HTTP-only users who never connect the tunnel — and clicking it opens the
AnkiMCP Server settings dialog.

State -> color:
    - grey  (#888888): disconnected / never-connected / idle
    - amber (#e0a800): connecting or reconnecting
    - green (#4caf50): connected

Design notes:
    This module mirrors the polling pattern already used by
    ``TunnelSettingsSection`` (a 1-second ``QTimer`` reading state off
    ``ConnectionManager``) rather than introducing a push/observer channel.
    The settings UI does not use ``TunnelLog.entry_added`` for status — that
    signal drives only the log display — so polling *is* the existing
    "channel" for tunnel state, and reusing it keeps the core tunnel modules
    and ``ConnectionManager`` free of any UI dependency.

    Because the ``QTimer`` fires on the Qt main thread, there is no
    cross-thread UI access here: the indicator never touches ``mw.toolbar``
    from the background asyncio thread, so no ``run_on_main`` marshalling is
    needed.

    State derivation matches ``TunnelSettingsSection._refresh_status``:
    ``tunnel_active`` stays True across the transient per-connection
    disconnect during a reconnect (see ``McpServer`` — ``_tunnel_running``
    is only cleared on terminal stop), so the dot stays amber while
    reconnecting and flips to grey only on the terminal stop. No flicker.

    UI module — must never be imported by core tunnel modules
    (``client.py``, ``reconnect.py``, ``protocol.py``, ``auth.py``).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

from aqt import gui_hooks, mw
from aqt.qt import QTimer

if TYPE_CHECKING:
    # Annotation-only import (PEP 563 via ``from __future__ import
    # annotations``) — avoids a runtime dependency on the core layer from a
    # UI module.
    from ...connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Toolbar link identifier — used for the link's HTML id and to scope the
# inline dot span that live updates target.
_LINK_ID = "ankimcp-status"
_DOT_ID = "ankimcp-status-dot"

# Polling cadence in milliseconds. Matches TunnelSettingsSection's 1s refresh.
_POLL_INTERVAL_MS = 1000


class IndicatorState(Enum):
    """Visual state of the toolbar indicator.

    RECONNECTING is intentionally absent — the tunnel's reconnect window is
    represented as CONNECTING (amber), since ``tunnel_active`` stays True
    across reconnect attempts and the user cares only about "working on it"
    vs "live" vs "off".
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


# State -> dot color. Greys/ambers/greens chosen to read on both light and
# dark toolbar themes.
_STATE_COLORS: dict[IndicatorState, str] = {
    IndicatorState.DISCONNECTED: "#888888",
    IndicatorState.CONNECTING: "#e0a800",
    IndicatorState.CONNECTED: "#4caf50",
}


def _derive_state(cm: Optional[ConnectionManager]) -> IndicatorState:
    """Map the connection manager's tunnel status to an indicator state.

    Mirrors ``TunnelSettingsSection._refresh_status``: connected wins, then
    any active (connecting/reconnecting) task is amber, otherwise grey. A
    ``None`` manager (no profile loaded yet) reads as disconnected.

    Args:
        cm: The current connection manager, or None before a profile opens.

    Returns:
        The indicator state to render.
    """
    if cm is None:
        return IndicatorState.DISCONNECTED
    if cm.tunnel_connected:
        return IndicatorState.CONNECTED
    if cm.tunnel_active:
        return IndicatorState.CONNECTING
    return IndicatorState.DISCONNECTED


def _label_html(state: IndicatorState) -> str:
    """Build the toolbar link label HTML for the given state.

    ``Toolbar.create_link`` hardcodes the link's CSS class and exposes no
    custom-class hook, so the dot color is carried as an inline style on a
    span with a stable id. That id is the target for live colour updates.

    The span uses **single** quotes: ``create_link`` inlines this label into
    a double-quoted ``aria-label="{label}"`` attribute *and* the link body.
    Double quotes here would close ``aria-label`` early, corrupting the
    ``<a>`` tag so the click handler never binds and the dot span never
    enters the DOM (killing live updates). Single quotes nest cleanly.

    Args:
        state: The state whose color the dot should show.

    Returns:
        HTML string like ``"<span id='...' style='color:#888888'>●</span> AnkiMCP"``.
    """
    color = _STATE_COLORS[state]
    return f"<span id='{_DOT_ID}' style='color:{color}'>●</span> AnkiMCP"


class _ToolbarIndicator:
    """Owns the toolbar item's lifecycle and live updates.

    A single module-level instance (created by :func:`register`) holds the
    last-rendered state and the injected dependencies. It registers the
    ``top_toolbar_did_init_links`` hook (which redraws the item on every
    toolbar (re)draw) and runs a ``QTimer`` that polls tunnel state and
    nudges the dot colour live without a full toolbar re-render.
    """

    def __init__(
        self,
        state_provider: Callable[[], Optional[ConnectionManager]],
        on_click: Callable[[], None],
    ) -> None:
        """Initialize the indicator.

        Args:
            state_provider: Returns the current connection manager (or None).
                Called lazily on every poll/redraw so it always reflects the
                manager for the active profile, even across profile switches.
            on_click: Click handler — opens the settings dialog. Invoked when
                the user clicks the toolbar item.
        """
        self._state_provider = state_provider
        self._on_click = on_click
        self._state = IndicatorState.DISCONNECTED
        self._timer: Optional[QTimer] = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def install(self) -> None:
        """Register the toolbar hook and start the polling timer.

        Safe to call once at addon startup. The hook renders the item every
        time the toolbar (re)draws; the timer keeps the dot colour current
        while the toolbar stays on screen.
        """
        gui_hooks.top_toolbar_did_init_links.append(self._on_init_links)

        # Parent the timer to mw so Qt keeps it alive (no GC) and tears it
        # down with the main window.
        self._timer = QTimer(mw)
        self._timer.timeout.connect(self._poll)
        self._timer.start(_POLL_INTERVAL_MS)

        # Force an initial toolbar draw. The top toolbar is drawn ONCE at
        # startup (AnkiQt.finish_ui_setup) BEFORE the main_window_did_init hook
        # fires — i.e. before the hook above is appended — and later screen
        # switches use redraw(), which does NOT re-fire
        # top_toolbar_did_init_links. Without this draw the item would never
        # appear until some unrelated full redraw happened to occur.
        if mw.toolbar is not None:
            mw.toolbar.draw()

    # ------------------------------------------------------------------
    # Toolbar hook — fires on every (re)draw
    # ------------------------------------------------------------------

    def _on_init_links(self, links: list[str], toolbar) -> None:
        """Append the indicator link to the toolbar, rendering current state.

        Fires on a full toolbar ``draw()`` (e.g. switching screens, review
        <-> deck browser), which rebuilds the toolbar DOM — so this reads the
        live state and renders the correct dot colour each time the link is
        recreated. Between full draws the ``QTimer`` poll's ``web.eval`` keeps
        the dot colour current; ``Toolbar.redraw()`` does NOT fire this hook,
        so the two mechanisms together cover every case.

        Args:
            links: The mutable list of link HTML strings to append to.
            toolbar: The ``aqt.toolbar.Toolbar`` building the strip.
        """
        self._state = _derive_state(self._state_provider())
        link = toolbar.create_link(
            cmd=_LINK_ID,
            label=_label_html(self._state),
            func=self._on_click,
            tip="AnkiMCP tunnel status — click for settings",
            id=_LINK_ID,
        )
        links.append(link)

    # ------------------------------------------------------------------
    # Live polling — runs on the Qt main thread (QTimer)
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        """Poll tunnel state and live-update the dot colour if it changed.

        Runs on the Qt main thread (QTimer), so it may safely touch the
        toolbar web view. Only re-renders when the state actually changes,
        and only the dot colour via ``web.eval`` (cheap) rather than a full
        ``toolbar.draw()``. If the dot element isn't present yet (toolbar
        mid-redraw / not built), the no-op JS simply finds nothing — the next
        hook render reapplies the correct colour.
        """
        new_state = _derive_state(self._state_provider())
        if new_state == self._state:
            return
        self._state = new_state
        self._apply_color(_STATE_COLORS[new_state])

    def _apply_color(self, color: str) -> None:
        """Set the dot's colour in the live toolbar web view.

        Args:
            color: CSS colour string for the dot.
        """
        if mw is None or mw.toolbar is None:
            return
        web = mw.toolbar.web
        if web is None:
            return
        # Guard in JS: the element may not exist mid-redraw. Mirrors Anki's
        # own sync-spinner update pattern (Toolbar.set_sync_active).
        web.eval(
            f"(function(){{var d=document.getElementById('{_DOT_ID}');"
            f"if(d){{d.style.color='{color}';}}}})();"
        )


# Module-level singleton, created on first register() call.
_indicator: Optional[_ToolbarIndicator] = None


def register(
    state_provider: Callable[[], Optional[ConnectionManager]],
    on_click: Callable[[], None],
) -> None:
    """Install the toolbar indicator once.

    Idempotent: subsequent calls are no-ops, so it's safe to call from a
    lifecycle hook that may fire repeatedly (e.g. ``profile_did_open``).
    No-op when running headless / without a main window.

    Args:
        state_provider: Returns the current connection manager (or None).
            Read lazily on every poll/redraw, so it must capture the live
            reference (e.g. a module global) rather than a snapshot.
        on_click: Click handler that opens the settings dialog.
    """
    global _indicator

    if mw is None:
        # Headless / source mode without a GUI — nothing to attach to.
        return

    if _indicator is not None:
        return

    _indicator = _ToolbarIndicator(state_provider, on_click)
    _indicator.install()
