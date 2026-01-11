"""E2E tests for MCP resources."""
from __future__ import annotations

import pytest

from .helpers import list_resources, read_resource


class TestResourceDiscovery:
    """Tests for resource listing and discovery."""

    def test_resources_list_returns_resources(self):
        """Server should return a list of resources."""
        resources = list_resources()
        assert isinstance(resources, list)
        assert len(resources) > 0

    def test_schema_resource_exists(self):
        """anki://schema resource should be registered."""
        resources = list_resources()
        uris = [r["uri"] for r in resources]
        assert "anki://schema" in uris

    def test_query_syntax_resource_exists(self):
        """anki://query-syntax resource should be registered."""
        resources = list_resources()
        uris = [r["uri"] for r in resources]
        assert "anki://query-syntax" in uris


class TestStaticResources:
    """Tests for static documentation resources."""

    def test_read_schema(self):
        """anki://schema should return schema documentation."""
        result = read_resource("anki://schema")
        # Should contain schema information
        assert result is not None

    def test_read_query_syntax(self):
        """anki://query-syntax should return query syntax documentation."""
        result = read_resource("anki://query-syntax")
        assert result is not None


class TestStatsResources:
    """Tests for statistics resources."""

    def test_stats_today(self):
        """anki://stats/today should return today's statistics."""
        result = read_resource("anki://stats/today")
        assert "cards_studied" in result or "content" in result

    def test_stats_forecast(self):
        """anki://stats/forecast should return 30-day forecast."""
        result = read_resource("anki://stats/forecast")
        assert "forecast" in result or "content" in result

    def test_stats_collection(self):
        """anki://stats/collection should return collection statistics."""
        result = read_resource("anki://stats/collection")
        assert "total_cards" in result or "content" in result
