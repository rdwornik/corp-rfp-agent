"""Tests for kb_simulate_rfp -- Simulated RFP Test Suite."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_simulate_rfp import (
    build_question_prompt,
    parse_questions,
    generate_questions,
    answer_via_rag,
    build_scoring_prompt,
    parse_accuracy,
    build_simulation_report,
    print_simulation_report,
    save_simulation,
    simulate,
    QUESTION_TOPICS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_profile():
    return {
        "product": "wms",
        "display_name": "Blue Yonder WMS",
        "cloud_native": False,
        "deployment": ["azure"],
        "apis": ["rest", "sftp"],
        "microservices": False,
        "uses_snowflake": False,
        "forbidden_claims": [
            "Does NOT use Snowflake",
            "NOT cloud-native",
        ],
        "_meta": {"status": "active"},
    }


# ===================================================================
# Question generation tests
# ===================================================================

class TestGenerateQuestions:

    def test_generate_questions_returns_count(self, sample_profile):
        raw_response = json.dumps([
            {"question": "How is WMS deployed?", "topic": "deployment", "category": "technical"},
            {"question": "What APIs does WMS support?", "topic": "integration", "category": "technical"},
            {"question": "How does WMS handle authentication?", "topic": "security", "category": "technical"},
        ])

        with patch("kb_extract_historical.call_llm", return_value=raw_response):
            questions = generate_questions(sample_profile, 3)

        assert len(questions) == 3

    def test_questions_cover_multiple_topics(self):
        questions = [
            {"question": "Q1?", "topic": "deployment", "category": "technical"},
            {"question": "Q2?", "topic": "security", "category": "technical"},
            {"question": "Q3?", "topic": "integration", "category": "functional"},
        ]
        topics = {q["topic"] for q in questions}
        assert len(topics) >= 2

    def test_questions_have_required_fields(self):
        raw = json.dumps([
            {"question": "Test Q?", "topic": "deployment", "category": "technical"},
        ])
        parsed = parse_questions(raw)
        assert len(parsed) == 1
        assert "question" in parsed[0]
        assert "topic" in parsed[0]

    def test_parse_questions_with_fences(self):
        raw = '```json\n[{"question": "Q?", "topic": "security"}]\n```'
        parsed = parse_questions(raw)
        assert len(parsed) == 1

    def test_parse_questions_invalid_returns_empty(self):
        assert parse_questions("not json") == []
        assert parse_questions("") == []


# ===================================================================
# RAG answer tests
# ===================================================================

class TestRAGAnswer:

    def test_rag_returns_answer(self, tmp_path):
        """Mock ChromaDB to return a result."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "ids": [["wms_0001"]],
            "distances": [[0.3]],
            "metadatas": [[{
                "kb_id": "wms_0001",
                "canonical_answer": "Blue Yonder WMS supports REST APIs.",
                "canonical_question": "What APIs?",
                "domain": "wms",
            }]],
        }

        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_collection

        mock_chromadb = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()

        with patch.dict("sys.modules", {
            "chromadb": mock_chromadb,
            "chromadb.utils": MagicMock(),
            "chromadb.utils.embedding_functions": MagicMock(),
        }):
            result = answer_via_rag("What APIs?", "wms", chroma_path=chroma_dir)

        assert result["answered"] is True
        assert "REST APIs" in result["answer"]
        assert len(result["entry_ids"]) >= 1

    def test_rag_empty_when_no_match(self, tmp_path):
        """No chroma_store directory = unanswered."""
        result = answer_via_rag("Random question?", "wms",
                                chroma_path=tmp_path / "nonexistent")
        assert result["answered"] is False
        assert result["answer"] == ""

    def test_rag_filters_by_family(self, tmp_path):
        """Check that family filter is passed to query."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "ids": [[]], "distances": [[]], "metadatas": [[]],
        }
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_collection

        mock_chromadb = MagicMock()
        mock_chromadb.PersistentClient.return_value = mock_client

        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()

        with patch.dict("sys.modules", {
            "chromadb": mock_chromadb,
            "chromadb.utils": MagicMock(),
            "chromadb.utils.embedding_functions": MagicMock(),
        }):
            answer_via_rag("Q?", "wms", chroma_path=chroma_dir)

        call_kwargs = mock_collection.query.call_args
        assert call_kwargs.kwargs.get("where") == {"domain": "wms"}


# ===================================================================
# Scoring tests
# ===================================================================

class TestScoring:

    def test_parse_accuracy_valid(self):
        raw = json.dumps({"accuracy": 4, "issues": ["minor"]})
        result = parse_accuracy(raw)
        assert result["accuracy"] == 4

    def test_parse_accuracy_invalid(self):
        result = parse_accuracy("not json")
        assert result["accuracy"] == 0


# ===================================================================
# Report tests
# ===================================================================

class TestReport:

    def test_simulation_results_saved(self, tmp_path):
        report = {
            "family": "wms",
            "timestamp": "2026-03-12T10:00:00",
            "question_count": 10,
            "answered": 8,
            "unanswered": 2,
            "avg_accuracy": 3.8,
            "coverage": {},
            "details": [],
        }
        path = save_simulation(report, tmp_path)
        assert path.exists()
        data = json.load(open(path, encoding="utf-8"))
        assert data["family"] == "wms"

    def test_report_shows_coverage_by_topic(self, capsys):
        report = {
            "family": "wms",
            "question_count": 10,
            "answered": 8,
            "unanswered": 2,
            "avg_accuracy": 3.8,
            "coverage": {
                "deployment": {"total": 2, "answered": 2, "pct": 100, "avg_accuracy": 4.0},
                "security": {"total": 2, "answered": 1, "pct": 50, "avg_accuracy": 3.0},
            },
        }
        print_simulation_report(report)
        output = capsys.readouterr().out
        assert "deployment" in output
        assert "security" in output
        assert "wms" in output


# ===================================================================
# Dry run test
# ===================================================================

class TestDryRun:

    def test_simulate_dry_run_no_llm_calls(self, tmp_path, sample_profile):
        import yaml

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        with patch("kb_simulate_rfp.generate_questions") as mock_gen:
            result = simulate("wms", count=10, dry_run=True,
                              profiles_dir=profiles_dir,
                              simulations_dir=tmp_path / "sims")

        mock_gen.assert_not_called()
        assert result.get("dry_run") is True
