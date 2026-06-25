"""Diagnostics controls for the AnkiMCP settings dialog.

Embeddable QWidget with two buttons:

* **Open log folder** — opens the ``user_files`` directory (where
  ``ankimcp.log`` lives) in the OS file manager via ``QDesktopServices``.
* **Copy diagnostics** — assembles the diagnostics snapshot (the SAME block
  the startup log writes) and copies it to the clipboard, formatted for a
  forum paste.

UI-only module. The snapshot itself is built by ``file_log.build_diagnostics_snapshot``
(stdlib-only, single source of truth) — this widget only renders controls and
gathers the live transport state from the ConnectionManager.
"""

from __future__ import annotations

from pathlib import Path

from aqt.qt import (
    QApplication,
    QDesktopServices,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QUrl,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import Config
from .connection_manager import ConnectionManager
from .file_log import build_diagnostics_snapshot, is_enabled

# The log lives in the addon's user_files directory (same place credentials are
# stored). Resolve it relative to this module so it matches CredentialsManager.
_USER_FILES_DIR = Path(__file__).resolve().parent / "user_files"


class DiagnosticsSection(QWidget):
    """Diagnostics control panel (Open log folder + Copy diagnostics)."""

    def __init__(
        self,
        connection_manager: ConnectionManager,
        config: Config,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cm = connection_manager
        self._config = config
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QLabel("<b>Diagnostics</b>")
        layout.addWidget(header)

        if not is_enabled():
            hint = QLabel(
                "<i>File logging is off. Set <code>log_to_file</code> to true in the "
                "add-on config and restart to capture a log.</i>"
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)

        open_folder_button = QPushButton("Open log folder")
        open_folder_button.clicked.connect(self._on_open_log_folder)
        button_row.addWidget(open_folder_button)

        copy_button = QPushButton("Copy diagnostics")
        copy_button.clicked.connect(self._on_copy_diagnostics)
        button_row.addWidget(copy_button)

        button_row.addStretch()
        layout.addLayout(button_row)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_open_log_folder(self) -> None:
        """Open the user_files directory (where ankimcp.log lives)."""
        try:
            _USER_FILES_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(_USER_FILES_DIR)))

    def _on_copy_diagnostics(self) -> None:
        """Assemble the diagnostics snapshot and copy it to the clipboard."""
        snapshot = build_diagnostics_snapshot(
            addon_version=__version__,
            transports=self._transport_lines(),
        )
        QApplication.clipboard().setText(snapshot)

    # ------------------------------------------------------------------
    # Transport state — gathered from the live ConnectionManager
    # ------------------------------------------------------------------

    def _transport_lines(self) -> list[str]:
        """Human-readable transport-state lines for the snapshot."""
        http = "enabled" if self._config.http_enabled else "disabled"
        if self._config.http_enabled:
            http += " (running)" if self._cm.http_running else " (not running)"

        if self._cm.tunnel_connected:
            tunnel = "connected"
        elif self._cm.tunnel_active:
            tunnel = "connecting/reconnecting"
        else:
            tunnel = "not connected"

        return [f"HTTP   : {http}", f"Tunnel : {tunnel}"]
