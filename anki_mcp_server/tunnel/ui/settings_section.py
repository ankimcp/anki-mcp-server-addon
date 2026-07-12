"""Tunnel settings section widget for the AnkiMCP settings dialog.

Embeddable QWidget that displays tunnel status, control buttons, and a
scrollable event log. Designed to be placed inside a parent dialog alongside
the HTTP server status section.

UI module -- depends on ConnectionManager for tunnel state and control.
"""

from __future__ import annotations

import logging

from aqt.qt import (
    QApplication,
    QFont,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    Qt,
    QTextCharFormat,
    QTimer,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...connection_manager import ConnectionManager
from ..log import LogEntry, format_entry
from .login_dialog import LoginDialog

logger = logging.getLogger(__name__)


class TunnelSettingsSection(QWidget):
    """Tunnel control panel for the settings dialog.

    Shows tunnel status, Connect/Disconnect/Logout buttons, URL with copy,
    user tier info, and scrollable event log.

    The widget refreshes its display every second via a QTimer to keep
    status labels current.
    """

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
        self._populate_log()
        self._refresh_status()

        # Connect to live log updates.
        self._cm.tunnel_log.entry_added.connect(self._on_log_entry)

        # Refresh status every second for countdown and state changes.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_status)
        self._refresh_timer.start(1000)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # -- Section header --
        header = QLabel("<b>Cloud Tunnel</b>")
        header_font = QFont()
        header_font.setPointSize(12)
        header.setFont(header_font)
        layout.addWidget(header)

        separator = QFrame(frameShape=QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        # -- Status area (dynamic content) --
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._expiry_label = QLabel()
        self._expiry_label.setWordWrap(True)
        layout.addWidget(self._expiry_label)

        # -- URL row --
        url_layout = QHBoxLayout()
        url_layout.setContentsMargins(0, 0, 0, 0)

        self._url_label = QLabel()
        self._url_label.setWordWrap(True)
        self._url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._url_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        url_layout.addWidget(self._url_label)

        self._copy_button = QPushButton("Copy")
        self._copy_button.setFixedWidth(60)
        self._copy_button.clicked.connect(self._on_copy_url)
        url_layout.addWidget(self._copy_button)

        layout.addLayout(url_layout)

        # -- Button row --
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)

        self._connect_button = QPushButton("Connect Tunnel")
        self._connect_button.clicked.connect(self._on_connect)
        button_layout.addWidget(self._connect_button)

        self._disconnect_button = QPushButton("Disconnect")
        self._disconnect_button.clicked.connect(self._on_disconnect)
        button_layout.addWidget(self._disconnect_button)

        button_layout.addStretch()

        self._logout_button = QPushButton("Logout")
        self._logout_button.clicked.connect(self._on_logout)
        button_layout.addWidget(self._logout_button)

        layout.addLayout(button_layout)

        # -- Log section --
        layout.addSpacing(4)
        log_header = QLabel("<b>Log</b>")
        layout.addWidget(log_header)

        self._log_display = QPlainTextEdit()
        self._log_display.setReadOnly(True)
        self._log_display.setMaximumHeight(150)
        log_font = QFont("Courier")
        log_font.setPointSize(10)
        self._log_display.setFont(log_font)
        self._log_display.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._log_display)

        # -- Log action buttons --
        log_button_layout = QHBoxLayout()
        log_button_layout.setContentsMargins(0, 0, 0, 0)
        log_button_layout.addStretch()

        copy_log_button = QPushButton("Copy Log")
        copy_log_button.setFixedWidth(80)
        copy_log_button.clicked.connect(self._on_copy_log)
        log_button_layout.addWidget(copy_log_button)

        clear_log_button = QPushButton("Clear")
        clear_log_button.setFixedWidth(60)
        clear_log_button.clicked.connect(self._on_clear_log)
        log_button_layout.addWidget(clear_log_button)

        layout.addLayout(log_button_layout)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Status refresh (called every second by QTimer)
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        """Update all dynamic labels and button visibility based on current state."""
        connected = self._cm.tunnel_connected
        active = self._cm.tunnel_active
        url = self._cm.tunnel_url
        user = self._cm.tunnel_user

        if connected and url:
            # -- Connected state --
            email = user.get("email", "unknown") if user else "unknown"
            tier = user.get("tier", "free") if user else "free"
            tier_display = "Free plan" if tier == "free" else tier.capitalize() + " plan"

            self._status_label.setText(
                f"Connected as <b>{email}</b> ({tier_display})"
            )

            # Expiry indicator (tunnel URLs do not expire; paid tiers show a
            # "Permanent URL" hint, free tier shows nothing).
            if tier != "free":
                self._expiry_label.setText("Permanent URL")
            else:
                self._expiry_label.setText("")

            self._expiry_label.setVisible(True)

            # URL row
            self._url_label.setText(f"<b>URL:</b> {url}")
            self._url_label.setVisible(True)
            self._copy_button.setVisible(True)

            # Buttons
            self._connect_button.setVisible(False)
            self._disconnect_button.setVisible(True)
            self._logout_button.setVisible(True)

        elif active:
            # -- Connecting / reconnecting state --
            self._status_label.setText("Status: Connecting...")
            self._expiry_label.setVisible(False)

            # URL row hidden
            self._url_label.setVisible(False)
            self._copy_button.setVisible(False)

            # Connect button becomes Stop
            self._connect_button.setVisible(True)
            self._connect_button.setEnabled(True)
            self._connect_button.setText("Stop")
            self._disconnect_button.setVisible(False)
            self._logout_button.setVisible(False)

        else:
            # -- Disconnected state --
            has_credentials = user is not None
            self._status_label.setText("Status: Not connected")
            self._expiry_label.setVisible(False)

            # URL row hidden
            self._url_label.setVisible(False)
            self._copy_button.setVisible(False)

            # Buttons
            self._connect_button.setVisible(True)
            self._connect_button.setEnabled(True)
            self._connect_button.setText("Connect Tunnel")
            self._disconnect_button.setVisible(False)
            self._logout_button.setVisible(has_credentials)

        # Hosted mode: unattended, no human operator. Suppress every
        # interactive auth control (Connect/Disconnect/Logout) regardless of
        # state, so nobody is invited to drive the device-flow login or delete
        # the provisioned credentials. The read-only status / URL / user
        # display above stays fully functional (useful for VNC debugging).
        # Applied last so it wins over the per-state visibility set above.
        if self._config.hosted_mode:
            self._connect_button.setVisible(False)
            self._disconnect_button.setVisible(False)
            self._logout_button.setVisible(False)

    # ------------------------------------------------------------------
    # Log display
    # ------------------------------------------------------------------

    def _populate_log(self) -> None:
        """Fill the log display with existing entries."""
        entries = self._cm.tunnel_log.get_entries()
        for entry in entries:
            self._append_log_entry(entry)

    def _on_log_entry(self, entry: LogEntry) -> None:
        """Handle a new log entry arriving (via Qt signal)."""
        self._append_log_entry(entry)

    def _append_log_entry(self, entry: LogEntry) -> None:
        """Append a formatted log entry to the display, with color for errors."""
        text = format_entry(entry)

        cursor = self._log_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)

        fmt = QTextCharFormat()
        if entry.level == "error":
            fmt.setForeground(Qt.GlobalColor.red)
        else:
            # Use the widget's default text color so it works in both
            # light and dark themes (hardcoded black is invisible in dark mode).
            fmt.setForeground(self._log_display.palette().text().color())

        cursor.insertText(text + "\n", fmt)
        self._log_display.setTextCursor(cursor)

        # Auto-scroll to bottom
        scrollbar = self._log_display.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_connect(self) -> None:
        """Handle Connect / Stop button click.

        This button serves double duty:
        - Disconnected state: text is "Connect Tunnel" -- starts connection
        - Connecting/reconnecting state: text is "Stop" -- cancels connection
        """
        # Hosted mode has no interactive login and no human. The button is
        # hidden in _refresh_status, but guard defensively so the device-flow /
        # credential-write path can NEVER be triggered while hosted.
        if self._config.hosted_mode:
            return

        # If the tunnel task is active (connecting/reconnecting), stop it.
        if self._cm.tunnel_active:
            self._cm.disconnect_tunnel()
            self._refresh_status()
            return

        # Otherwise, start a new connection.
        # Check for existing credentials first.
        credentials = self._cm.credentials_manager.load()

        if credentials is None:
            # No credentials -- show login dialog
            dialog = LoginDialog(
                server_url=self._config.tunnel_server_url,
                client_id=self._config.tunnel_client_id,
                parent=self.window(),
            )
            result = dialog.exec()
            if result != LoginDialog.DialogCode.Accepted or dialog.credentials is None:
                return
            # Credentials were saved by LoginDialog._on_success

        # Now connect
        self._connect_button.setEnabled(False)
        self._connect_button.setText("Connecting...")
        self._cm.connect_tunnel()

    def _on_disconnect(self) -> None:
        """Handle Disconnect button click."""
        self._cm.disconnect_tunnel()
        self._refresh_status()

    def _on_logout(self) -> None:
        """Handle Logout button click."""
        self._cm.logout_tunnel()
        self._refresh_status()

    def _on_copy_url(self) -> None:
        """Copy the tunnel URL to clipboard."""
        url = self._cm.tunnel_url
        if url:
            QApplication.clipboard().setText(url)

    def _on_copy_log(self) -> None:
        """Copy all log entries to clipboard as plain text."""
        text = self._log_display.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _on_clear_log(self) -> None:
        """Clear the log display and the underlying buffer."""
        self._cm.tunnel_log.clear()
        self._log_display.clear()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def destroy(self, *args, **kwargs) -> None:  # type: ignore[override]
        """Disconnect signals and stop timers before widget destruction.

        Explicitly disconnecting the ``entry_added`` signal prevents a
        race condition where the asyncio thread emits the signal after
        the widget starts being destroyed but before Qt's automatic
        disconnection takes effect.
        """
        self._refresh_timer.stop()
        try:
            self._cm.tunnel_log.entry_added.disconnect(self._on_log_entry)
        except (TypeError, RuntimeError):
            pass  # Already disconnected or object deleted
        super().destroy(*args, **kwargs)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        """Stop timer when widget is hidden (dialog closed)."""
        self._refresh_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        """Restart timer when widget becomes visible again."""
        self._refresh_status()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(1000)
        super().showEvent(event)
