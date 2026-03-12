"""Tests for rfp_feedback CLI and kb_migrate_to_files."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_migrate_to_files import normalize_entry, migrate, setup_directories
from rfp_feedback import (
    find_entry,
    find_entry_dir,
    load_entry,
    save_entry,
    append_feedback_log,
    read_feedback_log,
    check_forbidden_claims,
    _extract_check_terms,
    _is_negated,
    _has_same_issue,
    cmd_show,
    cmd_correct_offline,
    cmd_approve,
    cmd_reject,
    cmd_retag,
    cmd_propagate,
    cmd_log,
    cmd_search,
    _text_search,
    VERIFIED_DIR,
    DRAFTS_DIR,
    REJECTED_DIR,
    FEEDBACK_LOG,
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
    return {"verified": verified, "drafts": drafts, "rejected": rejected,
            "root": tmp_path}


@pytest.fixture
def sample_entry():
    """A standard verified entry."""
    return {
        "id": "KB_0001",
        "question": "How does Blue Yonder handle authentication?",
        "answer": "Blue Yonder uses SSO and LDAP for authentication.",
        "question_variants": [],
        "solution_codes": [],
        "family_code": "planning",
        "category": "technical",
        "subcategory": "security",
        "tags": ["SSO", "LDAP"],
        "confidence": "verified",
        "source_rfps": [],
        "last_updated": "2026-03-12",
        "cloud_native_only": False,
        "notes": "",
        "feedback_history": [],
        "provenance": {
            "original_source": "test",
            "migrated_at": "2026-03-12T00:00:00",
        },
    }


@pytest.fixture
def draft_entry():
    """A draft entry pending review."""
    return {
        "id": "KB_1001",
        "question": "Does the system support JSON ingestion?",
        "answer": "Yes, JSON, CSV, and Parquet are supported.",
        "question_variants": [],
        "solution_codes": [],
        "family_code": "wms",
        "category": "functional",
        "subcategory": "",
        "tags": ["ingestion"],
        "confidence": "draft",
        "source_rfps": [],
        "last_updated": "2026-03-12",
        "cloud_native_only": False,
        "notes": "",
        "feedback_history": [],
        "provenance": {},
    }


def _write_entry(base_dir, entry, subdir=None):
    """Write an entry to a dir, optionally in a family subdir."""
    if subdir:
        target = base_dir / subdir / f"{entry['id']}.json"
    else:
        target = base_dir / f"{entry['id']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)
    return target


# ===================================================================
# Migration tests
# ===================================================================

class TestMigration:

    def test_normalize_v1_entry(self):
        v1 = {
            "kb_id": "kb_0001",
            "canonical_question": "How does auth work?",
            "canonical_answer": "SSO and LDAP.",
            "category": "technical",
            "subcategory": "security",
            "domain": "planning",
            "rich_metadata": {
                "keywords": ["SSO", "LDAP"],
                "question_variants": ["Auth question?"],
            },
            "last_updated": "2026-01-01",
        }
        result = normalize_entry(v1, "planning", "source.json")
        assert result["id"] == "KB_0001"
        assert result["question"] == "How does auth work?"
        assert result["answer"] == "SSO and LDAP."
        assert result["family_code"] == "planning"
        assert result["tags"] == ["SSO", "LDAP"]
        assert result["question_variants"] == ["Auth question?"]
        assert result["confidence"] == "verified"
        assert result["provenance"]["original_source"] == "source.json"
        assert result["provenance"]["original_id"] == "kb_0001"

    def test_normalize_v2_entry(self):
        v2 = {
            "id": "NET-FUNC-0001",
            "question": "What are recent features?",
            "answer": "Network improvements.",
            "family_code": "network",
            "category": "functional",
            "subcategory": "features",
            "tags": ["network"],
            "question_variants": ["Recent changes?"],
            "confidence": "draft",
            "solution_codes": ["network"],
            "source_rfps": ["ARC-0001"],
            "last_updated": "2026-02-19",
            "cloud_native_only": True,
            "notes": "From workshop",
        }
        result = normalize_entry(v2, "network", "net.json")
        assert result["id"] == "NET-FUNC-0001"
        assert result["question"] == "What are recent features?"
        assert result["family_code"] == "network"
        assert result["confidence"] == "draft"
        assert result["cloud_native_only"] is True
        assert result["solution_codes"] == ["network"]
        assert result["provenance"]["original_id"] == "NET-FUNC-0001"

    def test_migrate_creates_individual_files(self, tmp_path):
        canonical = tmp_path / "canonical"
        verified = tmp_path / "verified"
        canonical.mkdir()

        entries = [
            {"kb_id": "kb_0001", "canonical_question": "Q1",
             "canonical_answer": "A1", "category": "tech", "domain": "planning"},
            {"kb_id": "kb_0002", "canonical_question": "Q2",
             "canonical_answer": "A2", "category": "func", "domain": "planning"},
        ]
        with open(canonical / "RFP_Database_Cognitive_Planning_CANONICAL.json",
                   "w", encoding="utf-8") as f:
            json.dump(entries, f)

        with patch("kb_migrate_to_files.SCHEMA_PATH",
                   Path(__file__).resolve().parents[1] / "data" / "kb" / "schema" / "family_config.json"):
            summary = migrate(canonical, verified)

        assert summary["planning"] == 2
        assert (verified / "planning" / "KB_0001.json").exists()
        assert (verified / "planning" / "KB_0002.json").exists()

    def test_migrate_preserves_all_fields(self, tmp_path):
        canonical = tmp_path / "canonical"
        verified = tmp_path / "verified"
        canonical.mkdir()

        entry = {
            "kb_id": "kb_0001",
            "canonical_question": "Q?",
            "canonical_answer": "A.",
            "category": "technical",
            "subcategory": "auth",
            "domain": "planning",
            "rich_metadata": {"keywords": ["SSO"], "question_variants": ["Alt?"]},
            "last_updated": "2026-01-01",
        }
        with open(canonical / "RFP_Database_Cognitive_Planning_CANONICAL.json",
                   "w", encoding="utf-8") as f:
            json.dump([entry], f)

        with patch("kb_migrate_to_files.SCHEMA_PATH",
                   Path(__file__).resolve().parents[1] / "data" / "kb" / "schema" / "family_config.json"):
            migrate(canonical, verified)

        out = json.load(open(verified / "planning" / "KB_0001.json", encoding="utf-8"))
        assert out["question"] == "Q?"
        assert out["answer"] == "A."
        assert out["category"] == "technical"
        assert out["subcategory"] == "auth"
        assert out["tags"] == ["SSO"]
        assert out["question_variants"] == ["Alt?"]

    def test_migrate_adds_provenance(self, tmp_path):
        canonical = tmp_path / "canonical"
        verified = tmp_path / "verified"
        canonical.mkdir()

        with open(canonical / "RFP_Database_Cognitive_Planning_CANONICAL.json",
                   "w", encoding="utf-8") as f:
            json.dump([{"kb_id": "kb_0001", "canonical_question": "Q",
                        "canonical_answer": "A", "domain": "planning"}], f)

        with patch("kb_migrate_to_files.SCHEMA_PATH",
                   Path(__file__).resolve().parents[1] / "data" / "kb" / "schema" / "family_config.json"):
            migrate(canonical, verified)

        out = json.load(open(verified / "planning" / "KB_0001.json", encoding="utf-8"))
        assert "provenance" in out
        assert out["provenance"]["original_source"] == "RFP_Database_Cognitive_Planning_CANONICAL.json"
        assert "migrated_at" in out["provenance"]

    def test_migrate_sets_verified_confidence(self, tmp_path):
        canonical = tmp_path / "canonical"
        verified = tmp_path / "verified"
        canonical.mkdir()

        with open(canonical / "RFP_Database_Cognitive_Planning_CANONICAL.json",
                   "w", encoding="utf-8") as f:
            json.dump([{"kb_id": "kb_0001", "canonical_question": "Q",
                        "canonical_answer": "A", "domain": "planning"}], f)

        with patch("kb_migrate_to_files.SCHEMA_PATH",
                   Path(__file__).resolve().parents[1] / "data" / "kb" / "schema" / "family_config.json"):
            migrate(canonical, verified)

        out = json.load(open(verified / "planning" / "KB_0001.json", encoding="utf-8"))
        assert out["confidence"] == "verified"

    def test_migrate_creates_family_subdirs(self, tmp_path):
        canonical = tmp_path / "canonical"
        verified = tmp_path / "verified"
        canonical.mkdir()

        # Two families
        with open(canonical / "RFP_Database_Cognitive_Planning_CANONICAL.json",
                   "w", encoding="utf-8") as f:
            json.dump([{"kb_id": "kb_0001", "canonical_question": "Q",
                        "canonical_answer": "A", "domain": "planning"}], f)
        with open(canonical / "RFP_Database_WMS_CANONICAL.json",
                   "w", encoding="utf-8") as f:
            json.dump([{"kb_id": "kb_0001", "canonical_question": "Q",
                        "canonical_answer": "A", "domain": "wms"}], f)

        with patch("kb_migrate_to_files.SCHEMA_PATH",
                   Path(__file__).resolve().parents[1] / "data" / "kb" / "schema" / "family_config.json"):
            migrate(canonical, verified)

        assert (verified / "planning").is_dir()
        assert (verified / "wms").is_dir()


# ===================================================================
# Entry lookup tests
# ===================================================================

class TestEntryLookup:

    def test_find_entry_in_verified(self, kb_dirs, sample_entry):
        _write_entry(kb_dirs["verified"], sample_entry, "planning")
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]):
            path = find_entry("KB_0001", ["verified"])
        assert path is not None
        assert path.name == "KB_0001.json"

    def test_find_entry_in_drafts(self, kb_dirs, draft_entry):
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]):
            path = find_entry("KB_1001", ["drafts"])
        assert path is not None

    def test_find_entry_not_found(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]):
            path = find_entry("KB_9999")
        assert path is None

    def test_search_by_text(self, kb_dirs, sample_entry):
        _write_entry(kb_dirs["verified"], sample_entry, "planning")
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]):
            results = _text_search("authentication", family="planning")
        assert len(results) >= 1
        assert results[0]["id"] == "KB_0001"


# ===================================================================
# Correct tests
# ===================================================================

class TestCorrect:

    def test_correct_updates_answer(self, kb_dirs, sample_entry):
        path = _write_entry(kb_dirs["verified"], sample_entry, "planning")
        new_answer = "Blue Yonder uses only SSO for authentication."

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            ret = cmd_correct_offline("KB_0001", new_answer, dry_run=False)

        assert ret == 0
        updated = json.load(open(path, encoding="utf-8"))
        assert updated["answer"] == new_answer

    def test_correct_dry_run_no_changes(self, kb_dirs, sample_entry):
        path = _write_entry(kb_dirs["verified"], sample_entry, "planning")
        original_answer = sample_entry["answer"]

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]):
            ret = cmd_correct_offline("KB_0001", "New answer", dry_run=True)

        assert ret == 0
        unchanged = json.load(open(path, encoding="utf-8"))
        assert unchanged["answer"] == original_answer

    def test_correct_adds_feedback_history(self, kb_dirs, sample_entry):
        path = _write_entry(kb_dirs["verified"], sample_entry, "planning")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_correct_offline("KB_0001", "Fixed answer", dry_run=False)

        updated = json.load(open(path, encoding="utf-8"))
        assert len(updated["feedback_history"]) == 1
        assert updated["feedback_history"][0]["action"] == "corrected"

    def test_correct_appends_to_log(self, kb_dirs, sample_entry):
        _write_entry(kb_dirs["verified"], sample_entry, "planning")
        log_path = kb_dirs["root"] / "log.jsonl"

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", log_path), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_correct_offline("KB_0001", "Fixed", dry_run=False)

        assert log_path.exists()
        line = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert line["action"] == "correct"
        assert line["entry_id"] == "KB_0001"

    def test_correct_preserves_other_fields(self, kb_dirs, sample_entry):
        path = _write_entry(kb_dirs["verified"], sample_entry, "planning")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_correct_offline("KB_0001", "New answer text", dry_run=False)

        updated = json.load(open(path, encoding="utf-8"))
        assert updated["question"] == sample_entry["question"]
        assert updated["family_code"] == "planning"
        assert updated["category"] == "technical"
        assert updated["tags"] == ["SSO", "LDAP"]


# ===================================================================
# Approve tests
# ===================================================================

class TestApprove:

    def test_approve_moves_draft_to_verified(self, kb_dirs, draft_entry):
        draft_path = _write_entry(kb_dirs["drafts"], draft_entry, "wms")
        verified_dir = kb_dirs["verified"]

        with patch("rfp_feedback.VERIFIED_DIR", verified_dir), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            ret = cmd_approve("KB_1001")

        assert ret == 0
        assert not draft_path.exists()
        assert (verified_dir / "wms" / "KB_1001.json").exists()

    def test_approve_sets_confidence_verified(self, kb_dirs, draft_entry):
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_approve("KB_1001")

        entry = json.load(open(
            kb_dirs["verified"] / "wms" / "KB_1001.json", encoding="utf-8"))
        assert entry["confidence"] == "verified"

    def test_approve_validates_forbidden_claims(self, kb_dirs, draft_entry):
        """Approve warns when forbidden claims are violated."""
        draft_entry["answer"] = "We use Snowflake for data storage."
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")

        # Create a profile with forbidden claim
        profile_dir = kb_dirs["root"] / "profiles"
        profile_dir.mkdir()
        import yaml
        with open(profile_dir / "wms.yaml", "w") as f:
            yaml.dump({"forbidden_claims": ["Does NOT use Snowflake"]}, f)

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", profile_dir), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"), \
             patch("builtins.input", return_value="n"):
            ret = cmd_approve("KB_1001")

        # Should abort because user said "n"
        assert ret == 1
        # Draft should still be in drafts
        assert (kb_dirs["drafts"] / "wms" / "KB_1001.json").exists()

    def test_approve_warns_on_violations(self, kb_dirs, draft_entry):
        """When violations found and user says yes, still approves."""
        draft_entry["answer"] = "We use Snowflake for data."
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")

        profile_dir = kb_dirs["root"] / "profiles"
        profile_dir.mkdir()
        import yaml
        with open(profile_dir / "wms.yaml", "w") as f:
            yaml.dump({"forbidden_claims": ["Does NOT use Snowflake"]}, f)

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", profile_dir), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"), \
             patch("builtins.input", return_value="y"):
            ret = cmd_approve("KB_1001")

        assert ret == 0
        assert (kb_dirs["verified"] / "wms" / "KB_1001.json").exists()

    def test_approve_nonexistent_entry_errors(self, kb_dirs):
        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]):
            ret = cmd_approve("KB_9999")
        assert ret == 1


# ===================================================================
# Reject tests
# ===================================================================

class TestReject:

    def test_reject_moves_to_rejected(self, kb_dirs, draft_entry):
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            ret = cmd_reject("KB_1001", reason="Outdated info")

        assert ret == 0
        assert not (kb_dirs["drafts"] / "wms" / "KB_1001.json").exists()
        assert (kb_dirs["rejected"] / "wms" / "KB_1001.json").exists()

    def test_reject_records_reason(self, kb_dirs, draft_entry):
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_reject("KB_1001", reason="Outdated info from 2023")

        entry = json.load(open(
            kb_dirs["rejected"] / "wms" / "KB_1001.json", encoding="utf-8"))
        assert entry["confidence"] == "rejected"
        assert entry["feedback_history"][-1]["reason"] == "Outdated info from 2023"

    def test_reject_appends_to_log(self, kb_dirs, draft_entry):
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")
        log_path = kb_dirs["root"] / "log.jsonl"

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.FEEDBACK_LOG", log_path), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_reject("KB_1001", reason="Bad data")

        line = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert line["action"] == "reject"
        assert line["reason"] == "Bad data"


# ===================================================================
# Retag tests
# ===================================================================

class TestRetag:

    def test_retag_product_changes_family(self, kb_dirs, sample_entry):
        _write_entry(kb_dirs["verified"], sample_entry, "planning")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            ret = cmd_retag("KB_0001", product="wms_native")

        assert ret == 0
        # Should be in new family dir
        new_path = kb_dirs["verified"] / "wms_native" / "KB_0001.json"
        assert new_path.exists()
        entry = json.load(open(new_path, encoding="utf-8"))
        assert entry["family_code"] == "wms_native"

    def test_retag_category_changes_category(self, kb_dirs, sample_entry):
        path = _write_entry(kb_dirs["verified"], sample_entry, "planning")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            ret = cmd_retag("KB_0001", category="functional")

        assert ret == 0
        entry = json.load(open(path, encoding="utf-8"))
        assert entry["category"] == "functional"

    def test_retag_moves_file_to_new_family_dir(self, kb_dirs, sample_entry):
        old_path = _write_entry(kb_dirs["verified"], sample_entry, "planning")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_retag("KB_0001", product="wms")

        assert not old_path.exists()
        assert (kb_dirs["verified"] / "wms" / "KB_0001.json").exists()


# ===================================================================
# Propagate tests
# ===================================================================

class TestPropagate:

    def test_propagate_finds_similar_entries(self, kb_dirs, sample_entry):
        """Propagate with mocked search returns results."""
        sample_entry["feedback_history"] = [{
            "action": "corrected",
            "timestamp": "2026-03-12T12:00:00",
            "correction": "Remove LDAP from supported protocols",
        }]
        _write_entry(kb_dirs["verified"], sample_entry, "planning")

        mock_similar = [{
            "id": "KB_0002",
            "question": "What protocols does BY support?",
            "answer": "Blue Yonder supports SSO and LDAP.",
            "family": "planning",
            "similarity": 0.88,
        }]

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.search_similar", return_value=mock_similar):
            ret = cmd_propagate("KB_0001", dry_run=True)

        assert ret == 0

    def test_propagate_dry_run_no_changes(self, kb_dirs, sample_entry):
        sample_entry["feedback_history"] = [{
            "action": "corrected",
            "timestamp": "2026-03-12T12:00:00",
            "correction": "Remove LDAP",
        }]
        path = _write_entry(kb_dirs["verified"], sample_entry, "planning")
        original = json.load(open(path, encoding="utf-8"))

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.search_similar", return_value=[{
                 "id": "KB_0002", "question": "Q", "answer": "Uses LDAP",
                 "family": "planning", "similarity": 0.9,
             }]):
            cmd_propagate("KB_0001", dry_run=True)

        # Original entry unchanged
        after = json.load(open(path, encoding="utf-8"))
        assert after == original

    def test_propagate_flags_entries(self, kb_dirs, sample_entry):
        sample_entry["feedback_history"] = [{
            "action": "corrected",
            "timestamp": "2026-03-12T12:00:00",
            "correction": "Remove LDAP",
        }]
        _write_entry(kb_dirs["verified"], sample_entry, "planning")

        # Create the target entry that will be flagged
        target = {
            "id": "KB_0002",
            "question": "Auth protocols?",
            "answer": "Uses LDAP and SSO.",
            "family_code": "planning",
            "category": "technical",
            "feedback_history": [],
        }
        target_path = _write_entry(kb_dirs["verified"], target, "planning")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.FEEDBACK_LOG", kb_dirs["root"] / "log.jsonl"), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"), \
             patch("rfp_feedback.search_similar", return_value=[{
                 "id": "KB_0002", "question": "Auth protocols?",
                 "answer": "Uses LDAP and SSO.",
                 "family": "planning", "similarity": 0.9,
             }]):
            ret = cmd_propagate("KB_0001", dry_run=False)

        assert ret == 0
        flagged = json.load(open(target_path, encoding="utf-8"))
        assert any(h["action"] == "flagged_for_review"
                   for h in flagged["feedback_history"])

    def test_propagate_no_matches_clean(self, kb_dirs, sample_entry):
        sample_entry["feedback_history"] = [{
            "action": "corrected",
            "timestamp": "2026-03-12T12:00:00",
            "correction": "Fix something specific",
        }]
        _write_entry(kb_dirs["verified"], sample_entry, "planning")

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.search_similar", return_value=[]):
            ret = cmd_propagate("KB_0001", dry_run=True)

        assert ret == 0


# ===================================================================
# Forbidden claims check tests
# ===================================================================

class TestForbiddenClaims:

    def test_forbidden_claim_detected(self):
        answer = "Blue Yonder uses Snowflake for cloud data warehousing."
        profile = {"forbidden_claims": ["Does NOT use Snowflake"]}
        violations = check_forbidden_claims(answer, profile)
        assert len(violations) >= 1
        assert any("Snowflake" in v for v in violations)

    def test_negated_term_not_flagged(self):
        answer = "Blue Yonder does not use Snowflake for storage."
        profile = {"forbidden_claims": ["Does NOT use Snowflake"]}
        violations = check_forbidden_claims(answer, profile)
        assert len(violations) == 0

    def test_no_violations_clean(self):
        answer = "Blue Yonder uses PostgreSQL for data storage."
        profile = {"forbidden_claims": ["Does NOT use Snowflake"]}
        violations = check_forbidden_claims(answer, profile)
        assert len(violations) == 0

    def test_check_loads_correct_profile(self, kb_dirs):
        """load_profile returns correct profile for family."""
        import yaml
        profile_dir = kb_dirs["root"] / "profiles"
        profile_dir.mkdir()
        with open(profile_dir / "wms.yaml", "w") as f:
            yaml.dump({"product": "wms",
                        "forbidden_claims": ["No JSON support"]}, f)

        with patch("rfp_feedback.PROFILES_DIR", profile_dir):
            from rfp_feedback import load_profile
            p = load_profile("wms")

        assert p["product"] == "wms"
        assert "No JSON support" in p["forbidden_claims"]


# ===================================================================
# Feedback log tests
# ===================================================================

class TestFeedbackLog:

    def test_log_appended_on_correct(self, kb_dirs, sample_entry):
        _write_entry(kb_dirs["verified"], sample_entry, "planning")
        log_path = kb_dirs["root"] / "log.jsonl"

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", log_path), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_correct_offline("KB_0001", "New answer", dry_run=False)

        assert log_path.exists()
        entries = [json.loads(l) for l in log_path.read_text(encoding="utf-8").strip().split("\n")]
        assert entries[0]["action"] == "correct"

    def test_log_appended_on_approve(self, kb_dirs, draft_entry):
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")
        log_path = kb_dirs["root"] / "log.jsonl"

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", log_path), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_approve("KB_1001")

        entries = [json.loads(l) for l in log_path.read_text(encoding="utf-8").strip().split("\n")]
        assert entries[0]["action"] == "approve"

    def test_log_appended_on_reject(self, kb_dirs, draft_entry):
        _write_entry(kb_dirs["drafts"], draft_entry, "wms")
        log_path = kb_dirs["root"] / "log.jsonl"

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", log_path), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_reject("KB_1001", reason="Bad")

        entries = [json.loads(l) for l in log_path.read_text(encoding="utf-8").strip().split("\n")]
        assert entries[0]["action"] == "reject"

    def test_log_format_valid_jsonl(self, kb_dirs, sample_entry):
        _write_entry(kb_dirs["verified"], sample_entry, "planning")
        log_path = kb_dirs["root"] / "log.jsonl"

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", log_path), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_correct_offline("KB_0001", "A1", dry_run=False)
            cmd_correct_offline("KB_0001", "A2", dry_run=False)

        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_log_has_feedback_id_and_timestamp(self, kb_dirs, sample_entry):
        _write_entry(kb_dirs["verified"], sample_entry, "planning")
        log_path = kb_dirs["root"] / "log.jsonl"

        with patch("rfp_feedback.VERIFIED_DIR", kb_dirs["verified"]), \
             patch("rfp_feedback.DRAFTS_DIR", kb_dirs["drafts"]), \
             patch("rfp_feedback.REJECTED_DIR", kb_dirs["rejected"]), \
             patch("rfp_feedback.PROFILES_DIR", kb_dirs["root"]), \
             patch("rfp_feedback.FEEDBACK_LOG", log_path), \
             patch("rfp_feedback._FB_COUNTER_PATH", kb_dirs["root"] / ".fb"):
            cmd_correct_offline("KB_0001", "Fixed", dry_run=False)

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "feedback_id" in entry
        assert entry["feedback_id"].startswith("FB_")
        assert "timestamp" in entry
        assert "2026" in entry["timestamp"]
