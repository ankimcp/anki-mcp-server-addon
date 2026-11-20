"""The TLS block of ``file_log.build_diagnostics_snapshot``.

It feeds both the startup log and "Copy diagnostics", so it must be built from
``os.path`` + ``ssl`` alone. Certificate files are probed with a read-only
open (``os.access`` ignores ACLs and share locks on Windows) after an ``isfile``
guard; ``SSLKEYLOGFILE`` is never opened, certifi is never force-imported, and
one failing field must not abort the snapshot.
"""
from __future__ import annotations

import builtins
import os
import ssl
import stat
import sys
import types

import pytest

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup

from anki_mcp_server import file_log

_ENV_VARS = ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE")
_FILE_VARS = ("SSL_CERT_FILE", "SSLKEYLOGFILE")


class InjectedContext(ssl.SSLContext):
    pass


def _skip_unless_permission_bits_apply() -> None:
    if os.name == "nt":
        pytest.skip("POSIX permission bits don't apply on Windows")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can open a chmod 000 file")


def _record_opens(monkeypatch) -> list[tuple[str, str]]:
    opened: list[tuple[str, str]] = []
    real_open = builtins.open

    def recording_open(file, mode="r", *args, **kwargs):
        opened.append((os.fspath(file) if isinstance(file, (str, os.PathLike)) else repr(file), mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording_open)
    return opened


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _tls_lines() -> list[str]:
    snapshot = file_log.build_diagnostics_snapshot(addon_version="0.0.0")
    lines = snapshot.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("TLS"))
    return lines[start + 1 : -1]


def _value(label: str) -> str:
    for line in _tls_lines():
        key, _, value = line.partition(": ")
        if key.strip() == label:
            return value
    raise AssertionError(f"no {label!r} line in {_tls_lines()!r}")


