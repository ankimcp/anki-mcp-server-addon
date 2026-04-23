"""Validation module for media tools — prevents path traversal and SSRF attacks.

All media-related inputs (file paths, URLs, filenames) must pass through these
validators before any I/O occurs. Each validator raises a specific HandlerError
subclass with an actionable hint for the AI client, while logging security-relevant
details (resolved paths, detected MIME types, resolved IPs) at WARNING level
for the addon operator's audit trail.

Only stdlib dependencies — no third-party packages.
"""

import ipaddress
import logging
import mimetypes
import os
import socket
import urllib.parse
from pathlib import Path
from typing import Optional

from .handler_wrappers import HandlerError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default allowed MIME type prefixes for media files
# ---------------------------------------------------------------------------
_DEFAULT_MEDIA_PREFIXES = ("image/", "audio/", "video/")


# ---------------------------------------------------------------------------
# Custom error classes
# ---------------------------------------------------------------------------

class MediaFileTypeError(HandlerError):
    """File extension is not in the allowed MIME types for media import."""

    def __init__(self, filename: str) -> None:
        super().__init__(
            "File type not allowed for media import",
            hint=(
                "Only image, audio, and video files are allowed. "
                "Check the file extension and try again."
            ),
            code="validation_error",
            filename=filename,
        )


class MediaImportDirError(HandlerError):
    """Resolved file path falls outside the allowed import directory."""

    def __init__(self, filename: str) -> None:
        super().__init__(
            "File path is outside the allowed import directory",
            hint=(
                "The file must be located inside the configured import directory. "
                "Move the file there or adjust the import_dir setting."
            ),
            code="validation_error",
            filename=filename,
        )


class MediaUrlSchemeError(HandlerError):
    """URL uses a non-HTTP(S) scheme."""

    def __init__(self, scheme: str) -> None:
        super().__init__(
            f"URL scheme '{scheme}' is not allowed",
            hint="Only http and https URLs are supported.",
            code="validation_error",
        )


class MediaUrlBlockedError(HandlerError):
    """URL resolves to a private, reserved, loopback, or otherwise blocked IP."""

    def __init__(self) -> None:
        super().__init__(
            "URL target is blocked",
            hint=(
                "The URL points to a restricted network address. "
                "Use a publicly routable URL instead."
            ),
            code="validation_error",
        )


# ---------------------------------------------------------------------------
# Part 2: File path validation
# ---------------------------------------------------------------------------

def validate_media_file_path(
    file_path: str,
    *,
    allowed_types: Optional[list[str]] = None,
    import_dir: Optional[str] = None,
) -> Path:
    """Validate and resolve a media file path.

    Canonicalizes the path, checks the MIME type against an allow-list, and
    optionally enforces that the file lives inside a specific directory.

    Args:
        file_path: Raw file path string from the client.
        allowed_types: Extra MIME types to permit beyond the defaults
            (e.g. ``["application/pdf"]``).
        import_dir: If set, the resolved path must be inside this directory.

    Returns:
        The resolved ``pathlib.Path``.

    Raises:
        MediaFileTypeError: Extension maps to a disallowed or unknown MIME type,
            or the path contains null bytes.
        MediaImportDirError: Resolved path is outside *import_dir*.
    """
    # 1. Reject null bytes early — classic path injection vector.
    if "\0" in file_path:
        logger.warning(
            "Null byte in file path rejected: %r", file_path[:120]
        )
        raise MediaFileTypeError(os.path.basename(file_path.replace("\0", "")))

    # 2. Canonicalize (collapses ../, resolves symlinks).
    resolved = Path(file_path).resolve()

    # 3. Detect MIME type from extension.
    mime_type, _ = mimetypes.guess_type(resolved.name)

    # 4. Build the effective allow-set.
    extra: set[str] = set(allowed_types) if allowed_types else set()

    def _is_allowed(mt: Optional[str]) -> bool:
        if mt is None:
            return False
        if mt in extra:
            return True
        return any(mt.startswith(prefix) for prefix in _DEFAULT_MEDIA_PREFIXES)

    # 5. Audit log — always, regardless of outcome.
    logger.warning(
        "Media file path validation: resolved=%s, mime=%s", resolved, mime_type
    )

    # 6. Reject disallowed / unknown types.
    if not _is_allowed(mime_type):
        raise MediaFileTypeError(resolved.name)

    # 7. Directory confinement check.
    if import_dir:
        allowed_root = Path(import_dir).resolve()
        # Use os.sep suffix trick so "/tmp/evil" doesn't match "/tmp/ev".
        if not (
            str(resolved).startswith(str(allowed_root) + os.sep)
        ):
            logger.warning(
                "Path traversal blocked: resolved=%s, allowed_root=%s",
                resolved,
                allowed_root,
            )
            raise MediaImportDirError(resolved.name)

    return resolved


# ---------------------------------------------------------------------------
# Part 3: URL validation
# ---------------------------------------------------------------------------

