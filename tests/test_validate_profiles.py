"""Tests for validate_profiles -- product profile validation."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validate_profiles import (
    validate_profile,
    build_auto_fix,
    save_override,
    validate_all,
    ERROR,
    WARNING,
    SUSPICIOUS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(**overrides):
    """Build a minimal valid profile with optional overrides."""
    base = {
        "product": "test_product",
        "display_name": "Test Product",
        "cloud_native": False,
        "uses_snowflake": False,
        "microservices": False,
        "multi_tenant": True,
        "database": "PostgreSQL",
        "security_protocols": ["TLS 1.3"],
        "deployment": ["SaaS"],
        "has_analytics": False,
        "has_ml_studio": False,
        "has_bdm": False,
        "has_workflow": False,
        "has_bulk_ingestion": False,
        "has_streaming": False,
        "has_data_share": False,
        "has_daas": False,
        "forbidden_claims": [],
        "key_facts": [],
        "platform_services": {
            "available": [],
            "not_available": [],
            "coming_soon": [],
        },
    }
    base.update(overrides)
    return base


# ===================================================================
# 1. Contradiction: cloud_native vs forbidden
# ===================================================================

class TestContradictionCloudNative:

    def test_cloud_native_true_vs_forbidden(self):
        profile = _make_profile(
            cloud_native=True,
            forbidden_claims=["Product is NOT cloud-native"],
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "cloud_native"]
        assert len(errors) >= 1
        assert "contradicts" in errors[0]["message"].lower()

    def test_cloud_native_false_no_contradiction(self):
        profile = _make_profile(
            cloud_native=False,
            forbidden_claims=["Product is NOT cloud-native"],
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "cloud_native"]
        assert len(errors) == 0


# ===================================================================
# 2. Contradiction: snowflake vs forbidden
# ===================================================================

class TestContradictionSnowflake:

    def test_uses_snowflake_true_vs_forbidden(self):
        profile = _make_profile(
            uses_snowflake=True,
            forbidden_claims=["Does NOT use Snowflake"],
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "uses_snowflake"]
        assert len(errors) >= 1

    def test_uses_snowflake_false_no_error(self):
        profile = _make_profile(
            uses_snowflake=False,
            forbidden_claims=["Does NOT use Snowflake"],
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "uses_snowflake"]
        assert len(errors) == 0


# ===================================================================
# 3. Contradiction: has_X vs not_available
# ===================================================================

class TestContradictionHasXNotAvailable:

    def test_has_analytics_true_but_not_available(self):
        profile = _make_profile(
            has_analytics=True,
            platform_services={
                "available": [],
                "not_available": ["analytics"],
                "coming_soon": [],
            },
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "has_analytics"]
        assert len(errors) >= 1
        assert "not_available" in errors[0]["message"]

    def test_has_bdm_true_and_available_no_error(self):
        profile = _make_profile(
            has_bdm=True,
            platform_services={
                "available": ["bdm"],
                "not_available": [],
                "coming_soon": [],
            },
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "has_bdm"]
        assert len(errors) == 0

    def test_has_workflow_true_but_not_available(self):
        profile = _make_profile(
            has_workflow=True,
            platform_services={
                "available": [],
                "not_available": ["workflow_orchestrator"],
                "coming_soon": [],
            },
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "has_workflow"]
        assert len(errors) >= 1


# ===================================================================
# 4. Missing data warned
# ===================================================================

class TestMissingData:

    def test_empty_database_warns(self):
        profile = _make_profile(database=None)
        issues = validate_profile(profile)
        warnings = [i for i in issues if i["level"] == WARNING and i["field"] == "database"]
        assert len(warnings) == 1

    def test_empty_security_warns(self):
        profile = _make_profile(security_protocols=[])
        issues = validate_profile(profile)
        warnings = [i for i in issues if i["level"] == WARNING and i["field"] == "security_protocols"]
        assert len(warnings) == 1

    def test_empty_deployment_warns(self):
        profile = _make_profile(deployment=None)
        issues = validate_profile(profile)
        warnings = [i for i in issues if i["level"] == WARNING and i["field"] == "deployment"]
        assert len(warnings) == 1

    def test_multi_tenant_none_warns(self):
        profile = _make_profile(multi_tenant=None)
        issues = validate_profile(profile)
        warnings = [i for i in issues if i["level"] == WARNING and i["field"] == "multi_tenant"]
        assert len(warnings) == 1

    def test_filled_fields_no_missing_warning(self):
        profile = _make_profile()
        issues = validate_profile(profile)
        warnings = [i for i in issues if i["level"] == WARNING]
        assert len(warnings) == 0


# ===================================================================
# 5. Suspicious inference flagged
# ===================================================================

class TestSuspiciousInference:

    def test_cloud_native_true_no_key_fact_support(self):
        profile = _make_profile(
            cloud_native=True,
            key_facts=["Uses PostgreSQL database", "REST API available"],
        )
        issues = validate_profile(profile)
        suspicious = [i for i in issues if i["level"] == SUSPICIOUS and i["field"] == "cloud_native"]
        assert len(suspicious) == 1

    def test_cloud_native_true_with_key_fact_support(self):
        profile = _make_profile(
            cloud_native=True,
            key_facts=["Built as cloud-native microservice architecture"],
        )
        issues = validate_profile(profile)
        suspicious = [i for i in issues if i["level"] == SUSPICIOUS and i["field"] == "cloud_native"]
        assert len(suspicious) == 0

    def test_cloud_native_true_empty_key_facts_no_suspicious(self):
        """If key_facts is empty, it's missing data, not suspicious."""
        profile = _make_profile(cloud_native=True, key_facts=[])
        issues = validate_profile(profile)
        suspicious = [i for i in issues if i["level"] == SUSPICIOUS]
        assert len(suspicious) == 0


