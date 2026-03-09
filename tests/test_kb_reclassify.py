"""Tests for kb_reclassify -- category migration."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_reclassify import (
    mechanical_migrate,
    CATEGORY_MIGRATION,
    VALID_CATEGORIES,
    print_category_stats,
)


def _make_entry(category, **kwargs):
    base = {
        "canonical_question": "Test question",
        "canonical_answer": "Test answer",
        "category": category,
        "kb_id": "test_001",
        "_source_file": "test.json",
    }
    base.update(kwargs)
    return base


def test_mechanical_migration_security_to_technical():
    """security -> technical."""
    entries = [_make_entry("security")]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "technical"
    assert stats["migrated"] == 1


def test_mechanical_migration_commercial_to_customer_executive():
    """commercial -> customer_executive."""
    entries = [_make_entry("commercial")]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "customer_executive"
    assert stats["migrated"] == 1


def test_mechanical_migration_deployment_to_technical():
    """deployment -> technical."""
    entries = [_make_entry("deployment")]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "technical"
    assert stats["migrated"] == 1


def test_mechanical_migration_general_to_customer_executive():
    """general -> customer_executive."""
    entries = [_make_entry("general")]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "customer_executive"
    assert stats["migrated"] == 1


def test_mechanical_migration_functional_stays():
    """functional stays functional."""
    entries = [_make_entry("functional")]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "functional"
    assert stats["already_correct"] == 1
    assert stats["migrated"] == 0


def test_mechanical_migration_handles_mixed():
    """Mixed batch with all old category types."""
    entries = [
        _make_entry("security"),
        _make_entry("functional"),
        _make_entry("deployment"),
        _make_entry("commercial"),
        _make_entry("general"),
        _make_entry("technical"),
    ]
    stats = mechanical_migrate(entries)
    assert stats["migrated"] == 4  # security, deployment, commercial, general
    assert stats["already_correct"] == 2  # functional, technical

    # Verify final categories
    cats = [e["category"] for e in entries]
    assert all(c in VALID_CATEGORIES for c in cats)


def test_mechanical_migration_handles_missing_category():
    """Entry with no category field."""
    entries = [{"canonical_question": "Q", "canonical_answer": "A", "_source_file": "t.json"}]
    stats = mechanical_migrate(entries)
    assert stats["unmapped"] == 1


def test_mechanical_migration_handles_nan_category():
    """Entry with NaN float category doesn't crash."""
    entries = [{"canonical_question": "Q", "canonical_answer": "A",
                "category": float("nan"), "_source_file": "t.json"}]
    stats = mechanical_migrate(entries)
    assert stats["unmapped"] == 1


def test_mechanical_migration_handles_none_category():
    """Entry with None category doesn't crash."""
    entries = [{"canonical_question": "Q", "canonical_answer": "A",
                "category": None, "_source_file": "t.json"}]
    stats = mechanical_migrate(entries)
    assert stats["unmapped"] == 1


def test_category_stats_no_crash(capsys):
    """print_category_stats runs without error."""
    entries = [
        _make_entry("technical"),
        _make_entry("functional"),
        _make_entry("functional"),
    ]
    print_category_stats(entries)
    captured = capsys.readouterr()
    assert "technical" in captured.out
    assert "functional" in captured.out


def test_category_stats_handles_nan(capsys):
    """print_category_stats handles NaN/None categories."""
    entries = [
        _make_entry("technical"),
        {"canonical_question": "Q", "canonical_answer": "A", "category": float("nan")},
        {"canonical_question": "Q", "canonical_answer": "A", "category": None},
    ]
    print_category_stats(entries)
    captured = capsys.readouterr()
    assert "uncategorized" in captured.out