def _check_ip_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP address belongs to a restricted range."""
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_media_url(
    url: str,
    *,
    allowed_hosts: Optional[list[str]] = None,
) -> str:
    """Validate a media URL against SSRF vectors.

    Parses the URL, enforces HTTP(S), resolves the hostname to an IP via DNS,
    and blocks private / reserved / loopback addresses unless the host is
    explicitly allow-listed.

    Args:
        url: Raw URL string from the client.
        allowed_hosts: Hostnames or IP strings that bypass the private-range
            check (e.g. ``["media.example.com", "10.0.0.5"]``).

    Returns:
        The original *url* string (unchanged) if validation passes.

    Raises:
        MediaUrlBlockedError: URL is malformed, hostname cannot be resolved,
            or the resolved IP is in a restricted range.
        MediaUrlSchemeError: Scheme is not ``http`` or ``https``.

    Note:
        This validation is subject to DNS rebinding (TOCTOU). The IP is resolved
        and checked here, but the actual HTTP client will re-resolve the hostname
        when fetching. An attacker controlling DNS could return a public IP for
        this check and a private IP for the actual fetch. This is an inherent
        limitation when the validator cannot control the downstream HTTP client.

    Also note: urllib.request.urlopen follows HTTP redirects by default.
    An attacker can host a public URL that 302-redirects to a private IP.
    The initial URL passes validation but the redirect target is not checked.
    This is a known limitation shared with the Node.js CLI implementation.
    """
    # 1. Parse.
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise MediaUrlBlockedError()

    # 2. Scheme check — before hostname, because non-HTTP schemes (file:, ftp:)
    #    often have no hostname and we want the specific error, not a generic block.
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise MediaUrlSchemeError(scheme or "<empty>")

    if not parsed.hostname:
        raise MediaUrlBlockedError()

    hostname = parsed.hostname
    safe_hosts: set[str] = {h.lower() for h in allowed_hosts} if allowed_hosts else set()

    # 3. DNS resolution — socket.getaddrinfo handles IPv4 and IPv6.
    try:
        addr_infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        logger.warning("DNS resolution failed for hostname: %s", hostname)
        raise MediaUrlBlockedError()

    if not addr_infos:
        raise MediaUrlBlockedError()

    # Check every resolved address — a hostname can resolve to multiple IPs.
    for family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)

        # 4. Audit log.
        logger.warning(
            "Media URL validation: hostname=%s, resolved_ip=%s", hostname, ip
        )

        # 5. Allow-list bypass.
        if hostname in safe_hosts or ip_str in safe_hosts:
            continue

        # 6. Restricted-range check.
        if _check_ip_blocked(ip):
            raise MediaUrlBlockedError()

        # 7. IPv4-mapped IPv6 (e.g. ::ffff:192.168.1.1).
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            if _check_ip_blocked(ip.ipv4_mapped):
                raise MediaUrlBlockedError()

    return url


# ---------------------------------------------------------------------------
# Part 4: Filename sanitisation
# ---------------------------------------------------------------------------

def validate_media_filename_type(
    filename: str,
    *,
    allowed_types: list[str] | None = None,
) -> None:
    """Validate that a filename has an allowed media MIME type.

    Unlike validate_media_file_path which validates a full file path,
    this only checks the filename's extension against the MIME allowlist.
    Use this for data/URL sources where there's no path to validate.

    Args:
        filename: The filename to check (already sanitized).
        allowed_types: Extra MIME types beyond defaults.

    Raises:
        MediaFileTypeError: Extension maps to a disallowed or unknown MIME type.
    """
    mime_type, _ = mimetypes.guess_type(filename)
    extra: set[str] = set(allowed_types) if allowed_types else set()

    def _is_allowed(mt: str | None) -> bool:
        if mt is None:
            return False
        if mt in extra:
            return True
        return any(mt.startswith(prefix) for prefix in _DEFAULT_MEDIA_PREFIXES)

    if not _is_allowed(mime_type):
        logger.warning("Filename MIME check failed: filename=%s, mime=%s", filename, mime_type)
        raise MediaFileTypeError(filename)


def sanitize_media_filename(filename: str) -> str:
    """Sanitize a filename for safe storage in Anki's media folder.

    Strips dangerous characters and sequences that could cause path traversal
    or other filesystem surprises.

    Args:
        filename: Raw filename string (not a full path).

    Returns:
        A safe, non-empty filename string.
    """
    # 1. Null bytes.
    name = filename.replace("\0", "")
    # 2. Path separators — remove BEFORE traversal sequences so that
    #    inputs like "./" can't recombine into ".." after separator removal.
    name = name.replace("/", "").replace("\\", "")
    # 3. Directory traversal sequences — loop because removal can create
    #    new ".." sequences (e.g. "....//" → after step 2 → "...." → "..").
    while ".." in name:
        name = name.replace("..", "")
    # 4. Basename (defence-in-depth after separator removal).
    name = os.path.basename(name)
    # 5. Whitespace.
    name = name.strip()
    # 6. Empty / dot-only guard.
    if not name or name in (".", ".."):
        return "unnamed"
    return name
