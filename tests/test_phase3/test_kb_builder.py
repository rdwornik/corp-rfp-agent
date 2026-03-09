"""Tests for KBBuilder."""

import json
import pytest
from pathlib import Path

from corp_rfp_agent.pipelines.kb_builder import KBBuilder
from corp_rfp_agent.kb.entry import KBEntry


def _write_canonical(canonical_dir, filename, entries):
    """Helper to write a canonical JSON file."""
    canonical_dir.mkdir(parents=True, exist_ok=True)
    path = canonical_dir / filename
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_merge_unified_combines_families(tmp_path):
    """merge_unified combines all family canonicals."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
    ])
    _write_canonical(tmp_path, "RFP_Database_AIML_CANONICAL.json", [
        {"id": "AIM-001", "question": "Q2?", "answer": "A2.", "family_code": "aiml"},
    ])

    builder = KBBuilder(tmp_path)
    total = builder.merge_unified()
    assert total == 2

    unified = tmp_path / "RFP_Database_UNIFIED_CANONICAL.json"
    assert unified.exists()
    with open(unified, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2


def test_merge_unified_deduplicates_by_id(tmp_path):
    """merge_unified deduplicates by entry ID."""
    entry = {"id": "WMS-001", "question": "Q?", "answer": "A.", "family_code": "wms"}
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [entry])
    # Intentionally place same ID in another file (shouldn't happen, but dedup)
    _write_canonical(tmp_path, "RFP_Database_Network_CANONICAL.json", [entry])

    builder = KBBuilder(tmp_path)
    total = builder.merge_unified()
    assert total == 1


def test_merge_unified_skips_existing_unified(tmp_path):
    """merge_unified ignores existing UNIFIED file."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q?", "answer": "A.", "family_code": "wms"},
    ])
    # Pre-existing unified with stale data
    _write_canonical(tmp_path, "RFP_Database_UNIFIED_CANONICAL.json", [
        {"id": "OLD-001", "question": "Old?", "answer": "Old.", "family_code": "old"},
    ])

    builder = KBBuilder(tmp_path)
    total = builder.merge_unified()
    assert total == 1  # Only from WMS, not from old UNIFIED


def test_append_to_family_adds_new(tmp_path):
    """append_to_family adds new entries."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
    ])

    builder = KBBuilder(tmp_path)
    new_entry = KBEntry(id="WMS-002", question="Q2?", answer="A2.", family_code="wms")
    added = builder.append_to_family("wms", [new_entry])
    assert added == 1

    # Verify file was updated
    path = tmp_path / "RFP_Database_WMS_CANONICAL.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2


def test_append_to_family_skips_duplicates(tmp_path):
    """append_to_family skips entries with existing IDs."""
    _write_canonical(tmp_path, "RFP_Database_WMS_CANONICAL.json", [
        {"id": "WMS-001", "question": "Q1?", "answer": "A1.", "family_code": "wms"},
    ])

    builder = KBBuilder(tmp_path)
    dup = KBEntry(id="WMS-001", question="Duplicate?", answer="Dup.", family_code="wms")
    added = builder.append_to_family("wms", [dup])
    assert added == 0


def test_append_creates_file_if_missing(tmp_path):
    """append_to_family creates canonical file if it doesn't exist."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    builder = KBBuilder(tmp_path)
    new_entry = KBEntry(id="WMS-001", question="Q?", answer="A.", family_code="wms")
    added = builder.append_to_family("wms", [new_entry])
    assert added == 1

    path = tmp_path / "RFP_Database_WMS_CANONICAL.json"
    assert path.exists()
