"""TLS context factory for the tunnel's outbound connections.

Both tunnel transports — the login/refresh HTTP calls in ``auth.py`` and the
WebSocket in ``client.py`` — get their context from here, so verification and
error reporting are the same for both. The context is
``ssl.create_default_context()``, i.e. the OS trust store under Anki's
truststore injection. httpx's default instead reads a CA *file*
(``SSL_CERT_FILE`` or certifi's ``cacert.pem``) while constructing the client,
and an unreadable file there used to break login with a bare
``[Errno 13] Permission denied``.

It also owns the one description of a certificate-verification failure, so
both transports tell the user the same thing.

Stdlib-only: no Qt, no vendored packages, no other add-on modules.
"""

from __future__ import annotations

import logging
import os
import re
import ssl
import threading
from typing import Iterator

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


_MAX_CHAIN_DEPTH = 16
_OPENSSL_PREFIX_RE = re.compile(r"^\[SSL: [A-Z0-9_]+\]\s*(certificate verify failed:?\s*)?")
_OPENSSL_SUFFIX_RE = re.compile(r"\s*\(_ssl\.c:\d+\)\s*$")


def find_certificate_error(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    """The certificate-verification failure behind ``exc``, if there is one.

    Found by class anywhere in the ``__cause__`` / ``__context__`` chain, never
    by message text: httpx/httpcore and websockets each wrap the ssl error
    under their own exception. Other ``ssl.SSLError`` subclasses (a handshake
    failure, an unsupported protocol) are not certificate failures.
    """
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending and len(seen) < _MAX_CHAIN_DEPTH:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return current
        pending.extend(
            e for e in (current.__cause__, current.__context__) if e is not None
        )
    return None


def _flatten_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _flatten_strings(item)


def _verification_reason(exc: ssl.SSLCertVerificationError) -> str:
    """The OS/OpenSSL reason as plain text: no tuple repr, no ``(_ssl.c:N)`` tail."""
    reason = getattr(exc, "verify_message", None)
    if not isinstance(reason, str) or not reason.strip():
        parts = [p for p in _flatten_strings(exc.args) if p.strip()]
        reason = parts[-1] if parts else type(exc).__name__
    reason = _OPENSSL_SUFFIX_RE.sub("", reason.strip())
    reason = _OPENSSL_PREFIX_RE.sub("", reason).strip().rstrip(".")
    return reason or type(exc).__name__


def certificate_failure_message(
    exc: ssl.SSLCertVerificationError, *, host: str | None, proxy_route: str | None
) -> str:
    """Plain-language text for a certificate failure, shared by both transports.

    ``host`` is the server the connection was made to, not necessarily the one
    whose certificate failed: through an ``https://`` proxy, or under
    interception, it may be another certificate, which nothing here can tell.
    So the text only says where the connection was going and, when proxied, how
    it was routed. ``proxy_route`` is the already credential-masked route text
    (``ProxyRoute.proxied_display()``), ``None`` for a direct connection.
    """
    target = host or "the server"
    text = (
        "A security certificate could not be verified while "
        f"connecting to {target} ({_verification_reason(exc)})."
    )
    if proxy_route is not None:
        text += f" The connection is routed {proxy_route}."
    return (
        f"{text} This usually means a proxy, antivirus or company network is "
        "inspecting HTTPS traffic, or that your computer's date and time are "
        "wrong. If it is inspection, trust that product's root certificate in "
        "your operating system (or point the SSL_CERT_FILE environment "
        "variable at it) and restart Anki."
    )


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
