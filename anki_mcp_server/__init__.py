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

__version__ = "0.24.0"

# Packages we vendor directly (we ship our own copy under vendor/shared). This
# is the set used for the system-package FALLBACK check on source/Nix installs:
# if vendor/ is absent, ALL of these must be importable from the system, else we
# abort with an actionable message. Keep this list to genuinely-required deps so
# we don't reject otherwise-fine source installs over an optional transitive.
_VENDOR_PACKAGES = [
    'mcp', 'pydantic', 'pydantic_core', 'starlette', 'uvicorn', 'anyio',
    'httpx', 'websockets', 'packaging',
]

# Broader watch list for CONFLICT detection only (never used for the fallback
# import check). Adds shared transitive libraries that heavyweight add-ons
# (notably AnkiHub) also bundle and that can collide with our vendored copies:
# if another add-on already loaded its own copy before our vendor path is
# prepended, that copy wins for the whole process. We log the provenance (whose
# copy is live) — advisory only, never aborts. ``google.protobuf`` is the
# importable module name for the protobuf dependency.
_CONFLICT_WATCH_PACKAGES = _VENDOR_PACKAGES + [
    'typing_extensions', 'google.protobuf', 'certifi', 'urllib3',
    'charset_normalizer',
]

# Set to True when running from source with system-provided packages (e.g. Nix)
_USING_SYSTEM_PACKAGES = False


def _read_log_to_file_flag() -> bool:
    """Read the ``log_to_file`` config flag using ONLY the standard library.

    Called before any vendored import so file logging can capture failures that
    happen during dependency loading. Must not depend on vendored packages or on
    our own config dataclass (which imports may not be safe yet).

    Tries Anki's addon config first (the supported path); falls back to reading
    the shipped ``config.json`` merged with Anki's ``meta.json`` user overrides
    directly off disk. Any failure yields ``False`` (logging stays off).
    """
    addon_dir = Path(__file__).parent

    # Preferred: Anki's addon manager (already available when add-ons import).
    try:
        from aqt import mw  # stdlib-adjacent; Anki's own module, not vendored

        if mw is not None and getattr(mw, "addonManager", None) is not None:
            raw = mw.addonManager.getConfig(__name__.split(".")[0]) or {}
            return bool(raw.get("log_to_file", False))
    except Exception:
        pass

    # Fallback: read config.json (defaults) + meta.json (user overrides) directly.
    try:
        import json

        merged: dict = {}
        config_path = addon_dir / "config.json"
        if config_path.exists():
            merged.update(json.loads(config_path.read_text(encoding="utf-8")))
        meta_path = addon_dir / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and isinstance(meta.get("config"), dict):
                merged.update(meta["config"])
        return bool(merged.get("log_to_file", False))
    except Exception:
        return False


# Initialize file logging FIRST — before vendor-path setup and dependency
# loading — so any failure in that layer is captured. file_log is stdlib-only.
from .file_log import (
    init_file_logging,
    get_logger,
    log_diagnostics_snapshot,
    _module_provenance,
)

init_file_logging(
    enabled=_read_log_to_file_flag(),
    user_files_dir=Path(__file__).parent / "user_files",
)

# Startup diagnostics snapshot (no-op when logging is disabled). Captured before
# vendored imports so it reflects the pristine module table (what other add-ons
# loaded before us).
log_diagnostics_snapshot(__version__, label="startup")


def _check_system_packages(packages: list[str]) -> list[str]:
    """Check which packages from the list are NOT importable from the system.

    Returns a list of package names that failed to import.
    Note: Packages that import successfully will remain in sys.modules.
    """
    import importlib
    missing = []
    for pkg in packages:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def _check_vendor_conflicts() -> list[str]:
    """Check if any vendored packages are already loaded (potential conflicts)."""
    if _USING_SYSTEM_PACKAGES:
        return []

    conflicts = []
    for pkg in _CONFLICT_WATCH_PACKAGES:
        if pkg in sys.modules:
            conflicts.append(pkg)
    return conflicts


