"""TLS context factory for the tunnel's outbound connections.

Both tunnel transports — the login/refresh HTTP calls in ``auth.py`` and the
WebSocket in ``client.py`` — get their context from here, so verification and
error reporting are the same for both. The context is
``ssl.create_default_context()``, i.e. the OS trust store under Anki's
truststore injection. httpx's default instead reads a CA *file*
(``SSL_CERT_FILE`` or certifi's ``cacert.pem``) while constructing the client,
and an unreadable file there used to break login with a bare
``[Errno 13] Permission denied``.

Stdlib-only: no Qt, no vendored packages, no other add-on modules.
"""

from __future__ import annotations

import logging
import os
import ssl
import threading

logger = logging.getLogger(__name__)

_CERT_FILE_ENV = "SSL_CERT_FILE"

# Paths already reported at WARNING. Holds path strings only, never a context.
_warned_cert_files: set[str] = set()
_warned_cert_files_lock = threading.Lock()


class TLSContextError(Exception):
    """Raised when no usable TLS context can be built."""


def create_tls_context() -> ssl.SSLContext:
    """Build a fresh client TLS context backed by the OS trust store.

    ``SSL_CERT_FILE`` is honoured; one that can't be loaded is logged (WARNING
    once per path, then DEBUG) and skipped here on every platform. Under
    truststore on macOS/Windows the OS verifies, with a loadable file's CAs
    added on top. Where OpenSSL verifies (Linux, or Python without truststore)
    OpenSSL already uses ``SSL_CERT_FILE`` in place of its default CA file, so
    an unusable one can still fail later as a normal certificate-verify error.

    Every call returns a NEW context — never cache or share one. Anki 25.x
    ships truststore < 0.10.4, whose ``SSLContext`` is not safe for concurrent
    handshakes from different threads (it toggles ``check_hostname`` /
    ``verify_mode`` on a shared inner context without a lock), and login runs
    on a QThread event loop while the tunnel runs on the MCP background loop.

    Raises:
        TLSContextError: If the context itself can't be created (e.g. the
            system store or ``SSLKEYLOGFILE`` can't be opened).
    """
    try:
        ctx = ssl.create_default_context()
    except OSError as exc:
        raise TLSContextError(
            f"Could not create a TLS context: {type(exc).__name__}: {exc}"
        ) from exc

    cert_file = os.environ.get(_CERT_FILE_ENV)
    if cert_file:
        try:
            ctx.load_verify_locations(cafile=cert_file)
        except OSError as exc:
            _report_unloadable_cert_file(cert_file, exc)

    return ctx


def _report_unloadable_cert_file(cert_file: str, exc: OSError) -> None:
    """Log an unloadable ``SSL_CERT_FILE`` at WARNING once per path, then DEBUG."""
    with _warned_cert_files_lock:
        first_time = cert_file not in _warned_cert_files
        _warned_cert_files.add(cert_file)
    logger.log(
        logging.WARNING if first_time else logging.DEBUG,
        "Could not load %s=%r (%s: %s); continuing without it. "
        "If TLS verification fails, fix or unset %s.",
        _CERT_FILE_ENV,
        cert_file,
        type(exc).__name__,
        exc,
        _CERT_FILE_ENV,
    )
