"""Qt login dialog for the OAuth 2.0 Device Authorization Flow.

Presents the user code, opens the browser for verification, and polls
for token completion in a background thread. Self-contained: the caller
creates the dialog, calls ``exec()``, then checks ``dialog.credentials``.

UI module -- depends on auth + credentials, never the reverse.
"""

from __future__ import annotations

import asyncio
import logging
import webbrowser

from aqt.qt import (
    QDialog,
    QFont,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    Qt,
    QThread,
    QTimer,
    QVBoxLayout,
    pyqtSignal,
)

from ...credentials import Credentials, CredentialsManager
from ..auth import AuthError, DeviceCodeResponse, DeviceFlowAuth

logger = logging.getLogger(__name__)


def _open_url(url: str) -> None:
    """Open a URL in the default browser.

    Tries ``aqt.utils.openLink`` first (Anki's native helper), falls
    back to ``webbrowser.open``.
    """
    try:
        from aqt.utils import openLink

        openLink(url)
    except (ImportError, AttributeError):
        webbrowser.open(url)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _LoginWorker(QThread):
    """Runs the async device-code flow in a background thread.

    Creates its own asyncio event loop so the Qt main thread stays
    responsive. Emits signals for each stage of the flow so the dialog
    can update its widgets.

    Signals:
        device_code_received: Emitted once the server returns a user code.
        login_succeeded: Emitted when the user completes auth.
        login_failed: Emitted on any terminal error.
    """

    device_code_received = pyqtSignal(object)  # DeviceCodeResponse
    login_succeeded = pyqtSignal(object)        # Credentials
    login_failed = pyqtSignal(str)              # error message

    def __init__(
        self, server_url: str, client_id: str, parent: QDialog | None = None
    ) -> None:
        super().__init__(parent)
        self._server_url = server_url
        self._client_id = client_id
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- public API (called from main thread) --

    def cancel(self) -> None:
        """Request cancellation of the polling loop.

        Thread-safe: sets a flag that the async loop checks between
        polling iterations.
        """
        self._cancelled = True
        # If the loop is running, schedule its stop so asyncio.sleep
        # is interrupted promptly.
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    # -- QThread entry point --

    def run(self) -> None:
        """Run the full device-code flow on a dedicated event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._flow())
        except RuntimeError:
            # When cancel() calls loop.stop(), run_until_complete raises
            # RuntimeError ("Event loop stopped before Future completed").
            # That's expected -- don't treat it as an error.
            if not self._cancelled:
                raise
        finally:
            # Clean shutdown: cancel any lingering tasks, then close.
            _cancel_remaining_tasks(loop)
            loop.close()
            self._loop = None

    # -- async internals --

    async def _flow(self) -> None:
        """Run request_device_code -> poll loop, emitting signals."""
        auth = DeviceFlowAuth(self._server_url, self._client_id)

        # Step 1: get the user code.
        try:
            device = await auth.request_device_code()
        except AuthError as exc:
            self.login_failed.emit(f"Failed to start login: {exc}")
            return
        except Exception as exc:
            self.login_failed.emit(f"Unexpected error: {exc}")
            return

        if self._cancelled:
            return

        self.device_code_received.emit(device)

        # Step 2: poll until the user authorizes (or we're cancelled).
        # poll_for_token uses anyio.sleep internally, which is
        # cancellation-safe.  When cancel() calls loop.stop(), the
        # running task is interrupted and we land in the exception
        # handler below.
        try:
            credentials = await auth.poll_for_token(
                device.device_code, device.interval
            )
        except AuthError as exc:
            if not self._cancelled:
                self.login_failed.emit(str(exc))
            return
        except asyncio.CancelledError:
            # Expected when cancel() stops the event loop.
            return
        except Exception as exc:
            if not self._cancelled:
                self.login_failed.emit(f"Unexpected error: {exc}")
            return

        self.login_succeeded.emit(credentials)


def _cancel_remaining_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel all pending tasks on *loop* and drain them."""
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    if pending:
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Login dialog
# ---------------------------------------------------------------------------

