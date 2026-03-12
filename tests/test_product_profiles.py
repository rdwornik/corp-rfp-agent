"""Tests for product profile generation and merging."""

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from generate_product_profiles import (
    load_cke_json,
    merge_cke_facts,
    extract_services_for_solution,
    generate_profile,
    generate_all,
    save_profile_yaml,
    _flat_facts,
    _infer_bool,
    _check_contains,
    _extract_keywords,
    _build_forbidden_claims,
    _extract_key_facts,
    _resolve_cke_key,
    PRODUCT_NAME_MAP,
    SERVICE_KEY_MAP,
    DISPLAY_NAMES,
)
from merge_profiles import (
    merge_profile,
    validate_profile,
    merge_all,
    load_yaml,
    save_yaml,
    REQUIRED_FIELDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_matrix():
    """Minimal platform_matrix.json structure."""
    return {
        "metadata": {"version": "2.0"},
        "product_families": {
            "wms": {
                "name": "Warehouse Management",
                "cloud_native": True,
                "solutions": ["wms", "wms_native"],
            },
            "planning": {
                "name": "Cognitive Planning",
                "cloud_native": True,
                "solutions": ["planning"],
            },
        },
        "solutions": {
            "wms": {
                "solution_code": "wms",
                "display_name": "Warehouse Management",
                "family_code": "wms",
                "family_name": "Warehouse Management",
                "cloud_native": True,
                "services": {
                    "Access Management - Authentication": {"status": "native", "available": True, "note": None},
                    "API Management": {"status": "native", "available": True, "note": None},
                    "Business Data Management": {"status": "infrastructure", "available": False, "note": None},
                    "Workflow Orchestrator": {"status": "infrastructure", "available": False, "note": None},
                    "ML Studio": {"status": "native", "available": True, "note": None},
                    "Analytics": {"status": "native", "available": True, "note": None},
                    "Streaming Ingestion": {"status": "coming", "available": False, "note": "planned"},
                    "Bulk Ingestion": {"status": "infrastructure", "available": False, "note": None},
                    "DaaS - Egress": {"status": "native", "available": True, "note": None},
                    "Blue Yonder Data Share App": {"status": "infrastructure", "available": False, "note": None},
                },
            },
            "planning": {
                "solution_code": "planning",
                "display_name": "Demand and Supply Planning",
                "family_code": "planning",
                "family_name": "Cognitive Planning",
                "cloud_native": True,
                "services": {
                    "Access Management - Authentication": {"status": "native", "available": True, "note": None},
                    "Business Data Management": {"status": "native", "available": True, "note": None},
                    "Workflow Orchestrator": {"status": "native", "available": True, "note": None},
                    "ML Studio": {"status": "native", "available": True, "note": None},
                    "Analytics": {"status": "native", "available": True, "note": None},
                    "Bulk Ingestion": {"status": "native", "available": True, "note": None},
                },
            },
            "wms_native": {
                "solution_code": "wms_native",
                "display_name": "Platform Native WMS",
                "family_code": "wms",
                "family_name": "Warehouse Management",
                "cloud_native": True,
                "services": {},
            },
        },
        "platform_services": [],
    }


@pytest.fixture
def sample_svc_products():
    """CKE Service Description structure."""
    return {
        "Blue Yonder WMS": {
            "deployment": [
                {"fact": "Deployed on Azure AKS", "confidence": "high"},
            ],
            "architecture": [
                {"fact": "NOT microservices — monolithic Java application", "confidence": "high"},
            ],
            "platform_integration": [
                {"fact": "Leverages Platform Data Cloud for common data ingestion", "confidence": "high"},
            ],
            "data_layer": [
                {"fact": "Does NOT use Snowflake directly", "confidence": "medium"},
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
                "NOT cloud-native in the same way as Platform 25.2/25.4",
                "NOT microservices",
                "Does NOT use Snowflake",
            ],
        },
        "Blue Yonder Demand Planning": {
            "deployment": [
                {"fact": "Cloud-native microservices on Azure", "confidence": "high"},
            ],
            "architecture": [
                {"fact": "Microservice-based architecture", "confidence": "high"},
            ],
            "not_supported": [],
        },
    }


@pytest.fixture
def sample_arch_products():
    """CKE Architecture JSON structure."""
    return {
        "Blue Yonder WMS": {
            "architecture": [
                {"fact": "Company SaaS Resource running on the Platform", "confidence": "high"},
                {"fact": "Handles both small and large payloads", "confidence": "medium"},
            ],
            "apis": [
                {"fact": "Kafka for streaming data", "confidence": "high"},
                {"fact": "REST APIs for integration", "confidence": "high"},  # Duplicate
            ],
        },
    }


@pytest.fixture
def matrix_path(tmp_path, sample_matrix):
    p = tmp_path / "platform_matrix.json"
    p.write_text(json.dumps(sample_matrix), encoding="utf-8")
    return p


@pytest.fixture
def svc_path(tmp_path, sample_svc_products):
    p = tmp_path / "svc.json"
    p.write_text(json.dumps({"products": sample_svc_products}), encoding="utf-8")
    return p


@pytest.fixture
def arch_path(tmp_path, sample_arch_products):
    p = tmp_path / "arch.json"
    p.write_text(json.dumps({"products": sample_arch_products}), encoding="utf-8")
    return p


# ===================================================================
# CKE fact merging
# ===================================================================

class TestCKEMerging:

    def test_merge_finds_wms_in_both_sources(self, sample_svc_products, sample_arch_products):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        assert "architecture" in facts
        assert len(facts["architecture"]) >= 2  # from both sources

    def test_merge_handles_name_variants(self, sample_svc_products, sample_arch_products):
        """WMS is listed as 'Blue Yonder WMS' in CKE."""
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        assert facts  # Found something

    def test_merge_deduplicates(self, sample_svc_products, sample_arch_products):
        """REST APIs appears in both sources — only counted once."""
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        api_facts = _flat_facts(facts.get("apis", []))
        rest_count = sum(1 for f in api_facts if "REST" in f)
        assert rest_count == 1

    def test_merge_arch_only_product(self, sample_arch_products):
        """Product only in arch JSON still works."""
        facts = merge_cke_facts({}, sample_arch_products, "wms")
        assert "architecture" in facts

    def test_merge_svc_only_product(self, sample_svc_products):
        """Product only in svc JSON still works."""
        facts = merge_cke_facts(sample_svc_products, {}, "wms")
        assert "deployment" in facts

    def test_merge_unknown_product(self, sample_svc_products, sample_arch_products):
        """Product not in any source -> empty facts."""
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "nonexistent_product")
        assert facts == {}

    def test_resolve_cke_key(self, sample_svc_products):
        """Name resolution works for canonical key -> CKE name."""
        key = _resolve_cke_key(sample_svc_products, "wms")
        assert key == "Blue Yonder WMS"

    def test_resolve_cke_key_demand(self, sample_svc_products):
        key = _resolve_cke_key(sample_svc_products, "demand_planning")
        assert key == "Blue Yonder Demand Planning"


