"""Tests for EntryAdapter -- legacy format conversion."""

import json
import pytest
from pathlib import Path

from corp_rfp_agent.domain.entry_adapter import EntryAdapter
from corp_rfp_agent.kb.entry import KBEntry


def test_from_legacy_jsonl():
    """Converts v1 {question, answer, source} correctly."""
    data = {"question": "What is WMS?", "answer": "Warehouse Management.", "source": "workshop"}
    entry = EntryAdapter.from_legacy_jsonl(data)
    assert entry.question == "What is WMS?"
    assert entry.answer == "Warehouse Management."
    assert entry.source == "workshop"
    assert entry.source_type == "legacy"


def test_from_v2_dict():
    """Converts v2 with all fields."""
    data = {
        "id": "WMS-FUNC-0001",
        "question": "Does BY support pick-by-voice?",
        "answer": "Yes, BY WMS supports pick-by-voice.",
        "family_code": "wms",
        "category": "functional",
        "subcategory": "picking",
        "tags": ["voice", "picking"],
        "confidence": "verified",
        "solution_codes": ["wms", "wms_native"],
    }
    entry = EntryAdapter.from_v2_dict(data)
    assert entry.id == "WMS-FUNC-0001"
    assert entry.question == "Does BY support pick-by-voice?"
    assert entry.family_code == "wms"
    assert entry.category == "functional"
    assert entry.tags == ["voice", "picking"]
    assert entry.confidence == "verified"
    assert entry.source_type == "extracted"


def test_from_v2_dict_canonical_aliases():
    """Handles canonical_question/canonical_answer field aliases."""
    data = {
        "kb_id": "kb_0042",
        "canonical_question": "How does demand sensing work?",
        "canonical_answer": "It uses ML models.",
        "domain": "planning",
        "category": "technical",
    }
    entry = EntryAdapter.from_v2_dict(data)
    assert entry.question == "How does demand sensing work?"
    assert entry.answer == "It uses ML models."
    assert entry.id == "kb_0042"
    assert entry.family_code == "planning"


def test_to_dict_roundtrip():
    """from_v2_dict -> to_dict -> from_v2_dict preserves data."""
    original = {
        "id": "NET-TECH-0003",
        "question": "What protocols are supported?",
        "answer": "REST, GraphQL, gRPC.",
        "family_code": "network",
        "category": "technical",
        "tags": ["api", "protocols"],
        "confidence": "verified",
    }
    entry = EntryAdapter.from_v2_dict(original)
    exported = EntryAdapter.to_dict(entry)
    entry2 = EntryAdapter.from_v2_dict(exported)

    assert entry2.id == entry.id
    assert entry2.question == entry.question
    assert entry2.answer == entry.answer
    assert entry2.family_code == entry.family_code
    assert entry2.tags == entry.tags


def test_load_jsonl_mixed_formats(tmp_path):
    """load_jsonl handles mixed v1/v2 entries in same file."""
    lines = [
        json.dumps({"question": "v1 question", "answer": "v1 answer", "source": "test"}),
        json.dumps({"id": "X-001", "question": "v2 question", "answer": "v2 answer", "family_code": "wms"}),
    ]
    path = tmp_path / "mixed.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    entries = EntryAdapter.load_jsonl(path)
    assert len(entries) == 2
    assert entries[0].source_type == "legacy"
    assert entries[1].source_type == "extracted"


def test_load_jsonl_skips_invalid(tmp_path):
    """load_jsonl skips entries with empty question/answer."""
    lines = [
        json.dumps({"question": "Valid?", "answer": "Yes."}),
        json.dumps({"question": "", "answer": "No question."}),
        json.dumps({"question": "No answer.", "answer": ""}),
        "not valid json at all",
    ]
    path = tmp_path / "invalid.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    entries = EntryAdapter.load_jsonl(path)
    assert len(entries) == 1
    assert entries[0].question == "Valid?"


def test_load_jsonl_handles_blank_lines(tmp_path):
    """load_jsonl ignores blank lines."""
    lines = [
        json.dumps({"question": "Q1?", "answer": "A1."}),
        "",
        "  ",
        json.dumps({"question": "Q2?", "answer": "A2."}),
    ]
    path = tmp_path / "blanks.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    entries = EntryAdapter.load_jsonl(path)
    assert len(entries) == 2


def test_load_json(tmp_path):
    """load_json loads from JSON array."""
    data = [
        {"question": "Q1?", "answer": "A1.", "family_code": "wms"},
        {"question": "Q2?", "answer": "A2."},
    ]
    path = tmp_path / "entries.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    entries = EntryAdapter.load_json(path)
    assert len(entries) == 2
