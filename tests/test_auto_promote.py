"""Tests for auto-promote and record_usage in rfp_feedback."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rfp_feedback import (
    check_promotion_eligibility,
    cmd_auto_promote,
    record_usage,
    load_entry,
    save_entry,
    find_entry_dir,
    COOLING_PERIOD_DAYS,
    MIN_USAGE_COUNT,
    MIN_QUALITY_AVERAGE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_eligible_draft(**overrides):
    """Build a draft entry that meets ALL 6 promotion criteria."""
    old_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
    base = {
        "id": "KB_DRAFT_0001",
        "question": "What APIs does WMS support?",
        "answer": "Blue Yonder WMS provides REST APIs for integration with third-party systems.",
        "family_code": "wms",
        "category": "technical",
        "confidence": "draft",
        "usage_count": 5,
        "feedback_history": [],
        "generated_at": old_date,
        "_quality": {
            "accuracy": 5, "specificity": 4, "tone": 5,
            "completeness": 4, "self_contained": 5,
            "average": 4.6, "verdict": "EXCELLENT",
            "issues": [],
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def empty_profile():
    return {
        "product": "wms",
        "display_name": "Blue Yonder WMS",
        "forbidden_claims": [],
    }


@pytest.fixture
def drafts_setup(tmp_path):
    """Create tmp drafts dir with one eligible entry."""
    drafts = tmp_path / "drafts" / "wms"
    drafts.mkdir(parents=True)
    entry = _make_eligible_draft()
    path = drafts / "KB_DRAFT_0001.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f)

    verified = tmp_path / "verified" / "wms"
    verified.mkdir(parents=True)

    return {
        "drafts_dir": tmp_path / "drafts",
        "verified_dir": tmp_path / "verified",
        "path": path,
        "entry": entry,
    }


# ===================================================================
# Eligibility checks
# ===================================================================

class TestPromotionEligibility:

    def test_eligible_when_all_criteria_met(self, empty_profile):
        entry = _make_eligible_draft()
        ok, reasons = check_promotion_eligibility(entry, empty_profile)
        assert ok is True
        assert len(reasons) == 0

    def test_not_eligible_low_usage(self, empty_profile):
        entry = _make_eligible_draft(usage_count=1)
        ok, reasons = check_promotion_eligibility(entry, empty_profile)
        assert ok is False
        assert any("usage_count" in r for r in reasons)

    def test_not_eligible_has_corrections(self, empty_profile):
        entry = _make_eligible_draft(feedback_history=[
            {"action": "corrected", "timestamp": "2026-03-01"},
        ])
        ok, reasons = check_promotion_eligibility(entry, empty_profile)
        assert ok is False
        assert any("correction" in r for r in reasons)

    def test_not_eligible_forbidden_violation(self):
        profile = {
            "product": "wms",
            "forbidden_claims": ["Does NOT use Snowflake"],
        }
        entry = _make_eligible_draft(
            answer="WMS uses Snowflake for data warehousing.",
        )
        ok, reasons = check_promotion_eligibility(entry, profile)
        assert ok is False
        assert any("forbidden" in r for r in reasons)

    def test_not_eligible_low_quality(self, empty_profile):
        entry = _make_eligible_draft(_quality={
            "accuracy": 2, "specificity": 2, "tone": 2,
            "completeness": 2, "self_contained": 2,
            "average": 2.0, "verdict": "POOR", "issues": [],
        })
        ok, reasons = check_promotion_eligibility(entry, empty_profile)
        assert ok is False
        assert any("quality" in r for r in reasons)

    def test_not_eligible_cooling_period(self, empty_profile):
        recent = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        entry = _make_eligible_draft(generated_at=recent)
        ok, reasons = check_promotion_eligibility(entry, empty_profile)
        assert ok is False
        assert any("age=" in r for r in reasons)

    def test_not_eligible_already_verified(self, empty_profile):
        entry = _make_eligible_draft(confidence="verified")
        ok, reasons = check_promotion_eligibility(entry, empty_profile)
        assert ok is False
        assert any("not a draft" in r for r in reasons)


# ===================================================================
# Promotion action tests
# ===================================================================

class TestPromoteAction:

    def test_promote_moves_to_verified(self, drafts_setup, tmp_path):
        setup = drafts_setup

        with patch("rfp_feedback.DRAFTS_DIR", setup["drafts_dir"]), \
             patch("rfp_feedback.VERIFIED_DIR", setup["verified_dir"]), \
             patch("rfp_feedback.PROFILES_DIR", tmp_path / "profiles"), \
             patch("rfp_feedback.load_profile", return_value={"forbidden_claims": []}), \
             patch("rfp_feedback.append_feedback_log", return_value="FB_001"), \
             patch("rfp_feedback.FEEDBACK_LOG", tmp_path / "fb.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", tmp_path / ".fb"):

            result = cmd_auto_promote(dry_run=False)

        assert result == 0
        # Draft should be gone
        assert not setup["path"].exists()
        # Should be in verified
        verified_path = setup["verified_dir"] / "wms" / "KB_DRAFT_0001.json"
        assert verified_path.exists()
        data = json.load(open(verified_path, encoding="utf-8"))
        assert data["confidence"] == "verified"

    def test_promote_logs_feedback(self, drafts_setup, tmp_path):
        setup = drafts_setup

        fb_log = tmp_path / "fb.jsonl"

        with patch("rfp_feedback.DRAFTS_DIR", setup["drafts_dir"]), \
             patch("rfp_feedback.VERIFIED_DIR", setup["verified_dir"]), \
             patch("rfp_feedback.PROFILES_DIR", tmp_path / "profiles"), \
             patch("rfp_feedback.load_profile", return_value={"forbidden_claims": []}), \
             patch("rfp_feedback.FEEDBACK_LOG", fb_log), \
             patch("rfp_feedback._FB_COUNTER_PATH", tmp_path / ".fb"):

            cmd_auto_promote(dry_run=False)

        assert fb_log.exists()
        lines = fb_log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        log_entry = json.loads(lines[0])
        assert log_entry["action"] == "auto_promote"

    def test_dry_run_shows_eligible(self, drafts_setup, tmp_path, capsys):
        setup = drafts_setup

        with patch("rfp_feedback.DRAFTS_DIR", setup["drafts_dir"]), \
             patch("rfp_feedback.VERIFIED_DIR", setup["verified_dir"]), \
             patch("rfp_feedback.PROFILES_DIR", tmp_path / "profiles"), \
             patch("rfp_feedback.load_profile", return_value={"forbidden_claims": []}):

            result = cmd_auto_promote(dry_run=True)

        assert result == 0
        output = capsys.readouterr().out
        assert "KB_DRAFT_0001" in output
        assert "DRY RUN" in output
        # Draft should still be there
        assert setup["path"].exists()


# ===================================================================
# Usage tracking tests
# ===================================================================

class TestRecordUsage:

    def test_record_usage_increments(self, tmp_path):
        drafts = tmp_path / "drafts" / "wms"
        drafts.mkdir(parents=True)
        entry = {"id": "KB_DRAFT_0001", "question": "Q?", "answer": "A.",
                 "family_code": "wms", "usage_count": 0}
        path = drafts / "KB_DRAFT_0001.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f)

        with patch("rfp_feedback.VERIFIED_DIR", tmp_path / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", tmp_path / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", tmp_path / "rejected"):
            result = record_usage("KB_DRAFT_0001")

        assert result is True
        data = json.load(open(path, encoding="utf-8"))
        assert data["usage_count"] == 1

    def test_record_usage_sets_last_used(self, tmp_path):
        drafts = tmp_path / "drafts" / "wms"
        drafts.mkdir(parents=True)
        entry = {"id": "KB_DRAFT_0001", "question": "Q?", "answer": "A.",
                 "family_code": "wms"}
        path = drafts / "KB_DRAFT_0001.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f)

        with patch("rfp_feedback.VERIFIED_DIR", tmp_path / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", tmp_path / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", tmp_path / "rejected"):
            record_usage("KB_DRAFT_0001")

        data = json.load(open(path, encoding="utf-8"))
        assert "last_used" in data
        # Should be today's date
        assert datetime.now().strftime("%Y-%m-%d") in data["last_used"]
