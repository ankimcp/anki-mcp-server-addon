"""Configuration management for AnkiMCP Server addon.

This module provides configuration dataclass and manager for persisting
addon settings using Anki's built-in configuration system.
"""

from dataclasses import dataclass, field, asdict
from typing import Literal, Callable, List


@dataclass
class Config:
    """
    Addon configuration.

    All fields have sensible defaults - addon works without any configuration.
    Users can configure everything via GUI or by editing JSON directly.
    """

    # Connection mode (only http for now)
    mode: Literal["http"] = "http"

    # HTTP settings
    http_port: int = 3141
    http_host: str = "127.0.0.1"
    http_path: str = ""

    # CORS settings (list of allowed origins, empty = CORS disabled)
    # Example: ["https://inspector.example.com", "http://localhost:5173"]
    # Use ["*"] to allow all origins (not recommended for production)
    cors_origins: List[str] = field(default_factory=list)

    # CORS expose headers (headers browser JavaScript can read from responses)
    # MCP protocol requires session ID header to be readable
    cors_expose_headers: List[str] = field(
        default_factory=lambda: ["mcp-session-id", "mcp-protocol-version"]
    )

    # General
    auto_connect_on_startup: bool = True

    def is_valid_for_mode(self) -> tuple[bool, str]:
        """
        Check if config is valid for current mode.

        Returns:
            Tuple of (is_valid, error_message). If valid, error_message is empty string.

        Examples:
            >>> config = Config(mode="http", http_port=80)
            >>> config.is_valid_for_mode()
            (True, '')

            >>> config = Config(mode="http", http_port=70000)
            >>> config.is_valid_for_mode()
            (False, 'Port must be between 1 and 65535')
        """
        if self.mode == "http":
            if not (1 <= self.http_port <= 65535):
                return False, "Port must be between 1 and 65535"
            return True, ""
        return False, f"Unknown mode: {self.mode}"

    def to_dict(self) -> dict:
        """
        Convert to dict for Anki's config storage.

        Returns:
            Dictionary representation of config suitable for JSON serialization.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """
        Create from dict, using defaults for missing keys.

        Only includes keys that are actual dataclass fields, ignoring
        any extra keys in the input dict. This provides forward compatibility
        if we remove fields in future versions.

        Args:
            data: Dictionary with config values (typically from JSON).

        Returns:
            Config instance with provided values merged with defaults.

        Examples:
            >>> Config.from_dict({"http_port": 8080})
            Config(mode='http', http_port=8080, ...)

            >>> Config.from_dict({"unknown_field": "ignored"})
            Config(mode='http', ...)  # Uses all defaults
        """
        return cls(
            **{k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        )


class ConfigManager:
    """
    Manages configuration persistence.

    Uses Anki's addon config system which stores data in:
    - Default values: config.json (shipped with addon)
    - User values: meta.json (auto-managed by Anki)

    The manager handles loading, saving, and change notifications.
    """

    def __init__(self, addon_name: str):
        """
        Initialize config manager.

        Args:
            addon_name: Name of the addon (usually __name__ of the main module).
        """
        self._addon_name = addon_name
        self._listeners: list[Callable[[Config], None]] = []

    def load(self) -> Config:
        """
        Load config, merging defaults with user overrides.

        Imports aqt at runtime to allow testing without Anki installation.

        Returns:
            Config instance with current settings.
        """
        from aqt import mw

        raw = mw.addonManager.getConfig(self._addon_name) or {}
        return Config.from_dict(raw)

    def save(self, config: Config) -> None:
        """
        Save config and notify listeners.

        Imports aqt at runtime to allow testing without Anki installation.

        Args:
            config: Config instance to persist.
        """
        from aqt import mw

        mw.addonManager.writeConfig(self._addon_name, config.to_dict())
        for listener in self._listeners:
            listener(config)

    def on_change(self, callback: Callable[[Config], None]) -> None:
        """
        Register callback for config changes.

        Callback will be invoked after config is saved to disk,
        allowing components to react to configuration updates.

        Args:
            callback: Function that accepts a Config instance.

        Examples:
            >>> manager = ConfigManager("ankimcp")
            >>> manager.on_change(lambda cfg: print(f"Mode changed to {cfg.mode}"))
        """
        self._listeners.append(callback)

    def get_default(self) -> Config:
        """
        Get default config (ignores user changes).

        Returns:
            Config instance with all default values.
        """
        return Config()
