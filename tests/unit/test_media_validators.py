"""Unit tests for anki_mcp_server.media_validators.

Tests cover all three public functions: validate_media_file_path,
validate_media_url, and sanitize_media_filename.  Organised by function,
one class per function, following the existing test style in
test_tool_filtering.py.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_mcp_server.media_validators import (
    MediaFileTypeError,
    MediaImportDirError,
    MediaUrlBlockedError,
    MediaUrlSchemeError,
    sanitize_media_filename,
    validate_media_file_path,
    validate_media_url,
)


# ---------------------------------------------------------------------------
# Helpers for mocking socket.getaddrinfo
# ---------------------------------------------------------------------------

def _fake_addrinfo(ip: str, family: int = socket.AF_INET):
    """Build a single getaddrinfo result tuple for a given IP string."""
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]


def _patch_dns(ip: str, family: int = socket.AF_INET):
    """Return a ``patch`` context that makes every DNS lookup resolve to *ip*."""
    return patch(
        "anki_mcp_server.media_validators.socket.getaddrinfo",
        return_value=_fake_addrinfo(ip, family),
    )


# ===========================================================================
# validate_media_file_path
# ===========================================================================


class TestValidateMediaFilePath:
    """Tests for validate_media_file_path()."""

    # ---- Allowed extensions (image) ----------------------------------------

    @pytest.mark.parametrize("ext", [
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ])
    def test_image_extensions_allowed(self, tmp_path: Path, ext: str):
        f = tmp_path / f"photo{ext}"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(str(f))
        assert result == f.resolve()

    # ---- Allowed extensions (audio) ----------------------------------------

    @pytest.mark.parametrize("ext", [
        ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac",
    ])
    def test_audio_extensions_allowed(self, tmp_path: Path, ext: str):
        f = tmp_path / f"clip{ext}"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(str(f))
        assert result == f.resolve()

    # ---- Allowed extensions (video) ----------------------------------------

    @pytest.mark.parametrize("ext", [
        ".mp4", ".webm", ".avi", ".mkv", ".mov",
    ])
    def test_video_extensions_allowed(self, tmp_path: Path, ext: str):
        f = tmp_path / f"movie{ext}"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(str(f))
        assert result == f.resolve()

    # ---- Blocked file types ------------------------------------------------

    @pytest.mark.parametrize("filename", [
        "credentials.json",
        "script.py",
        "data.csv",
        "archive.zip",
        "document.pdf",
    ])
    def test_blocked_extensions(self, tmp_path: Path, filename: str):
        f = tmp_path / filename
        f.write_bytes(b"\x00")
        with pytest.raises(MediaFileTypeError):
            validate_media_file_path(str(f))

    @pytest.mark.parametrize("filename", [
        "id_rsa",               # no extension (ssh key)
        "file_with_no_extension",
    ])
    def test_blocked_no_extension(self, tmp_path: Path, filename: str):
        f = tmp_path / filename
        f.write_bytes(b"\x00")
        with pytest.raises(MediaFileTypeError):
            validate_media_file_path(str(f))

    def test_blocked_dotfile_env(self, tmp_path: Path):
        """Dotfiles like .env have no MIME type and should be blocked."""
        f = tmp_path / ".env"
        f.write_bytes(b"\x00")
        with pytest.raises(MediaFileTypeError):
            validate_media_file_path(str(f))

    def test_blocked_etc_passwd(self, tmp_path: Path):
        """/etc/passwd has no extension and should be blocked."""
        f = tmp_path / "passwd"
        f.write_bytes(b"\x00")
        with pytest.raises(MediaFileTypeError):
            validate_media_file_path(str(f))

    # ---- Null byte injection -----------------------------------------------

    def test_null_byte_in_path_raises(self):
        with pytest.raises(MediaFileTypeError):
            validate_media_file_path("/etc/passwd\0.jpg")

    def test_null_byte_complex_injection(self):
        with pytest.raises(MediaFileTypeError):
            validate_media_file_path("/photos/image\0.ssh/id_rsa.jpg")

    # ---- allowed_types config ----------------------------------------------

    def test_allowed_types_pdf(self, tmp_path: Path):
        """Extra allowed_types should permit otherwise-blocked MIME types."""
        f = tmp_path / "document.pdf"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(
            str(f), allowed_types=["application/pdf"]
        )
        assert result == f.resolve()

    def test_default_types_still_work_with_extra(self, tmp_path: Path):
        """Default media types remain allowed when extra types are set."""
        f = tmp_path / "image.png"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(
            str(f), allowed_types=["application/pdf"]
        )
        assert result == f.resolve()

    def test_extra_types_dont_open_everything(self, tmp_path: Path):
        """Adding one extra type does not allow unrelated types."""
        f = tmp_path / "script.py"
        f.write_bytes(b"\x00")
        with pytest.raises(MediaFileTypeError):
            validate_media_file_path(
                str(f), allowed_types=["application/pdf"]
            )

    # ---- import_dir restriction --------------------------------------------

    def test_file_inside_import_dir(self, tmp_path: Path):
        f = tmp_path / "image.jpg"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(str(f), import_dir=str(tmp_path))
        assert result == f.resolve()

    def test_file_in_subdirectory_of_import_dir(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "image.jpg"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(str(f), import_dir=str(tmp_path))
        assert result == f.resolve()

    def test_file_outside_import_dir(self, tmp_path: Path):
        import_dir = tmp_path / "allowed"
        import_dir.mkdir()
        f = tmp_path / "image.jpg"
        f.write_bytes(b"\x00")
        with pytest.raises(MediaImportDirError):
            validate_media_file_path(str(f), import_dir=str(import_dir))

    def test_path_traversal_blocked_by_import_dir(self, tmp_path: Path):
        """../../../etc/passwd.jpg resolves outside import_dir."""
        import_dir = tmp_path / "allowed"
        import_dir.mkdir()
        traversal = str(import_dir / ".." / ".." / ".." / "etc" / "passwd.jpg")
        with pytest.raises(MediaImportDirError):
            validate_media_file_path(traversal, import_dir=str(import_dir))

    def test_empty_import_dir_means_no_restriction(self, tmp_path: Path):
        """Empty string import_dir is treated as no restriction."""
        f = tmp_path / "image.jpg"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(str(f), import_dir="")
        assert result == f.resolve()

    def test_none_import_dir_means_no_restriction(self, tmp_path: Path):
        """None import_dir is treated as no restriction."""
        f = tmp_path / "image.jpg"
        f.write_bytes(b"\x00")
        result = validate_media_file_path(str(f), import_dir=None)
        assert result == f.resolve()

    # ---- Path resolution ---------------------------------------------------

    def test_symlink_outside_import_dir_blocked(self, tmp_path: Path):
        """Symlink pointing outside import_dir should be blocked after resolve()."""
        import_dir = tmp_path / "allowed"
        import_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        real_file = outside / "image.jpg"
        real_file.write_bytes(b"\x00")

        link = import_dir / "sneaky.jpg"
        link.symlink_to(real_file)

        with pytest.raises(MediaImportDirError):
            validate_media_file_path(str(link), import_dir=str(import_dir))

    def test_relative_path_resolves(self, tmp_path: Path, monkeypatch):
        """Relative paths like ./image.jpg resolve against cwd."""
        f = tmp_path / "image.jpg"
        f.write_bytes(b"\x00")
        monkeypatch.chdir(tmp_path)
        result = validate_media_file_path("./image.jpg")
        assert result == f.resolve()


# ===========================================================================
# validate_media_url
# ===========================================================================


class TestValidateMediaUrl:
    """Tests for validate_media_url()."""

    # ---- Allowed URLs ------------------------------------------------------

    def test_http_public_url_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = validate_media_url("http://example.com/image.jpg")
        assert result == "http://example.com/image.jpg"

    def test_https_public_url_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = validate_media_url("https://cdn.example.com/photo.png")
        assert result == "https://cdn.example.com/photo.png"

    def test_url_with_port_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = validate_media_url("http://example.com:8080/img.jpg")
        assert result == "http://example.com:8080/img.jpg"

    # ---- Blocked schemes ---------------------------------------------------

    def test_file_scheme_blocked(self):
        with pytest.raises(MediaUrlSchemeError) as exc_info:
            validate_media_url("file:///etc/passwd")
        assert "file" in str(exc_info.value)

    def test_ftp_scheme_blocked(self):
        with pytest.raises(MediaUrlSchemeError) as exc_info:
            validate_media_url("ftp://files.example.com/image.jpg")
        assert "ftp" in str(exc_info.value)

    def test_gopher_scheme_blocked(self):
        with pytest.raises(MediaUrlSchemeError):
            validate_media_url("gopher://evil.com/")

    def test_empty_scheme_blocked(self):
        """URL with no scheme should raise MediaUrlSchemeError."""
        with pytest.raises(MediaUrlSchemeError):
            validate_media_url("example.com/image.jpg")

    # ---- Blocked IPs (IPv4) ------------------------------------------------

    @pytest.mark.parametrize("ip,label", [
        ("10.0.0.1", "10.x private"),
        ("172.16.0.1", "172.16.x private"),
        ("192.168.1.1", "192.168.x private"),
        ("127.0.0.1", "loopback"),
        ("169.254.169.254", "link-local / cloud metadata"),
        ("0.0.0.0", "unspecified"),
        ("224.0.0.1", "multicast"),
        ("255.255.255.255", "broadcast"),
    ])
    def test_blocked_ipv4(self, ip: str, label: str):
        with _patch_dns(ip):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url(f"http://evil.example.com/{label}")

    def test_carrier_grade_nat_not_blocked(self):
        """100.64.0.1 (carrier-grade NAT) is NOT in Python's private ranges.

        The stdlib ipaddress module does not classify 100.64.0.0/10 as
        private, reserved, or any other restricted category.  This test
        documents the actual behavior of the validator.
        """
        with _patch_dns("100.64.0.1"):
            result = validate_media_url("http://cgnat.example.com/img.jpg")
        assert result == "http://cgnat.example.com/img.jpg"

    # ---- Blocked IPs (IPv6) ------------------------------------------------

    def test_ipv6_loopback_blocked(self):
        with _patch_dns("::1", family=socket.AF_INET6):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url("http://ipv6host.example.com/img.jpg")

    def test_ipv6_mapped_private_blocked(self):
        """::ffff:192.168.1.1 is blocked (is_private=True on the IPv6 address itself)."""
        with _patch_dns("::ffff:192.168.1.1", family=socket.AF_INET6):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url("http://mapped.example.com/img.jpg")

    def test_ipv6_unique_local_blocked(self):
        with _patch_dns("fd00::1", family=socket.AF_INET6):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url("http://ula.example.com/img.jpg")

    def test_ipv6_public_allowed(self):
        with _patch_dns("2607:f8b0:4004:800::200e", family=socket.AF_INET6):
            result = validate_media_url("http://google.example.com/img.jpg")
        assert result == "http://google.example.com/img.jpg"

    # ---- allowed_hosts config ----------------------------------------------

    def test_allowed_hosts_bypasses_private_ip(self):
        """A hostname in allowed_hosts allows access even if IP is private."""
        with _patch_dns("192.168.1.1"):
            result = validate_media_url(
                "http://internal.company.com/img.jpg",
                allowed_hosts=["internal.company.com"],
            )
        assert result == "http://internal.company.com/img.jpg"

    def test_allowed_hosts_by_ip_string(self):
        """An IP string in allowed_hosts allows that specific IP."""
        with _patch_dns("10.0.0.5"):
            result = validate_media_url(
                "http://media-server.local/img.jpg",
                allowed_hosts=["10.0.0.5"],
            )
        assert result == "http://media-server.local/img.jpg"

    def test_non_listed_host_still_blocked(self):
        """Hosts not in allowed_hosts remain subject to IP checks."""
        with _patch_dns("192.168.1.1"):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url(
                    "http://evil.local/img.jpg",
                    allowed_hosts=["safe.company.com"],
                )

    def test_allowed_hosts_case_insensitive(self):
        """allowed_hosts should match case-insensitively since urlparse lowercases hostnames."""
        with _patch_dns("192.168.1.1"):
            # Mixed-case entry should still match lowered hostname
            result = validate_media_url(
                "http://internal.company.com/img.jpg",
                allowed_hosts=["Internal.Company.Com"],
            )
            assert result == "http://internal.company.com/img.jpg"

    # ---- DNS failure -------------------------------------------------------

    def test_dns_failure_raises_blocked(self):
        """socket.gaierror during DNS resolution should raise MediaUrlBlockedError."""
        with patch(
            "anki_mcp_server.media_validators.socket.getaddrinfo",
            side_effect=socket.gaierror("Name resolution failed"),
        ):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url("http://nonexistent.invalid/img.jpg")

    # ---- Invalid URLs ------------------------------------------------------

    def test_completely_invalid_url(self):
        """A string that is not a URL at all."""
        with pytest.raises((MediaUrlSchemeError, MediaUrlBlockedError)):
            validate_media_url("not a url at all")

    def test_empty_string(self):
        with pytest.raises(MediaUrlSchemeError):
            validate_media_url("")

    def test_url_with_no_hostname(self):
        """http:/// has scheme but no hostname."""
        with pytest.raises(MediaUrlBlockedError):
            validate_media_url("http:///path/to/file")

    # ---- Multiple resolved IPs ---------------------------------------------

    def test_all_ips_must_be_safe(self):
        """If a hostname resolves to multiple IPs and one is private, block."""
        mixed_results = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.1", 0)),
        ]
        with patch(
            "anki_mcp_server.media_validators.socket.getaddrinfo",
            return_value=mixed_results,
        ):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url("http://dual-homed.example.com/img.jpg")

    def test_empty_addrinfo_raises(self):
        """If getaddrinfo returns empty list, should raise."""
        with patch(
            "anki_mcp_server.media_validators.socket.getaddrinfo",
            return_value=[],
        ):
            with pytest.raises(MediaUrlBlockedError):
                validate_media_url("http://empty-dns.example.com/img.jpg")


