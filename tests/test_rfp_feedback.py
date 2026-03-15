"""Tests for simplified rfp_feedback CLI (show, correct, search)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rfp_feedback import (
    find_entry,
    find_entry_dir,
    load_entry,
    save_entry,
    check_forbidden_claims,
    _extract_check_terms,
    _is_negated,
    cmd_show,
    cmd_correct_offline,
    cmd_search,
    _text_search,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kb_dirs(tmp_path):
    """Create temp KB directory structure."""
    verified = tmp_path / "verified"
    drafts = tmp_path / "drafts"
    rejected = tmp_path / "rejected"
    for d in [verified, drafts, rejected]:
        d.mkdir()

    # Create a planning entry in verified
    (verified / "planning").mkdir()
    entry = {
        "id": "KB_0001",
        "question": "How does HA work?",
        "answer": "Active-active with 99.97% SLA.",
        "family_code": "planning",
        "category": "technical",
        "subcategory": "High Availability",
        "tags": ["ha", "sla"],
        "confidence": "verified",
        "source_rfps": [],
        "last_updated": "2025-12-30",
        "feedback_history": [],
    }
    (verified / "planning" / "KB_0001.json").write_text(
        json.dumps(entry), encoding="utf-8"
    )

    # Create a draft
    (drafts / "planning").mkdir()
    draft = {
        "id": "KB_DRAFT_0001",
        "question": "What deployment model?",
        "answer": "Cloud-native SaaS on Azure.",
        "family_code": "planning",
        "category": "technical",
        "subcategory": "",
        "tags": ["cloud"],
        "confidence": "draft",
        "source_rfps": [],
        "last_updated": "2026-03-12",
        "feedback_history": [],
    }
    (drafts / "planning" / "KB_DRAFT_0001.json").write_text(
        json.dumps(draft), encoding="utf-8"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Tests: Entry I/O
# ---------------------------------------------------------------------------

class TestEntryIO:
    def test_find_entry_verified(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"):
            path = find_entry("KB_0001")
            assert path is not None
            assert "KB_0001.json" in str(path)

    def test_find_entry_drafts(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"):
            path = find_entry("KB_DRAFT_0001")
            assert path is not None

    def test_find_entry_not_found(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"):
            path = find_entry("NONEXISTENT")
            assert path is None

    def test_find_entry_dir(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"):
            path, dir_type = find_entry_dir("KB_0001")
            assert dir_type == "verified"

    def test_load_save_roundtrip(self, tmp_path):
        entry = {"id": "TEST", "question": "Q?", "answer": "A."}
        p = tmp_path / "test.json"
        save_entry(entry, p)
        loaded = load_entry(p)
        assert loaded["id"] == "TEST"


# ---------------------------------------------------------------------------
# Tests: Forbidden claims
# ---------------------------------------------------------------------------

class TestForbiddenClaims:
    def test_no_violations_clean_answer(self):
        profile = {"forbidden_claims": ["Does NOT use Snowflake"]}
        violations = check_forbidden_claims("We use Azure Data Lake.", profile)
        assert len(violations) == 0

    def test_violation_detected(self):
        profile = {"forbidden_claims": ["Does NOT use Snowflake"]}
        violations = check_forbidden_claims("We integrate with Snowflake.", profile)
        assert len(violations) >= 1

    def test_negated_context_no_violation(self):
        profile = {"forbidden_claims": ["Does NOT use Snowflake"]}
        violations = check_forbidden_claims("We do not use Snowflake.", profile)
        assert len(violations) == 0

    def test_platform_service_pattern(self):
        terms = _extract_check_terms("Platform service 'ML Studio' is not available")
        assert "ML Studio" in terms

    def test_is_negated(self):
        assert _is_negated("we do not use snowflake in our stack", "snowflake")
        assert not _is_negated("we use snowflake for analytics", "snowflake")


# ---------------------------------------------------------------------------
# Tests: Commands
# ---------------------------------------------------------------------------

class TestCmdShow:
    def test_show_existing(self, kb_dirs, capsys):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"):
            rc = cmd_show("KB_0001")
            assert rc == 0
            out = capsys.readouterr().out
            assert "KB_0001" in out
            assert "99.97%" in out

    def test_show_not_found(self, kb_dirs, capsys):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"):
            rc = cmd_show("NONEXISTENT")
            assert rc == 1


class TestCmdCorrectOffline:
    def test_dry_run(self, kb_dirs, capsys):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs / "profiles"):
            rc = cmd_correct_offline("KB_0001", "New answer text.", dry_run=True)
            assert rc == 0
            out = capsys.readouterr().out
            assert "DRY RUN" in out

    def test_apply(self, kb_dirs):
        fb_log = kb_dirs / "feedback_log.jsonl"
        fb_counter = kb_dirs / ".fb_counter"
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs / "profiles"), \
             patch("rfp_feedback.FEEDBACK_LOG", fb_log), \
             patch("rfp_feedback._FB_COUNTER_PATH", fb_counter):
            rc = cmd_correct_offline("KB_0001", "Updated answer.", dry_run=False)
            assert rc == 0
            # Verify entry was updated
            entry = json.loads(
                (kb_dirs / "verified" / "planning" / "KB_0001.json").read_text()
            )
            assert entry["answer"] == "Updated answer."

    def test_not_found(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs / "rejected"):
            rc = cmd_correct_offline("NOPE", "text", dry_run=True)
            assert rc == 1


class TestCmdSearch:
    def test_text_search_found(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"):
            results = _text_search("SLA")
            assert len(results) >= 1
            assert results[0]["id"] == "KB_0001"

    def test_text_search_family_filter(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"):
            results = _text_search("SLA", family="wms")
            assert len(results) == 0

    def test_search_no_results(self, kb_dirs, capsys):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs / "verified"), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs / "drafts"):
            rc = cmd_search("xyznonexistent123")
            assert rc == 0
            assert "No matching" in capsys.readouterr().out