class LoginDialog(QDialog):
    """Modal dialog for OAuth 2.0 Device Authorization login.

    Usage::

        dialog = LoginDialog(
            server_url="wss://tunnel.ankimcp.ai",
            client_id="ankimcp-cli",
            parent=mw,
        )
        dialog.exec()
        if dialog.credentials is not None:
            print("Logged in as", dialog.credentials.user.get("email"))
    """

    def __init__(
        self,
        server_url: str,
        client_id: str,
        parent: QDialog | None = None,
    ) -> None:
        super().__init__(parent)

        self.credentials: Credentials | None = None
        """Set on successful login; ``None`` if cancelled or failed."""

        self._server_url = server_url
        self._client_id = client_id
        self._device: DeviceCodeResponse | None = None
        self._worker: _LoginWorker | None = None

        self._setup_ui()
        self._start_worker()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle("Login to AnkiMCP")
        self.setMinimumWidth(420)
        self.setModal(True)

        layout = QVBoxLayout()
        layout.setSpacing(12)

        # -- Title --
        title = QLabel("Login to AnkiMCP")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # -- Instruction --
        self._instruction_label = QLabel("Requesting login code...")
        self._instruction_label.setWordWrap(True)
        layout.addWidget(self._instruction_label)

        # -- User code display (framed box) --
        code_frame = QFrame()
        code_frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
        code_frame.setLineWidth(1)
        code_frame_layout = QHBoxLayout()
        code_frame_layout.setContentsMargins(16, 12, 16, 12)

        self._code_label = QLabel("------")
        code_font = QFont("Courier")
        code_font.setPointSize(22)
        code_font.setBold(True)
        self._code_label.setFont(code_font)
        self._code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._code_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        code_frame_layout.addWidget(self._code_label)
        code_frame.setLayout(code_frame_layout)
        layout.addWidget(code_frame)

        # -- Open Browser button --
        self._browser_button = QPushButton("Open Browser")
        self._browser_button.setEnabled(False)
        self._browser_button.clicked.connect(self._on_open_browser)
        layout.addWidget(self._browser_button)

        # -- Manual URL --
        self._url_label = QLabel("")
        self._url_label.setWordWrap(True)
        self._url_label.setOpenExternalLinks(True)
        self._url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        layout.addWidget(self._url_label)

        # -- Status line --
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # -- Spacer --
        layout.addStretch()

        # -- Bottom button row --
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._retry_button = QPushButton("Retry")
        self._retry_button.setVisible(False)
        self._retry_button.clicked.connect(self._on_retry)
        button_layout.addWidget(self._retry_button)

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self._cancel_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        """Create and start the background login worker."""
        self._stop_worker()

        self._worker = _LoginWorker(
            self._server_url, self._client_id, parent=self
        )
        self._worker.device_code_received.connect(self._on_device_code)
        self._worker.login_succeeded.connect(self._on_success)
        self._worker.login_failed.connect(self._on_failure)
        self._worker.start()

        self._set_status("Requesting login code...")

    def _stop_worker(self) -> None:
        """Cancel and clean up the background worker, if any."""
        if self._worker is not None:
            self._worker.cancel()
            # Give the thread a moment to finish; don't block the UI
            # indefinitely.
            if not self._worker.wait(3000):
                logger.warning("Login worker did not stop within 3 seconds")
            self._worker = None

    # ------------------------------------------------------------------
    # Signal handlers (main thread)
    # ------------------------------------------------------------------

    def _on_device_code(self, device: DeviceCodeResponse) -> None:
        """Display the user code and enable the browser button."""
        self._device = device

        self._instruction_label.setText("Enter this code in your browser:")
        self._code_label.setText(device.user_code)
        self._browser_button.setEnabled(True)

        # Show manual URL as a clickable link.
        url = device.verification_uri
        self._url_label.setText(
            f"Or visit: <a href='{url}'>{url}</a>"
        )

        self._set_status("Waiting for authorization...")

    def _on_success(self, credentials: Credentials) -> None:
        """Save credentials, show success, then close after a short delay."""
        self.credentials = credentials

        # Persist credentials.
        try:
            CredentialsManager().save(credentials)
        except Exception as exc:
            logger.error("Failed to save credentials: %s", exc)

        # Update UI to show success.
        email = credentials.user.get("email", "unknown")
        self._instruction_label.setText("")
        self._code_label.setText("")
        self._url_label.setText("")
        self._browser_button.setVisible(False)
        self._retry_button.setVisible(False)

        success_font = QFont()
        success_font.setPointSize(12)
        self._status_label.setFont(success_font)
        self._status_label.setText(f"Logged in as {email}")

        self._cancel_button.setText("Close")

        # Auto-close after 1.5 seconds.
        QTimer.singleShot(1500, self.accept)

    def _on_failure(self, message: str) -> None:
        """Show the error and offer retry."""
        self._set_status(f"Login failed: {message}")
        self._retry_button.setVisible(True)
        self._browser_button.setEnabled(False)

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def _on_open_browser(self) -> None:
        """Open the verification URL in the system browser."""
        if self._device is None:
            return

        url = self._device.verification_uri_complete or self._device.verification_uri
        _open_url(url)

    def _on_retry(self) -> None:
        """Reset the UI and restart the device-code flow."""
        self._device = None
        self._retry_button.setVisible(False)
        self._instruction_label.setText("Requesting login code...")
        self._code_label.setText("------")
        self._url_label.setText("")
        self._browser_button.setEnabled(False)

        # Reset status label font in case it was changed by success path.
        self._status_label.setFont(QFont())

        self._start_worker()

    # ------------------------------------------------------------------
    # Dialog close handling
    # ------------------------------------------------------------------

    def reject(self) -> None:
        """Cancel button or window close."""
        self._stop_worker()
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Ensure the worker is stopped if the window is closed."""
        self._stop_worker()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
