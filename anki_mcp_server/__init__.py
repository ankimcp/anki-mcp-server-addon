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

__version__ = "0.12.0"

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
from aqt.qt import (
    QAction,
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showInfo, showWarning

from .config import Config, ConfigManager
from .connection_manager import ConnectionManager
from .tool_decorator import validate_disabled_tools
from .tunnel.ui.settings_section import TunnelSettingsSection

# Global instances
_config_manager: Optional[ConfigManager] = None
_connection_manager: Optional[ConnectionManager] = None


def _show_startup_warnings(warnings: list[str]) -> None:
    """Show accumulated startup warnings to the user via Anki dialog.

    Takes a generic list of warning strings and displays them in a single
    dialog. The function is intentionally decoupled from any specific
    warning source -- callers just append strings to the list.

    Args:
        warnings: List of human-readable warning messages. If empty,
            no dialog is shown.
    """
    if not warnings:
        return
    header = "<b>AnkiMCP Server detected configuration issues:</b><br>"
    from html import escape
    body = "<br>".join(f"&bull; {escape(w)}" for w in warnings)
    showWarning(f"{header}<br>{body}", title="AnkiMCP Server")


def _on_profile_opened() -> None:
    """Called when Anki profile is loaded - initialize and optionally connect."""
    global _config_manager, _connection_manager

    # Get addon package name for config manager
    # In __init__.py, __name__ is the package name directly (e.g., "ankimcp")
    addon_package = __name__

    _config_manager = ConfigManager(addon_package)
    config = _config_manager.load()

    # Validate config and collect warnings for the user
    warnings: list[str] = []
    warnings.extend(validate_disabled_tools(config.disabled_tools))
    _show_startup_warnings(warnings)

    _connection_manager = ConnectionManager(config)

    # Always start the background thread (needed for both HTTP and tunnel)
    valid, error = config.is_valid()
    if valid:
        _connection_manager.start()
        print("AnkiMCP Server: Started")
    else:
        print(f"AnkiMCP Server: Start skipped - {error}")


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
    """Show settings dialog with HTTP server info, tunnel controls, and footer."""
    if _connection_manager is None or _config_manager is None:
        showInfo("AnkiMCP Server: Not initialized. Please load a profile first.")
        return

    config = _config_manager.load()

    # Create dialog
    dialog = QDialog(mw)
    dialog.setWindowTitle(f"AnkiMCP Server v{__version__}")
    dialog.setMinimumWidth(450)

    layout = QVBoxLayout()

    # -- HTTP Server section --
    http_checkbox = QCheckBox("Enable HTTP Server")
    http_checkbox.setChecked(config.http_enabled)

    def _on_http_toggled(checked: bool) -> None:
        config.http_enabled = checked
        _config_manager.save(config)
        http_status_label.setVisible(checked)
        http_url_widget.setVisible(checked)
        restart_label.setVisible(True)

    http_checkbox.toggled.connect(_on_http_toggled)
    layout.addWidget(http_checkbox)

    # Status + URL (visible only when enabled)
    http_status = "connected" if _connection_manager.http_running else "disconnected"
    if config.http_path:
        server_url = f"http://{config.http_host}:{config.http_port}/{config.http_path.strip('/')}/"
    else:
        server_url = f"http://{config.http_host}:{config.http_port}/"

    http_status_label = QLabel(f"<b>Status:</b> {http_status}")
    http_status_label.setVisible(config.http_enabled)
    layout.addWidget(http_status_label)

    # URL row as a widget so we can show/hide it
    http_url_widget = QWidget()
    url_inner = QVBoxLayout()
    url_inner.setContentsMargins(0, 0, 0, 0)
    url_inner.addWidget(QLabel("<b>Server URL:</b>"))
    url_row = QHBoxLayout()
    url_field = QLineEdit(server_url)
    url_field.setReadOnly(True)
    url_row.addWidget(url_field)
    copy_button = QPushButton("Copy URL")
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(server_url))
    url_row.addWidget(copy_button)
    url_inner.addLayout(url_row)
    http_url_widget.setLayout(url_inner)
    http_url_widget.setVisible(config.http_enabled)
    layout.addWidget(http_url_widget)

    # Restart notice (hidden until toggled)
    restart_label = QLabel("<i>Restart Anki for this change to take effect.</i>")
    restart_label.setVisible(False)
    layout.addWidget(restart_label)

    layout.addSpacing(10)

    # -- Separator --
    sep1 = QFrame(frameShape=QFrame.Shape.HLine)
    sep1.setFrameShadow(QFrame.Shadow.Sunken)
    layout.addWidget(sep1)
    layout.addSpacing(6)

    # -- Tunnel section --
    tunnel_section = TunnelSettingsSection(_connection_manager, config, parent=dialog)
    layout.addWidget(tunnel_section)
    layout.addSpacing(6)

    # -- Separator --
    sep2 = QFrame(frameShape=QFrame.Shape.HLine)
    sep2.setFrameShadow(QFrame.Shadow.Sunken)
    layout.addWidget(sep2)
    layout.addSpacing(6)

    # -- Footer --
    website_label = QLabel("<b>Website:</b> <a href='https://ankimcp.ai'>https://ankimcp.ai</a>")
    website_label.setOpenExternalLinks(True)
    layout.addWidget(website_label)
    repo_label = QLabel("<b>GitHub:</b> <a href='https://github.com/ankimcp/anki-mcp-server-addon'>https://github.com/ankimcp/anki-mcp-server-addon</a>")
    repo_label.setOpenExternalLinks(True)
    layout.addWidget(repo_label)
    author_label = QLabel("<b>Created by</b> <a href='https://anatoly.dev'>Anatoly Tarnavsky</a>")
    author_label.setOpenExternalLinks(True)
    layout.addWidget(author_label)
    layout.addSpacing(10)

    # -- Close button --
    close_button = QPushButton("Close")
    close_button.clicked.connect(dialog.accept)
    layout.addWidget(close_button)

    dialog.setLayout(layout)
    dialog.exec()


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