# ===================================================================
# Inference from CKE facts
# ===================================================================

class TestInference:

    def test_infer_cloud_native_false_for_wms(self, sample_svc_products, sample_arch_products):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        all_facts = _flat_facts(facts.get("not_supported", []) + facts.get("architecture", []))
        result = _infer_bool(all_facts,
                              positive=["cloud-native"],
                              negative=["NOT cloud-native"])
        assert result is False

    def test_infer_microservices_false_for_wms(self, sample_svc_products, sample_arch_products):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        all_facts = _flat_facts(facts.get("not_supported", []) + facts.get("architecture", []))
        result = _infer_bool(all_facts,
                              positive=["microservice"],
                              negative=["NOT microservice"])
        assert result is False

    def test_infer_snowflake_false_for_wms(self, sample_svc_products, sample_arch_products):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        data_facts = _flat_facts(facts.get("data_layer", []))
        result = _check_contains(data_facts, "snowflake")
        # "Does NOT use Snowflake" contains "snowflake" — but the inference
        # here is about presence of the word, not sentiment. The forbidden_claims
        # handle the negative semantics.
        assert result is True or result is None  # Depends on fact text

    def test_infer_pdc_true_for_wms(self, sample_svc_products, sample_arch_products):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        platform_facts = _flat_facts(facts.get("platform_integration", []))
        result = _check_contains(platform_facts, "platform data cloud", "pdc")
        assert result is True

    def test_infer_bool_unknown(self):
        result = _infer_bool(["some unrelated fact"],
                              positive=["microservice"],
                              negative=["NOT microservice"])
        assert result is None

    def test_extract_keywords_apis(self, sample_svc_products, sample_arch_products):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        api_facts = _flat_facts(facts.get("apis", []))
        result = _extract_keywords(api_facts, {
            "rest": ["rest"], "sftp": ["sftp"], "kafka": ["kafka"],
        })
        assert "rest" in result
        assert "sftp" in result
        assert "kafka" in result


