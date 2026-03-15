"""Tests for vault_adapter — corp retrieve wrapper."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import vault_adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cli_output(notes: list[dict], query: str = "test") -> str:
    """Build JSON string matching corp retrieve --format json output."""
    return json.dumps({
        "query": query,
        "total_found": len(notes),
        "sufficient": len(notes) >= 3,
        "coverage_gaps": [],
        "notes": notes,
    })


def _sample_note(**overrides) -> dict:
    """Return a realistic note dict."""
    base = {
        "note_id": 42,
        "title": "WMS Integration Architecture",
        "content": "Blue Yonder WMS supports REST APIs for warehouse integration.",
        "products": ["WMS"],
        "topics": ["Integration", "API"],
        "domains": ["logistics"],
        "confidence": "verified",
        "relevance_score": 0.85,
        "source_path": "C:/vault/notes/wms_042.md",
        "citation": "WMS Architecture Workshop 2025",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1: retrieve parses JSON from CLI
# ---------------------------------------------------------------------------
def test_retrieve_parses_json():
    """retrieve() parses corp CLI JSON output into note dicts."""
    notes = [_sample_note(), _sample_note(note_id=43, title="Second note")]
    stdout = _make_cli_output(notes, query="warehouse API")

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = stdout
    fake_result.stderr = ""

    with patch("vault_adapter.subprocess.run", return_value=fake_result) as mock_run:
        result = vault_adapter.retrieve("warehouse API", limit=5)

    # Verify CLI was called correctly
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "corp"
    assert cmd[1] == "retrieve"
    assert "warehouse API" in cmd
    assert "--format" in cmd
    assert "json" in cmd
    assert "--top" in cmd
    assert "5" in cmd

    # Verify parsed results
    assert len(result) == 2
    assert result[0]["note_id"] == 42
    assert result[0]["title"] == "WMS Integration Architecture"
    assert result[1]["note_id"] == 43


# ---------------------------------------------------------------------------
# Test 2: retrieve returns empty on no results
# ---------------------------------------------------------------------------
def test_retrieve_empty_results():
    """retrieve() returns empty list when corp CLI finds nothing."""
    stdout = _make_cli_output([], query="nonexistent topic")

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = stdout
    fake_result.stderr = ""

    with patch("vault_adapter.subprocess.run", return_value=fake_result):
        result = vault_adapter.retrieve("nonexistent topic")

    assert result == []


# ---------------------------------------------------------------------------
# Test 3: retrieve passes product filter
# ---------------------------------------------------------------------------
def test_retrieve_filters_by_product():
    """retrieve() passes --product flags to corp CLI."""
    stdout = _make_cli_output([_sample_note()])

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = stdout
    fake_result.stderr = ""

    with patch("vault_adapter.subprocess.run", return_value=fake_result) as mock_run:
        vault_adapter.retrieve("API integration", products=["wms", "planning"])

    cmd = mock_run.call_args[0][0]
    # Should have --product wms --product planning
    product_indices = [i for i, c in enumerate(cmd) if c == "--product"]
    assert len(product_indices) == 2
    product_values = [cmd[i + 1] for i in product_indices]
    assert "wms" in product_values
    assert "planning" in product_values


# ---------------------------------------------------------------------------
# Test 4: retrieve_for_rfp returns best match
# ---------------------------------------------------------------------------
def test_retrieve_for_rfp_returns_best():
    """retrieve_for_rfp() returns answer from top-ranked note."""
    notes = [
        _sample_note(relevance_score=0.9, content="Top answer about WMS."),
        _sample_note(note_id=43, relevance_score=0.6, content="Secondary."),
        _sample_note(note_id=44, relevance_score=0.4, content="Tertiary."),
    ]

    with patch("vault_adapter.retrieve", return_value=notes):
        result = vault_adapter.retrieve_for_rfp("How does WMS handle APIs?", family="wms")

    assert result["status"] == "OK"
    assert result["answer"] == "Top answer about WMS."
    assert result["confidence"] == 0.9
    assert len(result["sources"]) == 3
    assert result["sources"][0] == 42


# ---------------------------------------------------------------------------
# Test 5: retrieve_for_rfp with no data
# ---------------------------------------------------------------------------
def test_retrieve_for_rfp_no_data():
    """retrieve_for_rfp() returns NO_DATA when no notes found."""
    with patch("vault_adapter.retrieve", return_value=[]):
        result = vault_adapter.retrieve_for_rfp("Something obscure")

    assert result["status"] == "NO_DATA"
    assert result["answer"] == ""
    assert result["confidence"] == 0.0
    assert result["sources"] == []


# ---------------------------------------------------------------------------
# Test 6: retrieve_for_rfp with low confidence
# ---------------------------------------------------------------------------
def test_retrieve_for_rfp_low_confidence():
    """retrieve_for_rfp() returns LOW_CONFIDENCE when best score is below threshold."""
    notes = [_sample_note(relevance_score=0.1, content="Weak match.")]

    with patch("vault_adapter.retrieve", return_value=notes):
        result = vault_adapter.retrieve_for_rfp("Vague question")

    assert result["status"] == "LOW_CONFIDENCE"
    assert result["answer"] == "Weak match."
    assert result["confidence"] == 0.1


# ---------------------------------------------------------------------------
# Test 7: fallback to direct SQLite when CLI unavailable
# ---------------------------------------------------------------------------
def test_fallback_when_cli_unavailable():
    """When corp CLI is not found, retrieve() falls back to SQLite."""
    # Simulate FileNotFoundError from subprocess (CLI not installed)
    with patch("vault_adapter.subprocess.run", side_effect=FileNotFoundError("corp not found")), \
         patch("vault_adapter._retrieve_via_sqlite", return_value=[_sample_note()]) as mock_sql:
        result = vault_adapter.retrieve("test query")

    mock_sql.assert_called_once()
    assert len(result) == 1
    assert result[0]["note_id"] == 42


# ---------------------------------------------------------------------------
# Test 8: direct SQLite retrieval
# ---------------------------------------------------------------------------
def test_fallback_direct_sqlite(tmp_path):
    """_retrieve_via_sqlite() queries FTS5 and returns parsed notes."""
    # Create a minimal SQLite DB with FTS5
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            topics TEXT,
            products TEXT,
            domains TEXT,
            confidence TEXT,
            note_path TEXT NOT NULL,
            project_id TEXT
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE notes_fts USING fts5(
            title, topics, products, domains, client, project_id,
            content=notes, content_rowid=id
        )
    """)

    # Insert a test note
    conn.execute(
        "INSERT INTO notes (id, title, topics, products, domains, confidence, note_path, project_id) "
        "VALUES (1, 'WMS REST API Integration', '[\"Integration\"]', '[\"WMS\"]', '[\"logistics\"]', "
        "'verified', ?, 'PRJ-001')",
        [str(tmp_path / "note.md")],
    )
    # Manually populate FTS (normally done by triggers)
    conn.execute(
        "INSERT INTO notes_fts (rowid, title, topics, products, domains, client, project_id) "
        "VALUES (1, 'WMS REST API Integration', '[\"Integration\"]', '[\"WMS\"]', '[\"logistics\"]', "
        "'Acme', 'PRJ-001')"
    )
    conn.commit()
    conn.close()

    # Write a note file
    (tmp_path / "note.md").write_text("WMS supports REST APIs for integration.", encoding="utf-8")

    # Patch _find_index_db to use our temp DB
    with patch("vault_adapter._find_index_db", return_value=db_path):
        result = vault_adapter._retrieve_via_sqlite("REST API", limit=5)

    assert len(result) == 1
    assert result[0]["note_id"] == 1
    assert result[0]["title"] == "WMS REST API Integration"
    assert result[0]["content"] == "WMS supports REST APIs for integration."
    assert result[0]["products"] == ["WMS"]
    assert result[0]["confidence"] == "verified"
    assert result[0]["relevance_score"] > 0


# ---------------------------------------------------------------------------
# Test 9: corp CLI error returns empty
# ---------------------------------------------------------------------------
def test_corp_cli_error_returns_empty():
    """When corp CLI returns non-zero exit code, retrieve() returns empty."""
    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stdout = ""
    fake_result.stderr = "Error: index not found"

    with patch("vault_adapter.subprocess.run", return_value=fake_result):
        result = vault_adapter._retrieve_via_cli("test query")

    assert result == []


# Need sqlite3 for the FTS5 test
import sqlite3
