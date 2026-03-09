"""Tests for ChromaKBClient with real (temp) ChromaDB instance.

These tests create ephemeral ChromaDB instances in tmp_path.
They require chromadb and sentence-transformers to be installed.
"""

import json
import pytest
from pathlib import Path

from corp_rfp_agent.kb.entry import KBEntry

# Skip all tests if chromadb not available
chromadb = pytest.importorskip("chromadb")


@pytest.fixture
def chroma_client(tmp_path):
    """Create a ChromaKBClient with temp ChromaDB and test data."""
    from corp_rfp_agent.kb.chromadb_impl import ChromaKBClient

    chroma_path = str(tmp_path / "chroma")

    # Create test entries
    entries = [
        KBEntry(id="WMS-001", question="Does BY WMS support pick-by-voice?",
                answer="Yes, BY WMS supports pick-by-voice with Android devices.",
                family_code="wms", category="functional"),
        KBEntry(id="WMS-002", question="What barcode scanners are supported?",
                answer="BY WMS supports Zebra, Honeywell, and Datalogic scanners.",
                family_code="wms", category="technical"),
        KBEntry(id="PLN-001", question="How does demand sensing work?",
                answer="Demand sensing uses ML models to predict short-term demand.",
                family_code="planning", category="functional"),
    ]

    # Write KB JSON for lookup
    kb_json = tmp_path / "kb.json"
    kb_data = []
    for e in entries:
        kb_data.append({
            "id": e.id,
            "kb_id": e.id,
            "domain": e.family_code,
            "category": e.category,
            "canonical_question": e.question,
            "canonical_answer": e.answer,
        })
    kb_json.write_text(json.dumps(kb_data), encoding="utf-8")

    client = ChromaKBClient(
        chroma_path=chroma_path,
        kb_json_path=kb_json,
        create_if_missing=True,
    )

    # Upsert entries
    client.upsert(entries)

    return client


def test_upsert_returns_count(chroma_client):
    """upsert adds entries and returns count."""
    new_entries = [
        KBEntry(id="NET-001", question="What networking features exist?",
                answer="Network optimization and routing.",
                family_code="network", category="functional"),
    ]
    count = chroma_client.upsert(new_entries)
    assert count == 1


def test_query_returns_matches(chroma_client):
    """query returns matches sorted by similarity."""
    matches = chroma_client.query(
        "pick by voice warehouse",
        threshold=0.0,  # Low threshold to ensure results
        top_k=3,
    )
    assert len(matches) > 0
    # First result should be about pick-by-voice
    assert "WMS-001" in matches[0].entry_id or "voice" in matches[0].answer.lower()


def test_query_filters_by_threshold(chroma_client):
    """query filters by threshold -- very high threshold returns fewer results."""
    all_matches = chroma_client.query("pick by voice", threshold=0.0, top_k=10)
    strict_matches = chroma_client.query("pick by voice", threshold=0.99, top_k=10)
    assert len(strict_matches) <= len(all_matches)


def test_count_returns_total(chroma_client):
    """count returns correct total."""
    assert chroma_client.count() == 3


def test_rebuild_clears_and_reindexes(chroma_client):
    """rebuild clears and re-indexes."""
    new_entries = [
        KBEntry(id="NEW-001", question="New question?", answer="New answer.",
                family_code="wms", category="functional"),
    ]
    count = chroma_client.rebuild(new_entries)
    assert count == 1
    assert chroma_client.count() == 1
