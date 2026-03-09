"""Tests for ChromaKBClient._recency_boost."""

from datetime import date, datetime, timedelta

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corp_rfp_agent.kb.chromadb_impl import ChromaKBClient


class TestRecencyBoost:
    """Unit tests for the static _recency_boost method."""

    def test_recent_entry_gets_max_boost(self):
        """Entry from last 6 months gets 0.10 boost."""
        recent = (date.today() - timedelta(days=30)).isoformat()
        assert ChromaKBClient._recency_boost(recent) == 0.10

    def test_6_to_12_months_gets_medium_boost(self):
        """Entry from 6-12 months ago gets 0.05 boost."""
        mid = (date.today() - timedelta(days=270)).isoformat()
        assert ChromaKBClient._recency_boost(mid) == 0.05

    def test_1_to_2_years_gets_small_boost(self):
        """Entry from 1-2 years ago gets 0.02 boost."""
        old = (date.today() - timedelta(days=500)).isoformat()
        assert ChromaKBClient._recency_boost(old) == 0.02

    def test_older_than_2_years_gets_no_boost(self):
        """Entry older than 2 years gets 0.0."""
        ancient = "2020-01-01"
        assert ChromaKBClient._recency_boost(ancient) == 0.0

    def test_empty_string_returns_zero(self):
        """Empty date string returns 0.0."""
        assert ChromaKBClient._recency_boost("") == 0.0

    def test_malformed_date_returns_zero(self):
        """Malformed date returns 0.0 without crashing."""
        assert ChromaKBClient._recency_boost("not-a-date") == 0.0
        assert ChromaKBClient._recency_boost("2025/13/45") == 0.0

    def test_boundary_180_days(self):
        """Exactly 180 days old gets 0.10 (inclusive)."""
        boundary = (date.today() - timedelta(days=180)).isoformat()
        assert ChromaKBClient._recency_boost(boundary) == 0.10

    def test_boundary_181_days(self):
        """181 days old gets 0.05 (just past 6 months)."""
        boundary = (date.today() - timedelta(days=181)).isoformat()
        assert ChromaKBClient._recency_boost(boundary) == 0.05