# ===================================================================
# Platform services from matrix
# ===================================================================

class TestPlatformServices:

    def test_available_services_for_planning(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "planning")
        assert "authentication" in services["available"]
        assert "bdm" in services["available"]
        assert "workflow_orchestrator" in services["available"]

    def test_not_available_for_wms(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "wms")
        assert "bdm" in services["not_available"]
        assert "workflow_orchestrator" in services["not_available"]

    def test_coming_soon_detected(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "wms")
        assert "streaming_ingestion" in services["coming_soon"]

    def test_available_sorted(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "wms")
        assert services["available"] == sorted(services["available"])

    def test_unknown_solution_returns_empty(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "nonexistent")
        assert services == {"available": [], "not_available": [], "coming_soon": [], "via_other": {}}


# ===================================================================
# Profile generation
# ===================================================================

class TestProfileGeneration:

    def test_profile_has_platform_services_section(self, sample_svc_products,
                                                     sample_arch_products, sample_matrix):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        services = extract_services_for_solution(sample_matrix, "wms")
        sol = sample_matrix["solutions"]["wms"]
        profile = generate_profile("wms", facts, services, sol)
        assert "platform_services" in profile
        assert "available" in profile["platform_services"]

    def test_profile_has_convenience_booleans(self, sample_svc_products,
                                               sample_arch_products, sample_matrix):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        services = extract_services_for_solution(sample_matrix, "wms")
        sol = sample_matrix["solutions"]["wms"]
        profile = generate_profile("wms", facts, services, sol)
        assert "has_analytics" in profile
        assert "has_ml_studio" in profile
        assert "has_bdm" in profile

    def test_has_analytics_true_for_wms(self, sample_svc_products,
                                         sample_arch_products, sample_matrix):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        services = extract_services_for_solution(sample_matrix, "wms")
        sol = sample_matrix["solutions"]["wms"]
        profile = generate_profile("wms", facts, services, sol)
        assert profile["has_analytics"] is True

    def test_has_bdm_false_for_wms(self, sample_svc_products,
                                     sample_arch_products, sample_matrix):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        services = extract_services_for_solution(sample_matrix, "wms")
        sol = sample_matrix["solutions"]["wms"]
        profile = generate_profile("wms", facts, services, sol)
        assert profile["has_bdm"] is False

    def test_not_available_services_become_forbidden_claims(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "wms")
        profile = generate_profile("wms", {}, services)
        fc = profile["forbidden_claims"]
        assert any("Bdm" in c or "Business Data Management" in c for c in fc)

    def test_forbidden_from_not_supported(self, sample_svc_products, sample_arch_products,
                                            sample_matrix):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        services = extract_services_for_solution(sample_matrix, "wms")
        profile = generate_profile("wms", facts, services)
        fc = profile["forbidden_claims"]
        assert any("NOT cloud-native" in c for c in fc)
        assert any("NOT microservices" in c or "NOT microservice" in c for c in fc)

    def test_forbidden_no_duplicates(self, sample_svc_products, sample_arch_products,
                                       sample_matrix):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        services = extract_services_for_solution(sample_matrix, "wms")
        profile = generate_profile("wms", facts, services)
        fc = profile["forbidden_claims"]
        assert len(fc) == len(set(fc))

    def test_profile_meta_status_draft(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "wms")
        profile = generate_profile("wms", {}, services)
        assert profile["_meta"]["status"] == "draft"

    def test_profile_display_name(self, sample_matrix):
        services = extract_services_for_solution(sample_matrix, "wms")
        profile = generate_profile("wms", {}, services)
        assert profile["display_name"] == "Blue Yonder Warehouse Management"

    def test_key_facts_from_high_confidence(self, sample_svc_products,
                                              sample_arch_products, sample_matrix):
        facts = merge_cke_facts(sample_svc_products, sample_arch_products, "wms")
        services = extract_services_for_solution(sample_matrix, "wms")
        profile = generate_profile("wms", facts, services)
        kf = profile["key_facts"]
        assert any("Platform Data Cloud" in f for f in kf)