# ===================================================================
# 6. Platform service consistency
# ===================================================================

class TestPlatformServiceConsistency:

    def test_service_in_both_available_and_not_available(self):
        profile = _make_profile(
            platform_services={
                "available": ["analytics", "bdm"],
                "not_available": ["analytics"],
                "coming_soon": [],
            },
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR
                  and i["field"] == "platform_services"
                  and "analytics" in i["message"]]
        assert len(errors) == 1

    def test_service_in_available_and_coming_soon(self):
        profile = _make_profile(
            platform_services={
                "available": ["ml_studio"],
                "not_available": [],
                "coming_soon": ["ml_studio"],
            },
        )
        issues = validate_profile(profile)
        warnings = [i for i in issues if i["level"] == WARNING
                    and "ml_studio" in i["message"]
                    and "coming_soon" in i["message"]]
        assert len(warnings) == 1

    def test_no_overlap_no_error(self):
        profile = _make_profile(
            platform_services={
                "available": ["analytics"],
                "not_available": ["bdm"],
                "coming_soon": ["ml_studio"],
            },
        )
        issues = validate_profile(profile)
        ps_errors = [i for i in issues if i["field"] == "platform_services"]
        assert len(ps_errors) == 0


# ===================================================================
# 7. Auto-fix generates correct override
# ===================================================================

class TestAutoFix:

    def test_auto_fix_contradiction_sets_false(self):
        profile = _make_profile(
            cloud_native=True,
            forbidden_claims=["Product is NOT cloud-native"],
        )
        issues = validate_profile(profile)
        fix = build_auto_fix(profile, issues)
        assert fix is not None
        assert fix["cloud_native"] is False

    def test_auto_fix_has_x_sets_false_and_removes_service(self):
        profile = _make_profile(
            has_analytics=True,
            platform_services={
                "available": ["analytics"],
                "not_available": ["analytics"],
                "coming_soon": [],
            },
        )
        issues = validate_profile(profile)
        fix = build_auto_fix(profile, issues)
        assert fix is not None
        assert fix["has_analytics"] is False
        assert "analytics" in fix["platform_services_remove"]["available"]

    def test_auto_fix_no_errors_returns_none(self):
        profile = _make_profile()
        issues = validate_profile(profile)
        fix = build_auto_fix(profile, issues)
        assert fix is None

    def test_auto_fix_includes_metadata(self):
        profile = _make_profile(
            uses_snowflake=True,
            forbidden_claims=["Does NOT use Snowflake"],
        )
        issues = validate_profile(profile)
        fix = build_auto_fix(profile, issues)
        assert fix is not None
        assert "review_notes" in fix
        assert "last_reviewed" in fix

    def test_save_override_creates_file(self, tmp_path):
        override = {"cloud_native": False, "review_notes": "test", "last_reviewed": "2026-03-12"}
        path = save_override("test_product", override, tmp_path)
        assert path.exists()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["cloud_native"] is False

    def test_save_override_merges_existing(self, tmp_path):
        # Write first override
        first = {"microservices": False, "review_notes": "first", "last_reviewed": "2026-03-01"}
        save_override("test_product", first, tmp_path)

        # Write second override — should merge
        second = {"cloud_native": False, "review_notes": "second", "last_reviewed": "2026-03-12"}
        path = save_override("test_product", second, tmp_path)

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["microservices"] is False
        assert data["cloud_native"] is False
        assert data["review_notes"] == "second"


