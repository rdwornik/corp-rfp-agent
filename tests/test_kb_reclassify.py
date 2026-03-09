"""Tests for kb_reclassify -- category migration."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_reclassify import (
    mechanical_migrate,
    _classify_by_terms,
    _parse_llm_json,
    _safe_category,
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


# --- mechanical_migrate tests ---

def test_mechanical_migration_security_to_technical():
    """security -> technical (partial match)."""
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
    """deployment -> technical (default, no term match)."""
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

    cats = [e["category"] for e in entries]
    assert all(c in VALID_CATEGORIES for c in cats)


def test_mechanical_migration_handles_missing_category():
    """Entry with no category field -> technical (default)."""
    entries = [{"canonical_question": "Q", "canonical_answer": "A", "_source_file": "t.json"}]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "technical"
    assert stats["migrated"] == 1


def test_mechanical_migration_handles_nan_category():
    """Entry with NaN float category -> technical (default)."""
    entries = [{"canonical_question": "Q", "canonical_answer": "A",
                "category": float("nan"), "_source_file": "t.json"}]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "technical"
    assert stats["migrated"] == 1


def test_mechanical_migration_handles_none_category():
    """Entry with None category -> technical (default)."""
    entries = [{"canonical_question": "Q", "canonical_answer": "A",
                "category": None, "_source_file": "t.json"}]
    stats = mechanical_migrate(entries)
    assert entries[0]["category"] == "technical"
    assert stats["migrated"] == 1


# --- _classify_by_terms tests (partial matching) ---

def test_classify_fine_grained_security():
    """Fine-grained security categories -> technical (default)."""
    assert _classify_by_terms("cybersecurity") is None  # no term match -> None
    assert _classify_by_terms("network security & cryptography") is None
    assert _classify_by_terms("identity and access management (iam)") is None


def test_classify_implementation_to_consulting():
    """Implementation -> consulting."""
    assert _classify_by_terms("implementation") == "consulting"
    assert _classify_by_terms("implementation & deployment") == "consulting"


def test_classify_project_management_to_consulting():
    """Project management -> consulting."""
    assert _classify_by_terms("project management") == "consulting"
    assert _classify_by_terms("project - delivery") is None  # no match


def test_classify_change_management_to_consulting():
    """Change management -> consulting."""
    assert _classify_by_terms("change management") == "consulting"


def test_classify_data_management_to_default():
    """Data management -> None (no match, caller defaults to technical)."""
    assert _classify_by_terms("data management") is None
    assert _classify_by_terms("data management and quality") is None


def test_classify_references_to_customer_executive():
    """References -> customer_executive."""
    assert _classify_by_terms("project - references") == "customer_executive"


def test_classify_pricing_to_customer_executive():
    """Pricing/licensing -> customer_executive."""
    assert _classify_by_terms("pricing") == "customer_executive"
    assert _classify_by_terms("licensing") == "customer_executive"


def test_classify_training_to_consulting():
    """Training -> consulting."""
    assert _classify_by_terms("training / operational protections") == "consulting"
    assert _classify_by_terms("knowledge transfer and team empowerment") == "consulting"


def test_classify_valid_categories_unchanged():
    """Already valid categories stay as-is."""
    assert _classify_by_terms("technical") == "technical"
    assert _classify_by_terms("functional") == "functional"
    assert _classify_by_terms("customer_executive") == "customer_executive"
    assert _classify_by_terms("consulting") == "consulting"


# --- _parse_llm_json tests (3-strategy parsing) ---

def test_parse_direct_json():
    """Direct JSON parse works."""
    text = '[{"index": 0, "category": "technical", "confidence": 0.9}]'
    result = _parse_llm_json(text)
    assert len(result) == 1
    assert result[0]["category"] == "technical"


def test_parse_with_code_fences():
    """Strips markdown code fences."""
    text = '```json\n[{"index": 0, "category": "functional", "confidence": 0.8}]\n```'
    result = _parse_llm_json(text)
    assert result[0]["category"] == "functional"


def test_parse_with_trailing_text():
    """Regex extracts JSON array from surrounding text."""
    text = 'Here is the result:\n[{"index": 0, "category": "consulting", "confidence": 0.9}]\nDone.'
    result = _parse_llm_json(text)
    assert result[0]["category"] == "consulting"


def test_parse_empty_returns_empty():
    """Empty input returns empty list."""
    assert _parse_llm_json("") == []


def test_parse_garbage_raises():
    """Completely unparseable text raises JSONDecodeError."""
    with pytest.raises(json.JSONDecodeError):
        _parse_llm_json("this is not json at all")


# --- print_category_stats tests ---

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
