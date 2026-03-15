"""Tests for kb_to_markdown -- KB JSON to Markdown migration."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_to_markdown import (
    json_to_markdown,
    _build_markdown_id,
    _yaml_list,
    _md_filename,
    ensure_directories,
    convert_directory,
    migrate,
    FAMILY_PRODUCTS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_entry():
    """A typical verified KB entry."""
    return {
        "id": "KB_0001",
        "question": "How does Blue Yonder ensure high availability?",
        "answer": "Blue Yonder provides 99.97% SLA with active-active deployment.",
        "question_variants": ["What is Blue Yonder's uptime SLA?"],
        "solution_codes": [],
        "family_code": "planning",
        "category": "technical",
        "subcategory": "High Availability",
        "tags": ["availability", "sla", "disaster-recovery"],
        "confidence": "verified",
        "source_rfps": ["Lenzing_2026"],
        "last_updated": "2025-12-30",
        "cloud_native_only": False,
        "notes": "",
        "feedback_history": [],
    }


@pytest.fixture
def sample_draft():
    """A typical draft KB entry."""
    return {
        "id": "KB_DRAFT_0001",
        "question": "What deployment model does Blue Yonder use?",
        "answer": "Blue Yonder is cloud-native SaaS on Microsoft Azure.",
        "family_code": "planning",
        "category": "technical",
        "subcategory": "",
        "tags": ["cloud", "azure"],
        "confidence": "draft",
        "source_rfps": [],
        "last_updated": "2026-03-12",
    }


@pytest.fixture
def kb_tree(tmp_path):
    """Create a mock KB directory structure with verified and draft entries."""
    verified = tmp_path / "verified"
    drafts = tmp_path / "drafts"

    # Verified: planning family
    (verified / "planning").mkdir(parents=True)
    entry1 = {
        "id": "KB_0001",
        "question": "How does HA work?",
        "answer": "Active-active with 99.97% SLA.",
        "family_code": "planning",
        "category": "technical",
        "subcategory": "High Availability",
        "tags": ["ha", "sla"],
        "source_rfps": ["Lenzing_2026"],
        "last_updated": "2025-12-30",
    }
    (verified / "planning" / "KB_0001.json").write_text(
        json.dumps(entry1), encoding="utf-8"
    )
    entry2 = {
        "id": "KB_0002",
        "question": "What auth is supported?",
        "answer": "LDAP and Active Directory.",
        "family_code": "planning",
        "category": "technical",
        "subcategory": "Access Management",
        "tags": ["auth", "ldap"],
        "source_rfps": [],
        "last_updated": "2025-12-30",
    }
    (verified / "planning" / "KB_0002.json").write_text(
        json.dumps(entry2), encoding="utf-8"
    )

    # Verified: wms family
    (verified / "wms").mkdir(parents=True)
    wms_entry = {
        "id": "wms_0002",
        "question": "What resources does a customer get?",
        "answer": "A technical account manager.",
        "family_code": "wms",
        "category": "customer_executive",
        "subcategory": "TAM",
        "tags": ["tam"],
        "source_rfps": [],
        "last_updated": "2025-12-30",
    }
    (verified / "wms" / "wms_0002.json").write_text(
        json.dumps(wms_entry), encoding="utf-8"
    )

    # Drafts: planning family
    (drafts / "planning").mkdir(parents=True)
    draft1 = {
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
    }
    (drafts / "planning" / "KB_DRAFT_0001.json").write_text(
        json.dumps(draft1), encoding="utf-8"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestJsonToMarkdown:
    """Test the core JSON -> markdown conversion."""

    def test_converts_json_to_markdown(self, sample_entry):
        md = json_to_markdown(sample_entry, "planning", "verified")
        assert md.startswith("---")
        assert "## Question" in md
        assert "## Answer" in md
        assert "99.97% SLA" in md
        assert "high availability" in md.lower()

    def test_frontmatter_has_required_fields(self, sample_entry):
        md = json_to_markdown(sample_entry, "planning", "verified")
        assert "id: kb-rfp-planning-kb-0001" in md
        assert "doc_type: rfp_response" in md
        assert "trust_level: verified" in md
        assert "products:" in md
        assert "topics:" in md
        assert "category: technical" in md
        assert "tags:" in md
        assert "source_rfps:" in md
        assert "last_reviewed:" in md

    def test_question_and_answer_sections(self, sample_entry):
        md = json_to_markdown(sample_entry, "planning", "verified")
        lines = md.split("\n")
        # Find the question section
        q_idx = lines.index("## Question")
        a_idx = lines.index("## Answer")
        assert q_idx < a_idx
        # Question text appears between ## Question and ## Answer
        question_block = "\n".join(lines[q_idx + 1:a_idx])
        assert "How does Blue Yonder ensure high availability?" in question_block
        # Answer text appears after ## Answer
        answer_block = "\n".join(lines[a_idx + 1:])
        assert "99.97% SLA" in answer_block

    def test_family_to_products_mapping(self):
        """Each known family maps to correct product list."""
        assert "Blue Yonder WMS" in FAMILY_PRODUCTS["wms"]
        assert "Blue Yonder Demand Planning" in FAMILY_PRODUCTS["planning"]
        assert "Blue Yonder TMS" in FAMILY_PRODUCTS["logistics"]
        assert "Blue Yonder Network" in FAMILY_PRODUCTS["network"]
        assert "Blue Yonder Platform" in FAMILY_PRODUCTS["aiml"]

    def test_unknown_family_gets_title_cased_name(self):
        entry = {
            "id": "XX_0001",
            "question": "Test?",
            "answer": "Yes.",
            "family_code": "new_product",
            "category": "general",
        }
        md = json_to_markdown(entry, "new_product")
        assert "Blue Yonder New Product" in md

    def test_trust_level_draft(self, sample_draft):
        md = json_to_markdown(sample_draft, "planning", "draft")
        assert "trust_level: draft" in md

    def test_handles_missing_fields_gracefully(self):
        """An entry with minimal fields should still produce valid markdown."""
        minimal = {"id": "MIN_001", "question": "Q?", "answer": "A."}
        md = json_to_markdown(minimal, "planning")
        assert "---" in md
        assert "## Question" in md
        assert "## Answer" in md
        assert "Q?" in md
        assert "A." in md
        # Missing fields get defaults
        assert "tags: []" in md
        assert "source_rfps: []" in md
        assert "topics: []" in md

    def test_handles_no_question_or_answer(self):
        entry = {"id": "EMPTY_001"}
        md = json_to_markdown(entry, "planning")
        assert "(no question)" in md
        assert "(no answer)" in md

    def test_v1_schema_fields(self):
        """Entries with v1 field names (kb_id, canonical_question) work."""
        entry = {
            "kb_id": "kb_0099",
            "canonical_question": "Old format question?",
            "canonical_answer": "Old format answer.",
            "category": "functional",
            "tags": ["legacy"],
        }
        md = json_to_markdown(entry, "planning")
        assert "Old format question?" in md
        assert "Old format answer." in md


class TestDraftsGoToStaging:
    """Test that drafts go to _staging/ subfolder."""

    def test_drafts_go_to_staging(self, kb_tree):
        output = kb_tree / "output"
        output.mkdir()
        drafts_dir = kb_tree / "drafts"
        stats = convert_directory(drafts_dir, output, "draft", dry_run=False)
        # Should have created _staging/planning/
        staging_file = output / "_staging" / "planning" / "kb-draft-0001.md"
        assert staging_file.exists()
        content = staging_file.read_text(encoding="utf-8")
        assert "trust_level: draft" in content


class TestDryRunNoFilesWritten:
    """Test that --dry-run doesn't write any files."""

    def test_dry_run_no_files_written(self, kb_tree):
        output = kb_tree / "output"
        result = migrate(
            verified_dir=kb_tree / "verified",
            drafts_dir=kb_tree / "drafts",
            output_dir=output,
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["total_verified"] == 3
        assert result["total_drafts"] == 1
        # No output directory should have been created
        assert not output.exists()


class TestFullMigration:
    """Integration-style tests for the full migrate() function."""

    def test_full_migration(self, kb_tree):
        output = kb_tree / "output"
        result = migrate(
            verified_dir=kb_tree / "verified",
            drafts_dir=kb_tree / "drafts",
            output_dir=output,
            dry_run=False,
        )
        assert result["total_verified"] == 3
        assert result["total_drafts"] == 1
        assert result["verified"]["planning"] == 2
        assert result["verified"]["wms"] == 1
        assert result["drafts"]["planning"] == 1

        # Check files exist
        assert (output / "planning" / "kb-0001.md").exists()
        assert (output / "planning" / "kb-0002.md").exists()
        assert (output / "wms" / "wms-0002.md").exists()
        assert (output / "_staging" / "planning" / "kb-draft-0001.md").exists()

    def test_invalid_json_skipped(self, kb_tree):
        # Write invalid JSON
        bad = kb_tree / "verified" / "planning" / "BAD.json"
        bad.write_text("not json{{{", encoding="utf-8")
        output = kb_tree / "output"
        result = migrate(
            verified_dir=kb_tree / "verified",
            drafts_dir=kb_tree / "drafts",
            output_dir=output,
            dry_run=False,
        )
        # 3 valid entries still processed, bad one skipped
        assert result["total_verified"] == 3


class TestHelpers:
    """Test helper functions."""

    def test_build_markdown_id(self):
        assert _build_markdown_id("planning", "KB_0001") == "kb-rfp-planning-kb-0001"
        assert _build_markdown_id("wms", "wms_0002") == "kb-rfp-wms-0002"

    def test_yaml_list_empty(self):
        assert _yaml_list([]) == "[]"

    def test_yaml_list_strings(self):
        result = _yaml_list(["foo", "bar"])
        assert result == "[foo, bar]"

    def test_yaml_list_special_chars(self):
        result = _yaml_list(["Blue Yonder WMS", "a:b"])
        assert '"Blue Yonder WMS"' in result
        assert '"a:b"' in result

    def test_md_filename(self):
        assert _md_filename("KB_0001") == "kb-0001.md"
        assert _md_filename("wms_0002") == "wms-0002.md"

    def test_ensure_directories(self, tmp_path):
        created = ensure_directories(tmp_path / "out", ["planning", "wms"])
        # Should create planning, wms, and default dirs like _staging
        out = tmp_path / "out"
        assert (out / "planning").exists()
        assert (out / "wms").exists()
        assert (out / "_staging").exists()