def _setup_vendor_path() -> None:
    """Add shared vendor directory to sys.path."""
    global _USING_SYSTEM_PACKAGES

    vendor_dir = Path(__file__).parent / "vendor"

    if not vendor_dir.exists():
        # No vendor directory — check if system provides the packages (e.g. Nix, pip from source)
        missing = _check_system_packages(_VENDOR_PACKAGES)
        if not missing:
            _USING_SYSTEM_PACKAGES = True
            print("AnkiMCP Server: vendor/ not found — using system-provided packages")
            return

        print(
            f"AnkiMCP Server Error: vendor/ directory not found and system is missing "
            f"required packages: {', '.join(missing)}\n"
            f"If you installed from source, install dependencies with:\n"
            f"  pip install {' '.join(missing)}\n"
            f"Or download the .ankiaddon release from "
            f"https://github.com/ankimcp/anki-mcp-server-addon/releases"
        )
        raise ImportError("AnkiMCP Server: required packages not available")

    # Check for conflicts before adding to path
    conflicts = _check_vendor_conflicts()
    if conflicts:
        # Console warning for users running Anki from a terminal.
        print(
            f"AnkiMCP Server Warning: Packages {conflicts} already loaded by another addon. "
            "This may cause compatibility issues. If you experience problems, "
            "try disabling other addons that might use these packages."
        )
        # Route the real detail (whose copy is live) to the file log. Advisory
        # only — we never abort. Each entry is gathered defensively so one bad
        # module object can't break the loop.
        logger = get_logger()
        logger.warning(
            "Vendor conflict: %d package(s) already loaded by another add-on "
            "before our vendor path was prepended.",
            len(conflicts),
        )
        for name in conflicts:
            try:
                logger.warning("  conflict: %s : %s", name, _module_provenance(name))
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("  conflict: %s (introspection failed: %r)", name, exc)

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
from .dependency_loader import ensure_pydantic_core, ensure_rpds

if not ensure_pydantic_core():
    print("AnkiMCP Server Error: Failed to load pydantic_core. Addon will not function.")
    # Log the real failure detail (the pre-flight classification from the
    # dependency loader already wrote diagnostics; this records that the
    # addon is aborting because of it).
    get_logger().error(
        "Aborting addon import: ensure_pydantic_core() returned False. "
        "See preceding file-log entries for the underlying failure detail."
    )
    # Don't load the rest of the addon
    raise ImportError("AnkiMCP Server: pydantic_core not available")

# Ensure rpds (the only compiled dep in the mcp -> jsonschema -> referencing
# chain) is importable before that chain runs via the connection_manager import
# below. We don't vendor rpds — the bundle can only carry one platform's binary,
# which crashed on Windows / Linux-aarch64 / macOS-Intel (issue #54). Anki
# normally provides rpds, so this is a no-op; otherwise it downloads the wheel.
if not ensure_rpds():
    print("AnkiMCP Server Error: Failed to load rpds. Addon will not function.")
    get_logger().error("Aborting addon import: ensure_rpds() returned False.")
    # Don't load the rest of the addon
    raise ImportError("AnkiMCP Server: rpds not available")

# Re-log provenance after dependency loading so we can see what loaded as a
# result of our startup (vs the pristine pre-vendor snapshot above).
log_diagnostics_snapshot(__version__, label="post-dependency-load")

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
from .diagnostics_section import DiagnosticsSection
from .http_auth import validate_http_api_key
from .tool_decorator import validate_disabled_tools, validate_enabled_destructive_tools
from .transport_security_config import validate_http_allowlist
from .tunnel.ui import toolbar_indicator
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

    # Register the API key as a secret so file logging never writes it to disk
    # (OAuth tokens register themselves via CredentialsManager when loaded).
    from .file_log import register_secret
    register_secret(config.http_api_key)

    # Validate config and collect warnings for the user
    warnings: list[str] = []
    warnings.extend(validate_disabled_tools(config.disabled_tools))
    warnings.extend(
        validate_enabled_destructive_tools(config.enabled_destructive_tools)
    )
    warnings.extend(validate_http_allowlist(config))
    warnings.extend(validate_http_api_key(config))
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
    """Add AnkiMCP Server to Tools menu and install the toolbar indicator."""
    action = QAction("AnkiMCP Server Settings...", mw)
    action.triggered.connect(_show_settings)
    mw.form.menuTools.addAction(action)

    # Persistent tunnel-status item in the top toolbar (opt-out via the
    # show_toolbar_indicator config flag; the change takes effect on restart).
    # Config is global to the addon, so it's read here directly rather than
    # via _config_manager, which isn't set until a profile opens. Reads the
    # live connection manager lazily (recreated per profile, None before the
    # first profile opens) and reuses _show_settings as the click handler.
    if ConfigManager(__name__).load().show_toolbar_indicator:
        toolbar_indicator.register(
            state_provider=lambda: _connection_manager,
            on_click=_show_settings,
        )


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

    # -- Diagnostics section --
    diagnostics_section = DiagnosticsSection(_connection_manager, config, parent=dialog)
    layout.addWidget(diagnostics_section)
    layout.addSpacing(6)

    # -- Separator --
    sep3 = QFrame(frameShape=QFrame.Shape.HLine)
    sep3.setFrameShadow(QFrame.Shadow.Sunken)
    layout.addWidget(sep3)
    layout.addSpacing(6)

    # -- Footer --
    dashboard_label = QLabel("<b>Tunnel Dashboard:</b> <a href='https://web.ankimcp.ai'>https://web.ankimcp.ai</a>")
    dashboard_label.setOpenExternalLinks(True)
    layout.addWidget(dashboard_label)
    community_label = QLabel("<b>Community:</b> <a href='https://forum.ankimcp.ai'>https://forum.ankimcp.ai</a>")
    community_label.setOpenExternalLinks(True)
    layout.addWidget(community_label)
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
