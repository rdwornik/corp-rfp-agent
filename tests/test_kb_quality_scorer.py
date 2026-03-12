"""Tests for kb_quality_scorer -- LLM Quality Scoring."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_quality_scorer import (
    build_scoring_prompt,
    parse_scores,
    zero_scores,
    save_scores,
    load_entries,
    score_entries,
    print_report,
    DIMENSIONS,
    SCORING_PROMPT,
    _normalize_scores,
    _validate_scores,
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
    }


@pytest.fixture
def sample_entry():
    return {
        "id": "KB_0001",
        "question": "What APIs does WMS support?",
        "answer": "Blue Yonder WMS provides REST APIs for integration.",
        "family_code": "wms",
        "category": "technical",
    }


# ===================================================================
# Prompt tests
# ===================================================================

class TestBuildPrompt:

    def test_build_prompt_includes_profile_and_forbidden(self, sample_entry, sample_profile):
        prompt = build_scoring_prompt(sample_entry, sample_profile)
        assert "Blue Yonder WMS" in prompt
        assert "Does NOT use Snowflake" in prompt
        assert "NOT cloud-native" in prompt
        assert "What APIs does WMS support?" in prompt
        assert "REST APIs" in prompt


# ===================================================================
# Score parsing tests
# ===================================================================

class TestParseScores:

    def test_parse_valid_scores_all_dimensions(self):
        raw = json.dumps({
            "accuracy": 4, "specificity": 3, "tone": 5,
            "completeness": 4, "self_contained": 5,
            "average": 4.2, "issues": [], "verdict": "GOOD",
        })
        scores = parse_scores(raw)
        assert scores is not None
        for d in DIMENSIONS:
            assert d in scores
        assert scores["average"] == 4.2

    def test_verdict_excellent_above_4_5(self):
        data = {"accuracy": 5, "specificity": 5, "tone": 5,
                "completeness": 4, "self_contained": 4}
        result = _normalize_scores(data)
        assert result["verdict"] == "EXCELLENT"
        assert result["average"] >= 4.5

    def test_verdict_good_above_3_5(self):
        data = {"accuracy": 4, "specificity": 3, "tone": 4,
                "completeness": 4, "self_contained": 3}
        result = _normalize_scores(data)
        assert result["verdict"] == "GOOD"
        assert result["average"] >= 3.5

    def test_verdict_needs_work_above_2_5(self):
        data = {"accuracy": 3, "specificity": 2, "tone": 3,
                "completeness": 3, "self_contained": 3}
        result = _normalize_scores(data)
        assert result["verdict"] == "NEEDS_WORK"
        assert result["average"] >= 2.5

    def test_verdict_poor_below_2_5(self):
        data = {"accuracy": 1, "specificity": 1, "tone": 2,
                "completeness": 1, "self_contained": 1}
        result = _normalize_scores(data)
        assert result["verdict"] == "POOR"
        assert result["average"] < 2.5

    def test_parse_error_returns_zero(self):
        assert parse_scores("not json at all") is None
        assert parse_scores("") is None
        z = zero_scores()
        assert z["average"] == 0.0
        assert z["verdict"] == "POOR"

    def test_parse_with_markdown_fences(self):
        raw = '```json\n{"accuracy": 4, "specificity": 3, "tone": 5, "completeness": 4, "self_contained": 5}\n```'
        scores = parse_scores(raw)
        assert scores is not None
        assert scores["accuracy"] == 4


# ===================================================================
# Save scores tests
# ===================================================================

class TestSaveScores:

    def test_save_scores_updates_entry_file(self, tmp_path, sample_entry):
        path = tmp_path / "KB_0001.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sample_entry, f)

        results = [{"entry_id": "KB_0001", "scores": {
            "accuracy": 4, "specificity": 3, "tone": 5,
            "completeness": 4, "self_contained": 5,
            "average": 4.2, "verdict": "GOOD", "issues": [],
        }}]

        updated = save_scores(results, {"KB_0001": path})
        assert updated == 1

        data = json.load(open(path, encoding="utf-8"))
        assert "_quality" in data
        assert data["_quality"]["accuracy"] == 4
        assert data["_quality"]["verdict"] == "GOOD"
        assert "scored_at" in data["_quality"]

    def test_save_scores_preserves_other_fields(self, tmp_path, sample_entry):
        path = tmp_path / "KB_0001.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sample_entry, f)

        results = [{"entry_id": "KB_0001", "scores": {
            "accuracy": 5, "specificity": 5, "tone": 5,
            "completeness": 5, "self_contained": 5,
            "average": 5.0, "verdict": "EXCELLENT", "issues": [],
        }}]

        save_scores(results, {"KB_0001": path})

        data = json.load(open(path, encoding="utf-8"))
        # Original fields preserved
        assert data["question"] == "What APIs does WMS support?"
        assert data["answer"] == "Blue Yonder WMS provides REST APIs for integration."
        assert data["family_code"] == "wms"


# ===================================================================
# Entry loading tests
# ===================================================================

class TestLoadEntries:

    def test_sample_mode_selects_percentage(self, tmp_path):
        verified = tmp_path / "verified" / "wms"
        verified.mkdir(parents=True)
        for i in range(20):
            entry = {"id": f"KB_{i:04d}", "question": f"Q{i}?",
                     "answer": f"A{i}.", "family_code": "wms"}
            with open(verified / f"KB_{i:04d}.json", "w") as f:
                json.dump(entry, f)

        entries = load_entries(scope="verified", sample_pct=10,
                               verified_dir=tmp_path / "verified",
                               drafts_dir=tmp_path / "drafts")
        # 10% of 20 = 2
        assert len(entries) == 2


# ===================================================================
# Report tests
# ===================================================================

class TestReport:

    def test_report_shows_verdicts_and_averages(self, capsys):
        results = [
            {"entry_id": "KB_0001", "scores": {
                "accuracy": 5, "specificity": 5, "tone": 5,
                "completeness": 5, "self_contained": 5,
                "average": 5.0, "verdict": "EXCELLENT", "issues": []}},
            {"entry_id": "KB_0002", "scores": {
                "accuracy": 2, "specificity": 1, "tone": 2,
                "completeness": 2, "self_contained": 1,
                "average": 1.6, "verdict": "POOR",
                "issues": ["generic answer"]}},
        ]
        print_report(results)
        output = capsys.readouterr().out
        assert "EXCELLENT" in output
        assert "POOR" in output
        assert "accuracy" in output
        assert "Entries scored: 2" in output


# ===================================================================
# Batch scoring test (mocked)
# ===================================================================

class TestBatchScoring:

    def test_batch_scoring_submits_all(self, tmp_path, sample_profile):
        import yaml

        # Setup dirs
        verified = tmp_path / "verified" / "wms"
        verified.mkdir(parents=True)
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()

        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        for i in range(3):
            entry = {"id": f"KB_{i:04d}", "question": f"Q{i}?",
                     "answer": f"Blue Yonder WMS answer {i}.",
                     "family_code": "wms"}
            with open(verified / f"KB_{i:04d}.json", "w") as f:
                json.dump(entry, f)

        mock_scores = {
            "accuracy": 4, "specificity": 3, "tone": 5,
            "completeness": 4, "self_contained": 5,
            "average": 4.2, "verdict": "GOOD", "issues": [],
        }

        with patch("kb_quality_scorer.score_sync") as mock_sync:
            mock_sync.return_value = [
                {"entry_id": f"KB_{i:04d}", "scores": mock_scores}
                for i in range(3)
            ]

            results = score_entries(
                scope="verified", family="wms",
                batch_mode=False,  # use sync mock
                verified_dir=tmp_path / "verified",
                drafts_dir=tmp_path / "drafts",
                profiles_dir=profiles_dir,
            )

        assert len(results) == 3
        mock_sync.assert_called_once()