class TestEnvVars:

    @pytest.mark.parametrize("name", _ENV_VARS)
    def test_unset(self, name) -> None:
        assert _value(name) == "not set"

    @pytest.mark.parametrize("name", _FILE_VARS)
    def test_existing_file(self, name, tmp_path, monkeypatch) -> None:
        path = tmp_path / "ca.pem"
        path.write_text("x")
        monkeypatch.setenv(name, str(path))
        assert _value(name) == f"{path}  [file]"

    def test_cert_dir_pointing_at_a_file(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "ca.pem"
        path.write_text("x")
        monkeypatch.setenv("SSL_CERT_DIR", str(path))
        assert _value("SSL_CERT_DIR") == f"{path}  [not a directory]"

    @pytest.mark.parametrize("name", _ENV_VARS)
    def test_directory(self, name, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(name, str(tmp_path))
        assert _value(name) == f"{tmp_path}  [directory]"

    @pytest.mark.parametrize("name", _ENV_VARS)
    def test_missing(self, name, tmp_path, monkeypatch) -> None:
        path = tmp_path / "nope.pem"
        monkeypatch.setenv(name, str(path))
        assert _value(name) == f"{path}  [missing]"

    def test_empty_value(self, monkeypatch) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", "")
        assert _value("SSL_CERT_FILE") == "(empty)"

    def test_unreadable_cert_file_reports_open_error(self, tmp_path, monkeypatch) -> None:
        _skip_unless_permission_bits_apply()
        path = tmp_path / "locked.pem"
        path.write_text("x")
        path.chmod(0o000)
        monkeypatch.setenv("SSL_CERT_FILE", str(path))
        try:
            value = _value("SSL_CERT_FILE")
        finally:
            path.chmod(0o600)
        assert value.startswith(f"{path}  [file, cannot open: PermissionError:")

    def test_unreadable_keylog_file_is_not_probed(self, tmp_path, monkeypatch) -> None:
        _skip_unless_permission_bits_apply()
        path = tmp_path / "keys.log"
        path.write_text("x")
        path.chmod(0o000)
        monkeypatch.setenv("SSLKEYLOGFILE", str(path))
        try:
            assert _value("SSLKEYLOGFILE") == f"{path}  [file]"
        finally:
            path.chmod(0o600)

    @pytest.mark.parametrize("name", ("SSL_CERT_FILE", "SSLKEYLOGFILE"))
    def test_fifo_is_not_opened(self, name, tmp_path, monkeypatch) -> None:
        if not hasattr(os, "mkfifo"):
            pytest.skip("os.mkfifo is unavailable on this platform")
        path = tmp_path / "pipe"
        os.mkfifo(path)
        assert stat.S_ISFIFO(os.stat(path).st_mode)
        # A held read/write end means even a missing guard can't block in open():
        # it would fail the assertions below instead of hanging the run.
        keeper = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        try:
            opened = _record_opens(monkeypatch)
            monkeypatch.setenv(name, str(path))

            assert _value(name) == f"{path}  [not a regular file]"
            assert str(path) not in [p for p, _ in opened]
        finally:
            os.close(keeper)


class TestOpenProbe:

    def test_cert_file_is_opened_read_only_and_unchanged(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "ca.pem"
        path.write_text("cert")
        before = path.stat()
        monkeypatch.setenv("SSL_CERT_FILE", str(path))
        opened = _record_opens(monkeypatch)

        assert _value("SSL_CERT_FILE") == f"{path}  [file]"

        modes = {m for p, m in opened if p == str(path)}
        assert modes == {"rb"}
        after = path.stat()
        assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
        assert path.read_text() == "cert"

    def test_keylog_file_is_never_opened(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "keys.log"
        path.write_text("secret keys")
        before = path.stat()
        monkeypatch.setenv("SSLKEYLOGFILE", str(path))
        opened = _record_opens(monkeypatch)

        assert _value("SSLKEYLOGFILE") == f"{path}  [file]"

        assert str(path) not in [p for p, _ in opened]
        after = path.stat()
        assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
        assert path.read_text() == "secret keys"

    def test_cert_dir_is_never_opened(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
        opened = _record_opens(monkeypatch)
        assert _value("SSL_CERT_DIR") == f"{tmp_path}  [directory]"
        assert str(tmp_path) not in [p for p, _ in opened]


class TestTrustBackend:

    def test_reports_current_context_class(self) -> None:
        cls = ssl.SSLContext
        assert _value("ssl.SSLContext") == f"{cls.__module__}.{cls.__qualname__}"

    def test_replaced_context_reports_defining_module(self, monkeypatch) -> None:
        monkeypatch.setattr(InjectedContext, "__module__", "fake_truststore._api")
        monkeypatch.setattr(ssl, "SSLContext", InjectedContext)
        assert _value("ssl.SSLContext") == "fake_truststore._api.InjectedContext"

    def test_openssl_version(self) -> None:
        assert _value("OpenSSL") == ssl.OPENSSL_VERSION


class TestVerifyPaths:

    def test_reports_default_verify_paths(self, monkeypatch) -> None:
        fake = ssl.DefaultVerifyPaths(
            cafile=None,
            capath=None,
            openssl_cafile_env="SSL_CERT_FILE",
            openssl_cafile="/etc/ssl/cert.pem",
            openssl_capath_env="SSL_CERT_DIR",
            openssl_capath="/etc/ssl/certs",
        )
        monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: fake)
        assert _value("verify cafile") == "None"
        assert _value("verify capath") == "None"
        assert _value("openssl cafile") == "/etc/ssl/cert.pem"
        assert _value("openssl capath") == "/etc/ssl/certs"

    def test_unreadable_verify_cafile_reports_open_error(self, tmp_path, monkeypatch) -> None:
        _skip_unless_permission_bits_apply()
        path = tmp_path / "locked.pem"
        path.write_text("x")
        path.chmod(0o000)
        fake = ssl.DefaultVerifyPaths(
            cafile=str(path),
            capath=None,
            openssl_cafile_env="SSL_CERT_FILE",
            openssl_cafile="/etc/ssl/cert.pem",
            openssl_capath_env="SSL_CERT_DIR",
            openssl_capath="/etc/ssl/certs",
        )
        monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: fake)
        try:
            value = _value("verify cafile")
        finally:
            path.chmod(0o600)
        assert value.startswith(f"{path}  [file, cannot open: PermissionError:")

    def test_non_str_cafile_is_a_placeholder_and_never_opened(self, monkeypatch) -> None:
        fake = ssl.DefaultVerifyPaths(
            cafile=b"/etc/ssl/cert.pem",
            capath=7,
            openssl_cafile_env="SSL_CERT_FILE",
            openssl_cafile="/etc/ssl/cert.pem",
            openssl_capath_env="SSL_CERT_DIR",
            openssl_capath="/etc/ssl/certs",
        )
        monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: fake)
        opened = _record_opens(monkeypatch)
        assert _value("verify cafile") == "<unexpected bytes>"
        assert _value("verify capath") == "<unexpected int>"
        assert repr(b"/etc/ssl/cert.pem") not in [p for p, _ in opened]
        assert repr(7) not in [p for p, _ in opened]

    def test_failure_becomes_placeholder(self, monkeypatch) -> None:
        def boom():
            raise OSError("no verify paths")

        monkeypatch.setattr(ssl, "get_default_verify_paths", boom)
        assert "no verify paths" in _value("default verify paths")
        assert _value("OpenSSL") == ssl.OPENSSL_VERSION


class TestCertifiBundle:

    def test_not_loaded_is_not_force_imported(self, monkeypatch) -> None:
        monkeypatch.delitem(sys.modules, "certifi", raising=False)
        assert _value("certifi bundle") == "not loaded"
        assert "certifi" not in sys.modules

    def test_loaded_reports_path_and_state(self, tmp_path, monkeypatch) -> None:
        bundle = tmp_path / "cacert.pem"
        bundle.write_text("x")
        fake = types.ModuleType("certifi")
        fake.where = lambda: str(bundle)
        monkeypatch.setitem(sys.modules, "certifi", fake)
        assert _value("certifi bundle") == f"{bundle}  [file]"

    def test_loaded_with_missing_bundle(self, tmp_path, monkeypatch) -> None:
        bundle = tmp_path / "gone.pem"
        fake = types.ModuleType("certifi")
        fake.where = lambda: str(bundle)
        monkeypatch.setitem(sys.modules, "certifi", fake)
        assert _value("certifi bundle") == f"{bundle}  [missing]"

    def test_unreadable_bundle_reports_open_error(self, tmp_path, monkeypatch) -> None:
        _skip_unless_permission_bits_apply()
        bundle = tmp_path / "locked.pem"
        bundle.write_text("x")
        bundle.chmod(0o000)
        fake = types.ModuleType("certifi")
        fake.where = lambda: str(bundle)
        monkeypatch.setitem(sys.modules, "certifi", fake)
        try:
            value = _value("certifi bundle")
        finally:
            bundle.chmod(0o600)
        assert value.startswith(f"{bundle}  [file, cannot open: PermissionError:")

    def test_where_returning_an_fd_is_not_treated_as_a_path(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "held.bin"
        path.write_text("x")
        fd = os.open(path, os.O_RDONLY)
        try:
            fake = types.ModuleType("certifi")
            fake.where = lambda: fd
            monkeypatch.setitem(sys.modules, "certifi", fake)

            assert _value("certifi bundle") == "<unexpected int>"
            os.fstat(fd)  # raises OSError if the probe closed someone else's fd
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def test_where_returning_bytes_is_a_placeholder(self, tmp_path, monkeypatch) -> None:
        bundle = tmp_path / "cacert.pem"
        bundle.write_text("x")
        fake = types.ModuleType("certifi")
        fake.where = lambda: os.fsencode(bundle)
        monkeypatch.setitem(sys.modules, "certifi", fake)
        opened = _record_opens(monkeypatch)

        assert _value("certifi bundle") == "<unexpected bytes>"
        assert str(bundle) not in [p for p, _ in opened]
        assert repr(os.fsencode(bundle)) not in [p for p, _ in opened]

    def test_where_raising_becomes_placeholder(self, monkeypatch) -> None:
        def boom():
            raise RuntimeError("where failed")

        fake = types.ModuleType("certifi")
        fake.where = boom
        monkeypatch.setitem(sys.modules, "certifi", fake)
        assert "where failed" in _value("certifi bundle")
        assert _value("OpenSSL") == ssl.OPENSSL_VERSION
