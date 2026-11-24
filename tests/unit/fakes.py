"""Test doubles shared by several unit-test modules."""
from __future__ import annotations


class FakeTunnelLog:
    """Records entries; stands in for the QObject-based TunnelLog."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.entries.append(("info", message))

    def error(self, message: str) -> None:
        self.entries.append(("error", message))