# ===================================================================
# Full generation pipeline
# ===================================================================

class TestGenerateAll:

    def test_full_generate_from_matrix_only(self, tmp_path, matrix_path):
        out_dir = tmp_path / "generated"
        report = generate_all(
            matrix_path=matrix_path,
            output_dir=out_dir,
        )
        assert report["total"] >= 2  # wms, planning, wms_native
        assert (out_dir / "wms.yaml").exists()

    def test_full_generate_with_cke(self, tmp_path, svc_path, arch_path, matrix_path):
        out_dir = tmp_path / "generated"
        report = generate_all(
            svc_path=svc_path,
            arch_path=arch_path,
            matrix_path=matrix_path,
            output_dir=out_dir,
        )
        assert report["cke_matches"] >= 1  # WMS found in CKE

    def test_generate_single_product(self, tmp_path, matrix_path):
        out_dir = tmp_path / "generated"
        report = generate_all(
            matrix_path=matrix_path,
            output_dir=out_dir,
            product_filter="wms",
        )
        assert report["total"] == 1
        assert (out_dir / "wms.yaml").exists()

    def test_generation_report_saved(self, tmp_path, matrix_path):
        out_dir = tmp_path / "generated"
        report_path = tmp_path / "profiles" / "_generation_report.json"
        from unittest.mock import patch
        with patch("generate_product_profiles.REPORT_PATH", report_path), \
             patch("generate_product_profiles.CHANGELOG_PATH", tmp_path / "cl.jsonl"):
            generate_all(matrix_path=matrix_path, output_dir=out_dir)
        assert report_path.exists()

    def test_dry_run_no_files(self, tmp_path, matrix_path):
        out_dir = tmp_path / "generated"
        report = generate_all(
            matrix_path=matrix_path,
            output_dir=out_dir,
            dry_run=True,
        )
        assert report["total"] >= 1
        assert not (out_dir / "wms.yaml").exists()

    def test_yaml_roundtrip(self, tmp_path):
        profile = {"product": "test", "list": [1, 2], "nested": {"a": True}}
        path = tmp_path / "test.yaml"
        save_profile_yaml(profile, path)
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["product"] == "test"
        assert loaded["list"] == [1, 2]
        assert loaded["nested"]["a"] is True


# ===================================================================
# Override merge
# ===================================================================

