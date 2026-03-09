"""Tests for ArchiveRegistry."""

import json
import pytest
from pathlib import Path

from corp_rfp_agent.pipelines.archive.registry import ArchiveRegistry, ArchiveEntry


def test_add_auto_id(tmp_path):
    """add creates new entry with auto-ID."""
    registry = ArchiveRegistry(tmp_path)
    entry = ArchiveEntry(client="Acme Corp", family_code="wms")
    assigned_id = registry.add(entry)
    assert assigned_id == "ARC-0001"
    assert registry.count == 1


def test_save_load_roundtrip(tmp_path):
    """save/load roundtrip preserves entries."""
    registry = ArchiveRegistry(tmp_path)
    registry.add(ArchiveEntry(
        client="Acme",
        family_code="wms",
        rfp_type="response",
        extraction_stats={"total_qa_extracted": 42, "accepted": 40},
    ))
    registry.add(ArchiveEntry(
        client="BigCo",
        family_code="planning",
    ))

    # Reload from disk
    registry2 = ArchiveRegistry(tmp_path)
    assert registry2.count == 2
    assert registry2.entries[0].client == "Acme"
    assert registry2.entries[0].extraction_stats["total_qa_extracted"] == 42
    assert registry2.entries[1].client == "BigCo"


def test_search_by_client(tmp_path):
    """search by client finds matches (case-insensitive)."""
    registry = ArchiveRegistry(tmp_path)
    registry.add(ArchiveEntry(client="Acme Corp", family_code="wms"))
    registry.add(ArchiveEntry(client="BigRetailer", family_code="planning"))

    results = registry.search(client="acme")
    assert len(results) == 1
    assert results[0].client == "Acme Corp"


def test_search_by_family(tmp_path):
    """search by family filters correctly."""
    registry = ArchiveRegistry(tmp_path)
    registry.add(ArchiveEntry(client="A", family_code="wms"))
    registry.add(ArchiveEntry(client="B", family_code="planning"))
    registry.add(ArchiveEntry(client="C", family_code="wms"))

    results = registry.search(family="wms")
    assert len(results) == 2


def test_search_by_date_range(tmp_path):
    """search by date range works."""
    registry = ArchiveRegistry(tmp_path)
    registry.add(ArchiveEntry(client="A", date_estimated="2023-Q1"))
    registry.add(ArchiveEntry(client="B", date_estimated="2024-Q2"))
    registry.add(ArchiveEntry(client="C", date_estimated="2025-Q1"))

    results = registry.search(date_from="2024-Q1", date_to="2024-Q4")
    assert len(results) == 1
    assert results[0].client == "B"


def test_empty_registry(tmp_path):
    """Empty registry returns empty results."""
    registry = ArchiveRegistry(tmp_path)
    assert registry.count == 0
    assert registry.search(client="anything") == []
    assert registry.entries == []


def test_get_by_id(tmp_path):
    """get_by_id finds specific entry."""
    registry = ArchiveRegistry(tmp_path)
    registry.add(ArchiveEntry(client="A", family_code="wms"))
    registry.add(ArchiveEntry(client="B", family_code="planning"))

    found = registry.get_by_id("ARC-0001")
    assert found is not None
    assert found.client == "A"

    assert registry.get_by_id("ARC-9999") is None


def test_next_id_increments(tmp_path):
    """next_id generates sequential IDs."""
    registry = ArchiveRegistry(tmp_path)
    assert registry.next_id() == "ARC-0001"
    registry.add(ArchiveEntry(client="A"))
    assert registry.next_id() == "ARC-0002"
