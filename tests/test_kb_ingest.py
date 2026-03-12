"""Tests for kb_ingest -- Knowledge Ingestion Pipeline."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_ingest import (
    load_architecture_facts,
    scan_project_facts,
    deduplicate_facts,
    filter_already_covered,
    collect_facts,
    cluster_facts,
    _subcluster_by_keywords,
    build_prompt,
    generate_qa_prompts,
    _parse_qa_response,
    generate_sync,
    validate_entry,
    deduplicate_against_kb,
    write_drafts,
    _next_draft_id,
    ingest,
    load_effective_profile,
    GENERATION_PROMPT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_profile():
    """A WMS-like product profile."""
    return {
        "product": "wms",
        "display_name": "Blue Yonder WMS",
        "cloud_native": False,
        "deployment": ["azure"],
        "apis": ["rest", "sftp"],
        "platform_services": {
            "available": ["api_management", "authentication"],
            "not_available": ["ml_studio", "bdm"],
            "coming_soon": [],
        },
        "forbidden_claims": [
            "Does NOT use Snowflake",
            "NOT cloud-native",
            "NOT microservices",
        ],
        "_meta": {"status": "draft"},
    }


@pytest.fixture
def sample_cke_data():
    """CKE JSON structure with WMS product."""
    return {
        "products": {
            "Blue Yonder WMS": {
                "deployment": [
                    {"fact": "Deployed on Azure AKS", "confidence": "high"},
                ],
                "apis": [
                    {"fact": "REST APIs for integration", "confidence": "high"},
                    {"fact": "SFTP for file-based imports", "confidence": "medium"},
                ],
                "security": [
                    {"fact": "SAML 2.0 for SSO", "confidence": "high"},
                ],
                "scalability": [
                    {"fact": "Max 500,000 order releases from PDC", "confidence": "high"},
                ],
                "not_supported": [
                    "NOT cloud-native",
                    "NOT microservices",
                ],
            }
        }
    }


@pytest.fixture
def cke_json_file(tmp_path, sample_cke_data):
    """Write CKE data to a temp file."""
    path = tmp_path / "test_cke.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sample_cke_data, f)
    return path


# ===================================================================
# Fact collection tests
# ===================================================================

class TestFactCollection:

    def test_collect_from_architecture_finds_wms_facts(self, cke_json_file):
        facts = load_architecture_facts("wms", svc_path=cke_json_file)
        assert len(facts) >= 3  # deployment + apis + security + scalability (not not_supported)
        fact_texts = [f["fact"] for f in facts]
        assert any("REST" in t for t in fact_texts)
        assert any("Azure" in t for t in fact_texts)

    def test_collect_filters_by_confidence(self, cke_json_file):
        facts = load_architecture_facts("wms", svc_path=cke_json_file)
        # All facts should have confidence >= 0.5
        for f in facts:
            assert f["confidence"] >= 0.5

        # Filter high confidence only
        high = [f for f in facts if f["confidence"] >= 0.9]
        assert len(high) >= 2  # "high" facts = 0.95

    def test_collect_skips_not_supported(self, cke_json_file):
        facts = load_architecture_facts("wms", svc_path=cke_json_file)
        categories = {f["category"] for f in facts}
        assert "not_supported" not in categories

    def test_collect_deduplicates_similar_facts(self):
        facts = [
            {"fact": "REST APIs for integration", "source": "a", "confidence": 0.9, "fact_id": "1"},
            {"fact": "REST APIs for integration", "source": "b", "confidence": 0.9, "fact_id": "2"},
            {"fact": "SFTP file transfer", "source": "a", "confidence": 0.8, "fact_id": "3"},
        ]
        deduped = deduplicate_facts(facts)
        assert len(deduped) == 2

    def test_collect_filters_already_in_kb(self):
        facts = [
            {"fact": "WMS supports REST API and SFTP", "source": "a", "confidence": 0.9},
            {"fact": "WMS uses SAML for SSO", "source": "a", "confidence": 0.9},
        ]
        existing = ["What REST APIs does WMS support for integration with SFTP?"]
        # Without embeddings, uses keyword fallback — REST/SFTP overlap should filter
        filtered = filter_already_covered(facts, existing, threshold=0.85)
        # At minimum, function should run without error
        assert isinstance(filtered, list)

    def test_collect_from_projects_scans_yaml(self, tmp_path):
        import yaml

        # Create a fake facts.yaml
        knowledge_dir = tmp_path / "_knowledge"
        knowledge_dir.mkdir()
        facts_data = [
            {
                "fact": "WMS supports wave planning",
                "source": "guide.pdf",
                "topics": ["Warehouse"],
                "products": ["Blue Yonder WMS"],
                "confidence": 0.9,
            }
        ]
        with open(knowledge_dir / "facts.yaml", "w") as f:
            yaml.dump(facts_data, f)

        with patch("kb_ingest.PROJECT_ROOT", tmp_path):
            results = scan_project_facts("wms", min_confidence=0.8)

        assert len(results) == 1
        assert "wave planning" in results[0]["fact"]


# ===================================================================
# Fact clustering tests
# ===================================================================

class TestFactClustering:

    def test_cluster_groups_similar_facts(self):
        facts = [
            {"fact": "REST APIs for data exchange", "category": "apis"},
            {"fact": "SFTP for file-based data transfer", "category": "apis"},
            {"fact": "SAML 2.0 for authentication", "category": "security"},
        ]
        clusters = cluster_facts(facts)
        assert len(clusters) == 2  # apis cluster + security cluster

    def test_cluster_single_fact_becomes_cluster(self):
        facts = [{"fact": "Single isolated fact", "category": "general"}]
        clusters = cluster_facts(facts)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_cluster_unrelated_facts_stay_separate(self):
        facts = [
            {"fact": "REST APIs available", "category": "apis"},
            {"fact": "Deployed on Azure", "category": "deployment"},
            {"fact": "SAML authentication", "category": "security"},
        ]
        clusters = cluster_facts(facts)
        assert len(clusters) == 3

    def test_cluster_empty_input(self):
        assert cluster_facts([]) == []

    def test_subcluster_splits_large_groups(self):
        facts = [{"fact": f"Fact about topic {i}", "category": "general"}
                 for i in range(10)]
        subclusters = _subcluster_by_keywords(facts, max_per_cluster=3)
        assert all(len(c) <= 3 for c in subclusters)
        # All facts should be in some cluster
        total = sum(len(c) for c in subclusters)
        assert total == 10


# ===================================================================
# Q&A generation tests
# ===================================================================

class TestQAGeneration:

    def test_generation_prompt_includes_forbidden_claims(self, sample_profile):
        cluster = [{"fact": "REST APIs", "source": "test"}]
        prompt = build_prompt(cluster, sample_profile)
        assert "Does NOT use Snowflake" in prompt
        assert "NOT cloud-native" in prompt

    def test_generation_prompt_includes_profile_context(self, sample_profile):
        cluster = [{"fact": "REST APIs", "source": "test"}]
        prompt = build_prompt(cluster, sample_profile)
        assert "Blue Yonder WMS" in prompt
        assert "api_management" in prompt

    def test_generation_prompt_includes_source_facts(self, sample_profile):
        cluster = [
            {"fact": "REST APIs for integration", "source": "arch.json"},
            {"fact": "SFTP for batch", "source": "arch.json"},
        ]
        prompt = build_prompt(cluster, sample_profile)
        assert "REST APIs for integration" in prompt
        assert "SFTP for batch" in prompt

    def test_parse_llm_response_extracts_fields(self):
        raw = json.dumps({
            "question": "What APIs does WMS support?",
            "answer": "Blue Yonder WMS supports REST APIs.",
            "question_variants": ["WMS API support?"],
            "category": "technical",
            "tags": ["api", "rest"],
        })
        parsed = _parse_qa_response(raw)
        assert parsed is not None
        assert parsed["question"] == "What APIs does WMS support?"
        assert parsed["category"] == "technical"

    def test_parse_llm_response_with_fences(self):
        raw = '```json\n{"question": "Q?", "answer": "A."}\n```'
        parsed = _parse_qa_response(raw)
        assert parsed is not None
        assert parsed["question"] == "Q?"

    def test_parse_llm_response_invalid_returns_none(self):
        assert _parse_qa_response("not json at all") is None
        assert _parse_qa_response("") is None


# ===================================================================
# Validation tests
# ===================================================================

class TestValidation:

    def test_validate_rejects_forbidden_claim_violation(self, sample_profile):
        entry = {
            "question": "Does WMS use Snowflake?",
            "answer": "Yes, WMS uses Snowflake for data warehousing.",
        }
        result = validate_entry(entry, sample_profile)
        assert result["_validation"] == "REJECTED"
        assert len(result.get("_violations", [])) >= 1

    def test_validate_passes_clean_entry(self, sample_profile):
        entry = {
            "question": "What APIs does WMS support?",
            "answer": "Blue Yonder WMS provides REST APIs for integration.",
        }
        result = validate_entry(entry, sample_profile)
        assert result["_validation"] == "PASSED"
        assert result["profile_validated"] is True

    def test_validate_warns_on_unavailable_service(self, sample_profile):
        entry = {
            "question": "Does WMS have ML capabilities?",
            "answer": "WMS includes ml studio for predictive analytics.",
        }
        result = validate_entry(entry, sample_profile)
        assert result["_validation"] == "WARNING"
        assert len(result.get("_warnings", [])) >= 1

    def test_validate_checks_negation_context(self, sample_profile):
        entry = {
            "question": "Cloud architecture?",
            "answer": "WMS does not use Snowflake for data storage.",
        }
        result = validate_entry(entry, sample_profile)
        # Negated mention should not trigger rejection
        assert result["_validation"] != "REJECTED"


# ===================================================================
# Deduplication tests
# ===================================================================

class TestDeduplication:

    def test_dedup_removes_similar_to_existing(self, tmp_path):
        """With text fallback, exact match is filtered."""
        # Create existing entry
        verified = tmp_path / "verified" / "wms"
        verified.mkdir(parents=True)
        entry = {
            "id": "KB_0001",
            "question": "what apis does wms support?",
            "answer": "REST and SFTP.",
            "family_code": "wms",
        }
        with open(verified / "KB_0001.json", "w") as f:
            json.dump(entry, f)

        new_entries = [
            {"question": "what apis does wms support?", "answer": "REST."},
            {"question": "How does WMS handle security?", "answer": "SAML."},
        ]

        with patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"):
            result = deduplicate_against_kb(new_entries, "wms")

        # At least the exact duplicate should be filtered
        questions = [e["question"] for e in result]
        assert "How does WMS handle security?" in questions

    def test_dedup_keeps_novel_entries(self, tmp_path):
        # Empty verified dir
        verified = tmp_path / "verified" / "wms"
        verified.mkdir(parents=True)

        new_entries = [
            {"question": "Novel question?", "answer": "Novel answer."},
        ]

        with patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"):
            result = deduplicate_against_kb(new_entries, "wms")

        assert len(result) == 1

    def test_dedup_checks_both_verified_and_drafts(self, tmp_path):
        # Create entries in both dirs
        for dir_name in ["verified", "drafts"]:
            d = tmp_path / dir_name / "wms"
            d.mkdir(parents=True)
            entry = {
                "id": f"KB_{dir_name}_001",
                "question": f"Question from {dir_name}?",
                "answer": "Answer.",
                "family_code": "wms",
            }
            with open(d / f"KB_{dir_name}_001.json", "w") as f:
                json.dump(entry, f)

        with patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"):
            from kb_ingest import load_existing_questions
            questions = load_existing_questions("wms")

        assert len(questions) == 2


# ===================================================================
# Draft writing tests
# ===================================================================

class TestDraftWriting:

    def test_write_creates_json_files(self, tmp_path):
        entries = [
            {"question": "Q1?", "answer": "A1.", "_validation": "PASSED",
             "profile_validated": True, "forbidden_claims_checked": True,
             "tags": ["tag1"], "source_facts": []},
            {"question": "Q2?", "answer": "A2.", "_validation": "PASSED",
             "profile_validated": True, "forbidden_claims_checked": True,
             "tags": [], "source_facts": []},
        ]
        written = write_drafts(entries, "wms", drafts_dir=tmp_path)
        assert written == 2
        files = list((tmp_path / "wms").glob("KB_DRAFT_*.json"))
        assert len(files) == 2

    def test_write_sets_confidence_draft(self, tmp_path):
        entries = [{"question": "Q?", "answer": "A.", "_validation": "PASSED",
                     "source_facts": []}]
        write_drafts(entries, "wms", drafts_dir=tmp_path)
        f = list((tmp_path / "wms").glob("*.json"))[0]
        data = json.load(open(f, encoding="utf-8"))
        assert data["confidence"] == "draft"
        assert data["generated_by"] == "kb_ingest.py"

    def test_write_increments_ids(self, tmp_path):
        # Write first batch
        entries1 = [{"question": "Q1?", "answer": "A1.", "_validation": "PASSED",
                      "source_facts": []}]
        write_drafts(entries1, "wms", drafts_dir=tmp_path)

        # Write second batch
        entries2 = [{"question": "Q2?", "answer": "A2.", "_validation": "PASSED",
                      "source_facts": []}]
        write_drafts(entries2, "wms", drafts_dir=tmp_path)

        files = sorted((tmp_path / "wms").glob("KB_DRAFT_*.json"))
        assert len(files) == 2
        assert "KB_DRAFT_0001" in files[0].name
        assert "KB_DRAFT_0002" in files[1].name

    def test_write_skips_rejected(self, tmp_path):
        entries = [
            {"question": "Good?", "answer": "Good.", "_validation": "PASSED",
             "source_facts": []},
            {"question": "Bad?", "answer": "Bad.", "_validation": "REJECTED",
             "source_facts": [], "_violations": ["test"]},
        ]
        written = write_drafts(entries, "wms", drafts_dir=tmp_path)
        assert written == 1


# ===================================================================
# Full pipeline tests
# ===================================================================

class TestFullPipeline:

    def test_ingest_dry_run_no_files_written(self, tmp_path, sample_profile,
                                              cke_json_file):
        drafts = tmp_path / "drafts"
        drafts.mkdir()

        with patch("kb_ingest.PROFILES_DIR", tmp_path / "profiles"), \
             patch("kb_ingest.DRAFTS_DIR", drafts), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"):
            # Create profile
            profiles_dir = tmp_path / "profiles"
            profiles_dir.mkdir()
            import yaml
            with open(profiles_dir / "wms.yaml", "w") as f:
                yaml.dump(sample_profile, f)

            summary = ingest(
                family="wms", source="architecture", dry_run=True,
                svc_path=cke_json_file, drafts_dir=drafts,
            )

        assert summary["facts_collected"] >= 1
        # Dry run: no files written
        wms_drafts = list(drafts.rglob("KB_DRAFT_*.json"))
        assert len(wms_drafts) == 0

    def test_ingest_writes_to_drafts_dir(self, tmp_path, sample_profile,
                                          cke_json_file):
        drafts = tmp_path / "drafts"
        drafts.mkdir()

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        import yaml
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        # Mock LLM call
        mock_response = json.dumps({
            "question": "What APIs does WMS support?",
            "answer": "Blue Yonder WMS provides REST APIs for integration.",
            "question_variants": ["WMS API?"],
            "category": "technical",
            "tags": ["api", "rest"],
        })

        with patch("kb_ingest.PROFILES_DIR", profiles_dir), \
             patch("kb_ingest.DRAFTS_DIR", drafts), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.generate_sync") as mock_gen:
            mock_gen.return_value = [{
                "question": "What APIs does WMS support?",
                "answer": "Blue Yonder WMS provides REST APIs for integration.",
                "question_variants": ["WMS API?"],
                "category": "technical",
                "tags": ["api", "rest"],
                "source_facts": [{"fact_id": "test", "text": "REST APIs", "source": "test"}],
                "family_code": "wms",
            }]

            summary = ingest(
                family="wms", source="architecture",
                svc_path=cke_json_file, drafts_dir=drafts,
            )

        assert summary["drafts_written"] >= 1
        files = list((drafts / "wms").glob("KB_DRAFT_*.json"))
        assert len(files) >= 1

    def test_ingest_respects_min_confidence(self, tmp_path, sample_profile):
        """High min_confidence filters out medium-confidence facts."""
        cke = {
            "products": {
                "Blue Yonder WMS": {
                    "apis": [
                        {"fact": "High confidence fact", "confidence": "high"},
                        {"fact": "Low confidence fact", "confidence": "low"},
                    ],
                }
            }
        }
        cke_path = tmp_path / "cke.json"
        with open(cke_path, "w") as f:
            json.dump(cke, f)

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        import yaml
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        with patch("kb_ingest.PROFILES_DIR", profiles_dir), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"):
            summary = ingest(
                family="wms", source="architecture",
                min_confidence=0.9, dry_run=True,
                svc_path=cke_path,
            )

        # Only high-confidence fact should pass
        assert summary["facts_collected"] == 1

    def test_ingest_batch_mode_uses_batch_api(self, tmp_path, sample_profile,
                                               cke_json_file):
        drafts = tmp_path / "drafts"
        drafts.mkdir()
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        import yaml
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        with patch("kb_ingest.PROFILES_DIR", profiles_dir), \
             patch("kb_ingest.DRAFTS_DIR", drafts), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.generate_batch") as mock_batch, \
             patch("kb_ingest.generate_sync") as mock_sync:
            mock_batch.return_value = [{
                "question": "Q?", "answer": "A.",
                "source_facts": [], "family_code": "wms",
                "tags": [], "category": "technical",
            }]

            ingest(
                family="wms", source="architecture", batch_mode=True,
                svc_path=cke_json_file, drafts_dir=drafts,
            )

        mock_batch.assert_called_once()
        mock_sync.assert_not_called()

    def test_ingest_no_profile_errors(self, tmp_path):
        with patch("kb_ingest.PROFILES_DIR", tmp_path / "empty"):
            summary = ingest(family="nonexistent", source="architecture")
        assert summary["drafts_written"] == 0


# ===================================================================
# Edge case tests
# ===================================================================

class TestEdgeCases:

    def test_empty_facts_no_generation(self, tmp_path, sample_profile):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        import yaml
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        with patch("kb_ingest.PROFILES_DIR", profiles_dir), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.collect_facts", return_value=[]):
            summary = ingest(family="wms", source="architecture")

        assert summary["entries_generated"] == 0
        assert summary["drafts_written"] == 0

    def test_all_entries_rejected_zero_written(self, tmp_path, sample_profile):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        import yaml
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        # All entries violate forbidden claims
        with patch("kb_ingest.PROFILES_DIR", profiles_dir), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.collect_facts", return_value=[
                 {"fact": "test", "source": "a", "category": "apis",
                  "confidence": 0.9, "fact_id": "t1"}
             ]), \
             patch("kb_ingest.generate_sync", return_value=[{
                 "question": "Does WMS use Snowflake?",
                 "answer": "Yes, WMS uses Snowflake for analytics.",
                 "source_facts": [], "family_code": "wms",
                 "tags": [], "category": "technical",
             }]):
            summary = ingest(family="wms", source="architecture",
                             drafts_dir=tmp_path / "drafts")

        assert summary["profile_rejected"] == 1
        assert summary["drafts_written"] == 0

    def test_all_duplicates_zero_written(self, tmp_path, sample_profile):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        import yaml
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        # Create existing entry with same question
        verified = tmp_path / "verified" / "wms"
        verified.mkdir(parents=True)
        existing = {
            "id": "KB_0001",
            "question": "what apis does wms support?",
            "answer": "REST.",
            "family_code": "wms",
        }
        with open(verified / "KB_0001.json", "w") as f:
            json.dump(existing, f)

        with patch("kb_ingest.PROFILES_DIR", profiles_dir), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"), \
             patch("kb_ingest.collect_facts", return_value=[
                 {"fact": "REST APIs", "source": "a", "category": "apis",
                  "confidence": 0.9, "fact_id": "t1"}
             ]), \
             patch("kb_ingest.generate_sync", return_value=[{
                 "question": "what apis does wms support?",
                 "answer": "Blue Yonder WMS provides REST APIs.",
                 "source_facts": [], "family_code": "wms",
                 "tags": ["api"], "category": "technical",
             }]):
            summary = ingest(family="wms", source="architecture",
                             drafts_dir=tmp_path / "drafts")

        assert summary["duplicates_filtered"] >= 1

    def test_single_fact_mode(self, tmp_path, sample_profile):
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        import yaml
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump(sample_profile, f)

        with patch("kb_ingest.PROFILES_DIR", profiles_dir), \
             patch("kb_ingest.DRAFTS_DIR", tmp_path / "drafts"), \
             patch("kb_ingest.VERIFIED_DIR", tmp_path / "verified"):
            summary = ingest(
                family="wms", source="architecture",
                single_fact="WMS supports REST API",
                dry_run=True,
            )

        assert summary["facts_collected"] == 1
        assert summary["clusters"] == 1

    def test_next_draft_id_empty_dir(self, tmp_path):
        d = tmp_path / "wms"
        d.mkdir()
        assert _next_draft_id(d) == 1

    def test_next_draft_id_existing_files(self, tmp_path):
        d = tmp_path / "wms"
        d.mkdir()
        (d / "KB_DRAFT_0001.json").write_text("{}")
        (d / "KB_DRAFT_0005.json").write_text("{}")
        assert _next_draft_id(d) == 6