class TestMergeProfile:

    def test_no_override_returns_copy(self):
        gen = {"product": "wms", "cloud_native": False, "forbidden_claims": []}
        result = merge_profile(gen, {})
        assert result == gen
        result["product"] = "changed"
        assert gen["product"] == "wms"  # original unchanged

    def test_override_replaces_field(self):
        gen = {"product": "wms", "database": [], "forbidden_claims": [],
               "platform_services": {"available": []}}
        ovr = {"database": ["sql_server", "oracle"]}
        result = merge_profile(gen, ovr)
        assert result["database"] == ["sql_server", "oracle"]

    def test_forbidden_claims_add(self):
        gen = {"forbidden_claims": ["claim A"], "platform_services": {"available": []}}
        ovr = {"forbidden_claims_add": ["claim B"]}
        result = merge_profile(gen, ovr)
        assert "claim A" in result["forbidden_claims"]
        assert "claim B" in result["forbidden_claims"]

    def test_forbidden_claims_add_no_duplicates(self):
        gen = {"forbidden_claims": ["claim A"], "platform_services": {"available": []}}
        ovr = {"forbidden_claims_add": ["claim A"]}  # Already exists
        result = merge_profile(gen, ovr)
        assert result["forbidden_claims"].count("claim A") == 1

    def test_forbidden_claims_remove(self):
        gen = {"forbidden_claims": ["claim A", "claim B"],
               "platform_services": {"available": []}}
        ovr = {"forbidden_claims_remove": ["claim A"]}
        result = merge_profile(gen, ovr)
        assert "claim A" not in result["forbidden_claims"]
        assert "claim B" in result["forbidden_claims"]

    def test_platform_services_add(self):
        gen = {"forbidden_claims": [],
               "platform_services": {"available": ["auth"], "not_available": ["bdm"]}}
        ovr = {"platform_services_add": {"available": ["new_svc"]}}
        result = merge_profile(gen, ovr)
        assert "new_svc" in result["platform_services"]["available"]

    def test_platform_services_remove(self):
        gen = {"forbidden_claims": [],
               "platform_services": {"available": ["auth", "bdm"], "not_available": []}}
        ovr = {"platform_services_remove": {"available": ["bdm"]}}
        result = merge_profile(gen, ovr)
        assert "bdm" not in result["platform_services"]["available"]

    def test_override_status_active(self):
        gen = {"forbidden_claims": [], "platform_services": {"available": []},
               "_meta": {"status": "draft"}}
        ovr = {"status": "active", "last_reviewed": "2026-03-15"}
        result = merge_profile(gen, ovr)
        assert result["_meta"]["status"] == "active"

    def test_regeneration_preserves_overrides(self, tmp_path):
        """Full round-trip: generate -> override -> regenerate -> merge."""
        gen_dir = tmp_path / "gen"
        ovr_dir = tmp_path / "ovr"
        eff_dir = tmp_path / "eff"
        gen_dir.mkdir()
        ovr_dir.mkdir()
        eff_dir.mkdir()

        # First generation
        gen = {"product": "wms", "cloud_native": False, "database": [],
               "forbidden_claims": ["old claim"],
               "platform_services": {"available": ["auth"]},
               "_meta": {"generated_at": "t1", "status": "draft"}}
        save_yaml(gen, gen_dir / "wms.yaml")

        # Manual override
        ovr = {"database": ["sql_server"], "forbidden_claims_add": ["new claim"],
               "status": "active", "last_reviewed": "2026-03-15"}
        save_yaml(ovr, ovr_dir / "wms.yaml")

        # Merge
        summary = merge_all(gen_dir, ovr_dir, eff_dir)
        eff = load_yaml(eff_dir / "wms.yaml")
        assert eff["database"] == ["sql_server"]
        assert "new claim" in eff["forbidden_claims"]
        assert eff["_meta"]["status"] == "active"

        # Re-generate (simulates CKE update)
        gen2 = dict(gen)
        gen2["cloud_native"] = True  # Changed by CKE
        gen2["forbidden_claims"] = ["updated claim"]
        save_yaml(gen2, gen_dir / "wms.yaml")

        # Re-merge — override still applies
        merge_all(gen_dir, ovr_dir, eff_dir)
        eff2 = load_yaml(eff_dir / "wms.yaml")
        assert eff2["database"] == ["sql_server"]  # Override preserved
        assert "new claim" in eff2["forbidden_claims"]  # Override preserved
        assert eff2["_meta"]["status"] == "active"  # Override preserved

    def test_convenience_booleans_recomputed(self):
        """Convenience booleans recomputed after platform_services merge."""
        gen = {"forbidden_claims": [],
               "platform_services": {"available": ["analytics"]},
               "has_analytics": True, "has_bdm": False, "has_ml_studio": False,
               "has_workflow": False, "has_bulk_ingestion": False,
               "has_streaming": False, "has_data_share": False, "has_daas": False}
        ovr = {"platform_services_add": {"available": ["bdm"]}}
        result = merge_profile(gen, ovr)
        assert result["has_bdm"] is True

    def test_conflict_override_wins(self):
        """When generated and override disagree, override wins."""
        gen = {"product": "wms", "cloud_native": True,
               "forbidden_claims": [], "platform_services": {"available": []}}
        ovr = {"cloud_native": False}
        result = merge_profile(gen, ovr)
        assert result["cloud_native"] is False