# ===================================================================
# 8. Clean profile passes validation
# ===================================================================

class TestCleanProfile:

    def test_clean_profile_no_issues(self):
        profile = _make_profile()
        issues = validate_profile(profile)
        assert len(issues) == 0

    def test_clean_profile_with_services(self):
        profile = _make_profile(
            has_analytics=True,
            has_bdm=True,
            platform_services={
                "available": ["analytics", "bdm"],
                "not_available": ["ml_studio"],
                "coming_soon": ["streaming_ingestion"],
            },
        )
        issues = validate_profile(profile)
        assert len(issues) == 0


# ===================================================================
# 9. Already-overridden profile not re-flagged
# ===================================================================

class TestAlreadyOverridden:

    def test_overridden_false_not_flagged(self):
        """If cloud_native was already fixed to False via override, no error."""
        profile = _make_profile(
            cloud_native=False,
            forbidden_claims=["Product is NOT cloud-native"],
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR]
        assert len(errors) == 0

    def test_overridden_service_removed_not_flagged(self):
        """If has_analytics was fixed to False and service removed, no error."""
        profile = _make_profile(
            has_analytics=False,
            platform_services={
                "available": [],
                "not_available": ["analytics"],
                "coming_soon": [],
            },
        )
        issues = validate_profile(profile)
        errors = [i for i in issues if i["level"] == ERROR and i["field"] == "has_analytics"]
        assert len(errors) == 0

    def test_auto_fix_skips_already_fixed(self):
        """build_auto_fix returns None for profile with no remaining errors."""
        profile = _make_profile(
            cloud_native=False,
            uses_snowflake=False,
            forbidden_claims=["Product is NOT cloud-native", "Does NOT use Snowflake"],
        )
        issues = validate_profile(profile)
        fix = build_auto_fix(profile, issues)
        assert fix is None


# ===================================================================
# validate_all with temp directory
# ===================================================================

class TestValidateAll:

    def test_validate_all_scans_directory(self, tmp_path):
        # Write two profiles
        clean = _make_profile(product="clean_prod")
        broken = _make_profile(
            product="broken_prod",
            cloud_native=True,
            forbidden_claims=["NOT cloud-native"],
        )

        for name, data in [("clean_prod.yaml", clean), ("broken_prod.yaml", broken)]:
            with open(tmp_path / name, "w", encoding="utf-8") as f:
                yaml.dump(data, f)

        results = validate_all(effective_dir=tmp_path)
        assert "clean_prod" in results
        assert "broken_prod" in results
        assert len(results["clean_prod"]) == 0
        assert len(results["broken_prod"]) >= 1

    def test_validate_all_product_filter(self, tmp_path):
        p1 = _make_profile(product="alpha")
        p2 = _make_profile(product="beta", database=None)

        for name, data in [("alpha.yaml", p1), ("beta.yaml", p2)]:
            with open(tmp_path / name, "w", encoding="utf-8") as f:
                yaml.dump(data, f)

        results = validate_all(effective_dir=tmp_path, product_filter="beta")
        assert "alpha" not in results
        assert "beta" in results

    def test_validate_all_empty_dir(self, tmp_path):
        results = validate_all(effective_dir=tmp_path)
        assert results == {}