# ===========================================================================
# sanitize_media_filename
# ===========================================================================


class TestSanitizeMediaFilename:
    """Tests for sanitize_media_filename()."""

    # ---- Simple filenames (no change) --------------------------------------

    def test_simple_filename(self):
        assert sanitize_media_filename("image.jpg") == "image.jpg"

    def test_filename_with_spaces_and_parens(self):
        assert sanitize_media_filename("my file (1).jpg") == "my file (1).jpg"

    def test_underscore_prefix(self):
        assert sanitize_media_filename("_hidden.png") == "_hidden.png"

    # ---- Path traversal stripping ------------------------------------------

    def test_dotdot_slash_stripped(self):
        assert sanitize_media_filename("../../evil.jpg") == "evil.jpg"

    def test_path_separators_removed(self):
        result = sanitize_media_filename("path/to/file.jpg")
        assert result == "pathtofile.jpg"

    def test_backslash_separators_removed(self):
        result = sanitize_media_filename("path\\to\\file.jpg")
        assert result == "pathtofile.jpg"

    def test_mixed_separators(self):
        result = sanitize_media_filename("../path\\..\\file.jpg")
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result
        assert result.endswith("file.jpg")

    def test_deep_traversal(self):
        """../../../etc/passwd.jpg has separators removed then .. removed."""
        result = sanitize_media_filename("../../../etc/passwd.jpg")
        assert ".." not in result
        assert "/" not in result
        # After removing / -> "......etcpasswd.jpg", then removing .. -> "etcpasswd.jpg"
        assert result == "etcpasswd.jpg"

    # ---- Null byte stripping -----------------------------------------------

    def test_null_byte_removed(self):
        assert sanitize_media_filename("image\0.jpg") == "image.jpg"

    # ---- The ./. bypass (bug we fixed) -------------------------------------

    def test_dot_slash_dot_returns_unnamed(self):
        """./. should become unnamed after sanitisation."""
        result = sanitize_media_filename("./.")
        assert result == "unnamed"

    # ---- Empty / degenerate inputs -----------------------------------------

    def test_empty_string(self):
        assert sanitize_media_filename("") == "unnamed"

    def test_whitespace_only(self):
        assert sanitize_media_filename("   ") == "unnamed"

    def test_single_dot(self):
        assert sanitize_media_filename(".") == "unnamed"

    def test_double_dot(self):
        assert sanitize_media_filename("..") == "unnamed"

    def test_single_slash(self):
        assert sanitize_media_filename("/") == "unnamed"

    def test_dot_dot_slash(self):
        assert sanitize_media_filename("../") == "unnamed"

    def test_multiple_slashes(self):
        assert sanitize_media_filename("////") == "unnamed"

    def test_only_backslashes(self):
        assert sanitize_media_filename("\\\\\\\\") == "unnamed"

    def test_dots_and_slashes_combo(self):
        """Various combinations of dots and slashes should all become unnamed."""
        assert sanitize_media_filename("././..") == "unnamed"
        assert sanitize_media_filename("../..") == "unnamed"
