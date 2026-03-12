"""Tests for kb_eval -- KB Health Evaluation."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_eval import (
    check_contradictions,
    check_coverage,
    check_quality,
    check_consistency,
    score_entry,
    calculate_health_score,
    save_health_snapshot,
    compare_reports,
    build_report,
    load_all_entries,
    load_all_profiles,
    CAPABILITY_TOPICS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(**overrides):
    """Build a minimal valid KB entry."""
    base = {
        "id": "KB_0001",
        "question": "How does Blue Yonder handle authentication?",
        "answer": "Blue Yonder uses SSO and SAML 2.0 for authentication across all products.",
        "question_variants": ["Auth question?"],
        "family_code": "wms",
        "category": "technical",
        "subcategory": "security",
        "tags": ["SSO", "SAML"],
        "confidence": "verified",
        "_dir": "verified",
    }
    base.update(overrides)
    return base


def _make_profile(**overrides):
    """Build a minimal product profile."""
    base = {
        "product": "wms",
        "display_name": "Blue Yonder WMS",
        "cloud_native": False,
        "uses_snowflake": False,
        "microservices": False,
        "forbidden_claims": [
            "Does NOT use Snowflake",
            "NOT cloud-native",
        ],
        "platform_services": {
            "available": ["api_management", "authentication"],
            "not_available": ["ml_studio", "bdm"],
            "coming_soon": [],
        },
        "_meta": {"status": "active"},
    }
    base.update(overrides)
    return base


# ===================================================================
# Contradiction tests
# ===================================================================

class TestContradictions:

    def test_contradiction_detected_snowflake_in_wms(self):
        entry = _make_entry(
            answer="Blue Yonder WMS leverages Snowflake for data warehousing."
        )
        profile = _make_profile(uses_snowflake=False)
        results = check_contradictions([entry], {"wms": profile})
        assert len(results) >= 1
        assert any(c["found_term"] == "snowflake" or "Snowflake" in c["found_term"]
                    for c in results)

    def test_contradiction_detected_cloud_native_false(self):
        entry = _make_entry(
            answer="WMS is a fully cloud-native microservices platform."
        )
        profile = _make_profile(cloud_native=False)
        results = check_contradictions([entry], {"wms": profile})
        field_contras = [c for c in results if c["type"] == "field_contradiction"
                         and "cloud_native" in c["claim"]]
        assert len(field_contras) >= 1

    def test_contradiction_detected_microservices_false(self):
        entry = _make_entry(
            answer="WMS is built on a microservice architecture."
        )
        profile = _make_profile(microservices=False)
        results = check_contradictions([entry], {"wms": profile})
        field_contras = [c for c in results if "microservice" in c.get("claim", "")]
        assert len(field_contras) >= 1

    def test_contradiction_detected_unavailable_service(self):
        entry = _make_entry(
            answer="WMS integrates with ml studio for predictive analytics."
        )
        profile = _make_profile()
        results = check_contradictions([entry], {"wms": profile})
        svc_contras = [c for c in results if c["type"] == "unavailable_service"]
        assert len(svc_contras) >= 1

    def test_negated_term_no_contradiction(self):
        entry = _make_entry(
            answer="Blue Yonder WMS does not use Snowflake directly."
        )
        profile = _make_profile(uses_snowflake=False)
        results = check_contradictions([entry], {"wms": profile})
        # Negated mentions should not be flagged
        snowflake_field = [c for c in results if c["type"] == "field_contradiction"
                           and "snowflake" in c["claim"]]
        assert len(snowflake_field) == 0

    def test_no_profile_no_contradiction_check(self):
        entry = _make_entry(family_code="unknown_product")
        results = check_contradictions([entry], {"wms": _make_profile()})
        assert len(results) == 0

    def test_drafts_checked_too(self):
        entry = _make_entry(
            id="KB_DRAFT_0001",
            answer="WMS uses Snowflake for analytics.",
            _dir="drafts",
        )
        profile = _make_profile(uses_snowflake=False)
        results = check_contradictions([entry], {"wms": profile})
        assert any(c["directory"] == "drafts" for c in results)


# ===================================================================
# Coverage tests
# ===================================================================

class TestCoverage:

    def test_coverage_identifies_gaps(self):
        # Entry covers security but not deployment, integration, etc.
        entry = _make_entry(
            answer="Blue Yonder uses SAML authentication and SSO for security."
        )
        profile = _make_profile()
        coverage = check_coverage([entry], {"wms": profile}, active_only=False)
        assert "wms" in coverage
        assert "security" not in coverage["wms"]["gaps"]
        assert len(coverage["wms"]["gaps"]) > 0

    def test_coverage_100_when_all_topics_covered(self):
        # Create entries covering every topic
        entries = []
        for i, (cap, keywords) in enumerate(CAPABILITY_TOPICS.items()):
            entries.append(_make_entry(
                id=f"KB_{i:04d}",
                answer=f"Blue Yonder WMS provides {keywords[0]} capabilities.",
            ))
        profile = _make_profile()
        coverage = check_coverage(entries, {"wms": profile}, active_only=False)
        assert coverage["wms"]["coverage_pct"] == 100.0
        assert len(coverage["wms"]["gaps"]) == 0

    def test_coverage_only_active_profiles(self):
        profile_draft = _make_profile(_meta={"status": "draft"})
        profile_active = _make_profile(product="logistics", _meta={"status": "active"})
        profiles = {"wms": profile_draft, "logistics": profile_active}

        entry = _make_entry(family_code="logistics",
                            answer="Blue Yonder provides REST API integration.")
        coverage = check_coverage([entry], profiles, active_only=True)
        assert "wms" not in coverage
        assert "logistics" in coverage

    def test_coverage_counts_per_capability(self):
        entries = [
            _make_entry(id="KB_0001", answer="Blue Yonder WMS REST API integration."),
            _make_entry(id="KB_0002", answer="Blue Yonder WMS SFTP integration."),
            _make_entry(id="KB_0003", answer="Blue Yonder WMS security authentication."),
        ]
        profile = _make_profile()
        coverage = check_coverage(entries, {"wms": profile}, active_only=False)
        # integration should have count >= 2
        assert coverage["wms"]["capabilities"]["integration"]["count"] >= 2


# ===================================================================
# Quality tests
# ===================================================================

class TestQuality:

    def test_quality_flags_short_answer(self):
        entry = _make_entry(answer="Yes.")
        score, issues = score_entry(entry)
        assert any("too short" in i for i in issues)

    def test_quality_flags_red_flag(self):
        entry = _make_entry(
            answer="Blue Yonder WMS supports this. See attached documentation for details."
        )
        score, issues = score_entry(entry)
        assert any("red flag" in i for i in issues)

    def test_quality_flags_deprecated_term(self):
        entry = _make_entry(
            answer="Blue Yonder WMS (formerly JDA WMS) supports warehouse management."
        )
        score, issues = score_entry(entry)
        assert any("deprecated" in i for i in issues)

    def test_quality_flags_no_branding(self):
        entry = _make_entry(
            answer="The system supports REST APIs for integration with third-party systems."
        )
        score, issues = score_entry(entry)
        assert any("branding" in i for i in issues)

    def test_quality_flags_invalid_category(self):
        entry = _make_entry(category="unknown_category")
        score, issues = score_entry(entry)
        assert any("invalid category" in i for i in issues)

    def test_quality_perfect_entry_scores_10(self):
        entry = _make_entry()  # Default entry is well-formed
        score, issues = score_entry(entry)
        assert score == 10
        assert len(issues) == 0

    def test_quality_threshold_7(self):
        entries = [
            _make_entry(id="KB_GOOD", answer="Blue Yonder WMS provides comprehensive REST APIs."),
            _make_entry(id="KB_BAD", answer="Yes.", question="Q?",
                        question_variants=[], tags=[], category="bad"),
        ]
        low = check_quality(entries, threshold=7)
        bad_ids = [q["entry_id"] for q in low]
        assert "KB_BAD" in bad_ids
        assert "KB_GOOD" not in bad_ids


# ===================================================================
# Consistency tests
# ===================================================================

class TestConsistency:

    def test_missing_required_fields_detected(self):
        entry = {"id": "KB_0001", "question": "Q?"}  # Missing answer, family_code, etc.
        issues = check_consistency([entry])
        missing_field_issues = [i for i in issues if i["type"] == "missing_fields"]
        assert len(missing_field_issues) >= 1
        assert "answer" in missing_field_issues[0]["fields"]

    def test_orphan_family_detected(self, tmp_path):
        entry = _make_entry(family_code="nonexistent_product")
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        # Create one profile
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump({"product": "wms"}, f)

        issues = check_consistency([entry], profiles_dir=profiles_dir)
        orphans = [i for i in issues if i["type"] == "orphan_family"]
        assert len(orphans) == 1
        assert orphans[0]["family"] == "nonexistent_product"

    def test_clean_entry_passes(self, tmp_path):
        entry = _make_entry()
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        with open(profiles_dir / "wms.yaml", "w") as f:
            yaml.dump({"product": "wms"}, f)

        issues = check_consistency([entry], profiles_dir=profiles_dir)
        assert len(issues) == 0


# ===================================================================
# Health score tests
# ===================================================================

class TestHealthScore:

    def test_health_score_100_clean_kb(self):
        score = calculate_health_score(
            contradictions=[],
            coverage={"wms": {"gaps": []}},
            quality=[],
            consistency=[],
        )
        assert score == 100.0

    def test_health_score_drops_per_contradiction(self):
        contras = [{"entry_id": "KB_0001", "type": "forbidden_claim"}]
        score = calculate_health_score(contras, {}, [], [])
        assert score == 90.0

    def test_health_score_drops_per_gap(self):
        coverage = {"wms": {"gaps": ["security", "compliance", "monitoring"]}}
        score = calculate_health_score([], coverage, [], [])
        assert score == 94.0  # 100 - 3*2

    def test_health_score_minimum_zero(self):
        contras = [{"type": "x"}] * 15  # -150 points
        score = calculate_health_score(contras, {}, [], [])
        assert score == 0.0

    def test_health_score_combined(self):
        contras = [{"type": "x"}]  # -10
        coverage = {"wms": {"gaps": ["security"]}}  # -2
        quality = [{"entry_id": "x"}] * 4  # -2
        consistency = [{"severity": "high"}]  # -5
        score = calculate_health_score(contras, coverage, quality, consistency)
        assert score == 81.0


# ===================================================================
# History tests
# ===================================================================

class TestHistory:

    def test_save_snapshot_creates_file(self, tmp_path):
        report = {"score": 85, "timestamp": "2026-03-12T16:00:00"}
        path = save_health_snapshot(report, health_dir=tmp_path)
        assert path.exists()
        data = json.load(open(path, encoding="utf-8"))
        assert data["score"] == 85

    def test_compare_shows_improvement(self, capsys):
        current = {"score": 90, "contradiction_count": 0,
                    "low_quality_count": 5, "entry_counts": {"total": 100},
                    "timestamp": "2026-03-13"}
        previous = {"score": 80, "contradiction_count": 2,
                     "low_quality_count": 10, "entry_counts": {"total": 90},
                     "timestamp": "2026-03-12"}
        compare_reports(current, previous)
        output = capsys.readouterr().out
        assert "+10" in output
        assert "90" in output

    def test_compare_shows_regression(self, capsys):
        current = {"score": 70, "contradiction_count": 3,
                    "low_quality_count": 15, "entry_counts": {"total": 100},
                    "timestamp": "2026-03-13"}
        previous = {"score": 85, "contradiction_count": 0,
                     "low_quality_count": 5, "entry_counts": {"total": 100},
                     "timestamp": "2026-03-12"}
        compare_reports(current, previous)
        output = capsys.readouterr().out
        assert "-15" in output


# ===================================================================
# Full report tests
# ===================================================================

class TestFullReport:

    def test_full_report_includes_all_checks(self, tmp_path):
        entries = [_make_entry()]
        profiles = {"wms": _make_profile()}

        report = build_report(entries, profiles, profiles_dir=tmp_path)
        assert "contradictions" in report or "contradiction_count" in report
        assert "coverage" in report
        assert "low_quality" in report
        assert "consistency_issues" in report
        assert "score" in report

    def test_full_report_json_output(self, tmp_path):
        entries = [_make_entry()]
        profiles = {"wms": _make_profile()}

        report = build_report(entries, profiles, profiles_dir=tmp_path)
        # Should be JSON-serializable
        json_str = json.dumps(report)
        assert isinstance(json.loads(json_str), dict)

    def test_per_family_filter_works(self, tmp_path):
        verified = tmp_path / "verified"
        for family in ["wms", "planning"]:
            d = verified / family
            d.mkdir(parents=True)
            entry = _make_entry(id=f"KB_{family}_001", family_code=family)
            with open(d / f"KB_{family}_001.json", "w") as f:
                json.dump(entry, f)

        entries = load_all_entries(family_filter="wms",
                                   verified_dir=verified,
                                   drafts_dir=tmp_path / "drafts")
        assert all(e["family_code"] == "wms" for e in entries)
        assert len(entries) == 1

    def test_single_check_mode(self, tmp_path):
        entries = [_make_entry()]
        profiles = {"wms": _make_profile()}

        report = build_report(entries, profiles, checks=["quality"],
                              profiles_dir=tmp_path)
        assert "low_quality" in report
        # Other checks should not be present
        assert "contradictions" not in report
        assert "coverage" not in report

    def test_load_entries_from_dirs(self, tmp_path):
        verified = tmp_path / "verified" / "wms"
        drafts = tmp_path / "drafts" / "wms"
        verified.mkdir(parents=True)
        drafts.mkdir(parents=True)

        v_entry = _make_entry(id="KB_0001")
        d_entry = _make_entry(id="KB_DRAFT_0001", confidence="draft")

        with open(verified / "KB_0001.json", "w") as f:
            json.dump(v_entry, f)
        with open(drafts / "KB_DRAFT_0001.json", "w") as f:
            json.dump(d_entry, f)

        entries = load_all_entries(verified_dir=tmp_path / "verified",
                                   drafts_dir=tmp_path / "drafts")
        assert len(entries) == 2
        dirs = {e["_dir"] for e in entries}
        assert "verified" in dirs
        assert "drafts" in dirs
