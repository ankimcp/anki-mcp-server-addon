"""Reporting helpers for login failures (no Qt, no I/O beyond logging).

Shared by every failure handler of the login worker so the user-visible text
and the log record for a failure are decided in one place. This module formats
no response body, device code or user code itself, but the ``AuthError``
messages it logs can carry a short server-supplied excerpt (auth.py's non-JSON
and non-object-JSON paths, non-2xx responses only).
"""

from __future__ import annotations

import logging

from .auth import AuthError

logger = logging.getLogger(__name__)

# OAuth outcomes the user causes or expects (code timed out, access refused):
# shown in the dialog but not a fault, so they get no traceback.
_EXPECTED_OAUTH_CODES = frozenset({"expired_token", "access_denied"})


def unexpected_error_message(exc: BaseException) -> str:
    """User-visible text for a failure that isn't an ``AuthError``."""
    return f"Unexpected error: {type(exc).__name__}: {exc}"


def log_login_failure(step: str, exc: Exception) -> None:
    """Log a login failure with enough detail to diagnose it from the file log.

    Expected OAuth outcomes are logged at INFO without a traceback. Any other
    ``AuthError`` is a WARNING with ``exc_info`` so its chained ``__cause__``
    (the httpx/OSError behind "HTTP request failed") is recorded. Everything
    else is an ERROR with the full traceback.
    """
    if isinstance(exc, AuthError):
        # error_code comes from the server's JSON, so it may not be a string
        # (or hashable); only a str can name an expected outcome.
        code = exc.error_code
        if isinstance(code, str) and code in _EXPECTED_OAUTH_CODES:
            logger.info("Login ended while trying to %s: %s (%s)", step, exc, code)
        else:
            logger.warning("Login failed while trying to %s: %s", step, exc, exc_info=exc)
    else:
        logger.error(
            "Login failed unexpectedly while trying to %s: %s: %s",
            step,
            type(exc).__name__,
            exc,
            exc_info=exc,
        )
