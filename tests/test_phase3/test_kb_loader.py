"""Tests for KBLoader."""

import json
import pytest
from pathlib import Path

from corp_rfp_agent.pipelines.kb_loader import KBLoader


def _write_canonical(canonical_dir, filename, entries):
    """Helper to write a canonical JSON file."""
    canonical_dir.mkdir(parents=True, exist_ok=True)
    path = canonical_dir / filename
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_load_all_finds_canonical_json(tmp_path):
    """load_all finds and loads canonical JSON files."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
        {"id": "WMS-002", "question": "Q2?", "answer": "A2.", "family_code": "wms"},
    ])
    _write_canonical(tmp_path, "RFP_Database_AIML_CANONICAL.json", [
        {"id": "AIM-001", "question": "Q3?", "answer": "A3.", "family_code": "aiml"},
    ])

    loader = KBLoader(tmp_path)
    entries = loader.load_all()
    assert len(entries) == 3


def test_load_all_skips_unified(tmp_path):
    """load_all skips UNIFIED canonical to avoid double-counting."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
    ])
    _write_canonical(tmp_path, "RFP_Database_UNIFIED_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
    ])

    loader = KBLoader(tmp_path)
    entries = loader.load_all()
    assert len(entries) == 1


def test_load_family_loads_specific(tmp_path):
    """load_family loads only specified family."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
    ])
    _write_canonical(tmp_path, "RFP_Database_AIML_CANONICAL.json", [
        {"id": "AIM-001", "question": "Q3?", "answer": "A3.", "family_code": "aiml"},
    ])

    loader = KBLoader(tmp_path)
    entries = loader.load_family("wms")
    assert len(entries) == 1
    assert entries[0].id == "WMS-001"


def test_load_family_unknown_returns_empty(tmp_path):
    """load_family returns empty list for unknown family."""
    loader = KBLoader(tmp_path)
    entries = loader.load_family("nonexistent")
    assert entries == []


def test_load_family_missing_file_returns_empty(tmp_path):
    """load_family returns empty if canonical file doesn't exist."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    loader = KBLoader(tmp_path)
    entries = loader.load_family("wms")
    assert entries == []


def test_stats_returns_counts(tmp_path):
    """stats returns correct counts per family."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
        {"id": "WMS-002", "question": "Q2?", "answer": "A2.", "family_code": "wms"},
    ])
    _write_canonical(tmp_path, "RFP_Database_AIML_CANONICAL.json", [
        {"id": "AIM-001", "question": "Q3?", "answer": "A3.", "family_code": "aiml"},
    ])

    loader = KBLoader(tmp_path)
    stats = loader.stats()
    assert stats["WMS"] == 2
    assert stats["AIML"] == 1


def test_handles_empty_canonical_dir(tmp_path):
    """Gracefully handles empty canonical directory."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    loader = KBLoader(tmp_path)
    assert loader.load_all() == []
    assert loader.stats() == {}
