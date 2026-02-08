# Python version check - must be first, before any vendor imports that use 3.10+ syntax
import sys

if sys.version_info < (3, 10):
    from aqt.addons import AbortAddonImport
    from aqt.utils import showWarning

    showWarning(
        f"<b>AnkiMCP Server</b> requires <b>Anki 25.07 or later</b>.<br><br>"
        f"Your Anki is running Python {sys.version_info.major}.{sys.version_info.minor}, "
        f"but the MCP protocol SDK requires Python 3.10+.<br><br>"
        f"Please upgrade Anki to continue using this addon.<br>"
        f'<a href="https://github.com/ankimcp/anki-mcp-server-addon/issues/8">More info</a>',
        title="AnkiMCP Server",
    )
    raise AbortAddonImport()

from pathlib import Path

__version__ = "0.4.0"

# Packages we vendor that might conflict with other addons
_VENDOR_PACKAGES = ['mcp', 'pydantic', 'pydantic_core', 'starlette', 'uvicorn', 'anyio', 'httpx', 'websockets']


def _check_vendor_conflicts() -> list[str]:
    """Check if any vendored packages are already loaded (potential conflicts)."""
    conflicts = []
    for pkg in _VENDOR_PACKAGES:
        if pkg in sys.modules:
            conflicts.append(pkg)
    return conflicts


def _setup_vendor_path() -> None:
    """Add shared vendor directory to sys.path."""
    vendor_dir = Path(__file__).parent / "vendor"

    if not vendor_dir.exists():
        print("AnkiMCP Server Warning: vendor directory not found. Dependencies may be missing.")
        return

    # Check for conflicts before adding to path
    conflicts = _check_vendor_conflicts()
    if conflicts:
        print(
            f"AnkiMCP Server Warning: Packages {conflicts} already loaded by another addon. "
            "This may cause compatibility issues. If you experience problems, "
            "try disabling other addons that might use these packages."
        )

    shared_vendor = vendor_dir / "shared"

    # Add shared vendor to path (pure Python packages)
    if shared_vendor.exists():
        sys.path.insert(0, str(shared_vendor))
        print("AnkiMCP Server: Loaded shared vendor packages")
    else:
        print("AnkiMCP Server Warning: shared vendor directory not found")


# Setup shared vendor path first
_setup_vendor_path()

# Now lazy-load pydantic_core binary before any imports that use pydantic
from .dependency_loader import ensure_pydantic_core

if not ensure_pydantic_core():
    print("AnkiMCP Server Error: Failed to load pydantic_core. Addon will not function.")
    # Don't load the rest of the addon
    raise ImportError("AnkiMCP Server: pydantic_core not available")

"""
AnkiMCP Server - Model Context Protocol server addon for Anki.

This addon exposes Anki's collection to AI assistants via MCP.
"""

from typing import Optional

from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import showInfo

from .config import Config, ConfigManager
from .connection_manager import ConnectionManager

# Global instances
_config_manager: Optional[ConfigManager] = None
_connection_manager: Optional[ConnectionManager] = None


def _on_profile_opened() -> None:
    """Called when Anki profile is loaded - initialize and optionally connect."""
    global _config_manager, _connection_manager

    # Get addon package name for config manager
    # In __init__.py, __name__ is the package name directly (e.g., "ankimcp")
    addon_package = __name__

    _config_manager = ConfigManager(addon_package)
    config = _config_manager.load()

    _connection_manager = ConnectionManager(config)

    # Auto-connect if enabled
    if config.auto_connect_on_startup:
        valid, error = config.is_valid_for_mode()
        if valid:
            _connection_manager.start()
            print(f"AnkiMCP Server: Started in {config.mode} mode")
        else:
            # Log warning but don't crash
            print(f"AnkiMCP Server: Auto-connect skipped - {error}")


def _on_profile_will_close() -> None:
    """Called when profile is closing - stop connection and cleanup."""
    global _connection_manager, _config_manager
    if _connection_manager:
        _connection_manager.stop()
        _connection_manager = None
    _config_manager = None
    print("AnkiMCP Server: Stopped connection")


def _on_app_shutdown() -> None:
    """Called when Anki is shutting down - final cleanup."""
    global _connection_manager, _config_manager
    if _connection_manager:
        _connection_manager.stop()
        _connection_manager = None
    _config_manager = None


def _setup_menu() -> None:
    """Add AnkiMCP Server to Tools menu."""
    action = QAction("AnkiMCP Server Settings...", mw)
    action.triggered.connect(_show_settings)
    mw.form.menuTools.addAction(action)


def _show_settings() -> None:
    """Show settings dialog."""
    # TODO: Implement full settings dialog in ui/config_dialog.py
    # For now, just show current status
    if _connection_manager is None or _config_manager is None:
        showInfo("AnkiMCP Server: Not initialized. Please load a profile first.")
        return

    status = (
        "connected" if _connection_manager.is_running else "disconnected"
    )
    config = _config_manager.load()

    info_parts = [
        f"Status: {status}",
        f"Server: http://{config.http_host}:{config.http_port}/",
        f"Auto-connect: {config.auto_connect_on_startup}",
        "",
        "Website: https://ankimcp.ai",
        "Created by Anatoly Tarnavsky",
    ]

    showInfo(f"AnkiMCP Server v{__version__}\n\n" + "\n".join(info_parts))


# Register lifecycle hooks
gui_hooks.profile_did_open.append(_on_profile_opened)
gui_hooks.profile_will_close.append(_on_profile_will_close)

# App shutdown hook - ensures cleanup even if profile close doesn't fire
# (e.g., if user force quits or Anki crashes)
try:
    # This hook was added in Anki 2.1.50
    gui_hooks.app_will_close.append(_on_app_shutdown)
except AttributeError:
    # Fallback for older Anki versions - use profile close as best effort
    print(
        "AnkiMCP Server: Warning - app_will_close hook not available, using profile_will_close"
    )

# Setup menu when main window is ready
gui_hooks.main_window_did_init.append(_setup_menu)