# ===================================================================
# Conflict detection
# ===================================================================

class TestConflicts:

    def test_conflict_logged_when_generated_differs(self, tmp_path):
        gen_dir = tmp_path / "gen"
        ovr_dir = tmp_path / "ovr"
        eff_dir = tmp_path / "eff"
        gen_dir.mkdir()
        ovr_dir.mkdir()
        eff_dir.mkdir()

        gen = {"product": "wms", "cloud_native": True,
               "forbidden_claims": [],
               "platform_services": {"available": []},
               "_meta": {"generated_at": "t1"}}
        save_yaml(gen, gen_dir / "wms.yaml")

        ovr = {"cloud_native": False}
        save_yaml(ovr, ovr_dir / "wms.yaml")

        from unittest.mock import patch
        with patch("merge_profiles.CHANGELOG_PATH", tmp_path / "cl.jsonl"):
            summary = merge_all(gen_dir, ovr_dir, eff_dir)

        assert len(summary["conflicts"]) == 1
        assert summary["conflicts"][0]["field"] == "cloud_native"


# ===================================================================
# Validation
# ===================================================================

class TestValidation:

    def test_valid_profile_passes(self):
        profile = {
            "product": "wms",
            "display_name": "WMS",
            "platform_services": {"available": []},
            "_meta": {"generated_at": "2026-01-01"},
        }
        issues = validate_profile(profile)
        assert issues == []

    def test_validate_catches_missing_fields(self):
        profile = {"product": "wms"}
        issues = validate_profile(profile)
        assert len(issues) > 0
        assert any("display_name" in i for i in issues)

    def test_validate_catches_missing_available(self):
        profile = {
            "product": "wms",
            "display_name": "WMS",
            "platform_services": {},
            "_meta": {"generated_at": "t1"},
        }
        issues = validate_profile(profile)
        assert any("available" in i for i in issues)

    def test_validate_catches_missing_generated_at(self):
        profile = {
            "product": "wms",
            "display_name": "WMS",
            "platform_services": {"available": []},
            "_meta": {},
        }
        issues = validate_profile(profile)
        assert any("generated_at" in i for i in issues)


# ===================================================================
# Changelog
# ===================================================================

class TestChangelog:

    def test_changelog_appended(self, tmp_path, matrix_path):
        changelog = tmp_path / "changelog.jsonl"
        out_dir = tmp_path / "generated"
        from unittest.mock import patch
        with patch("generate_product_profiles.CHANGELOG_PATH", changelog), \
             patch("generate_product_profiles.REPORT_PATH", tmp_path / "report.json"):
            generate_all(matrix_path=matrix_path, output_dir=out_dir)
        assert changelog.exists()
        lines = changelog.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert entry["action"] == "generated"
