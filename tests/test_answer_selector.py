"""Tests for answer_selector -- 5-stage answer selection algorithm."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from answer_selector import (
    _count_red_flags,
    _count_deprecated,
    apply_hard_gates,
    get_similarity_action,
    score_answer,
    make_decision,
    llm_topic_check,
    llm_judge,
    select_answer,
    _parse_llm_json_obj,
    print_improve_report,
    save_improve_report,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

CLEAN_ANSWER = (
    "Blue Yonder's SaaS platform runs on Azure Kubernetes Service (AKS) "
    "with 99.97% uptime SLA. Authentication uses SAML/SSO via OAuth2. "
    "The platform is SOC 2 Type II and ISO 27001 certified."
)

GENERIC_ANSWER = "We provide a supply chain solution with various features."

RED_FLAG_ANSWER = "Please see the attached document for details. Refer to appendix B."

DEPRECATED_ANSWER = (
    "JDA Software provides Luminate Planning for demand forecasting. "
    "The legacy i2 Technologies platform handles optimization."
)

WALL_OF_TEXT = "A" * 600  # >500 chars, no newlines


# ---------------------------------------------------------------------------
# Stage 0: Hard Gates
# ---------------------------------------------------------------------------

class TestHardGates:
    def test_red_flag_new_keeps_existing(self):
        """New answer with red flags -> KEEP_EXISTING."""
        result = apply_hard_gates(CLEAN_ANSWER, RED_FLAG_ANSWER)
        assert result == "KEEP_EXISTING"

    def test_red_flag_existing_replaces(self):
        """Existing answer with red flags -> REPLACE."""
        result = apply_hard_gates(RED_FLAG_ANSWER, CLEAN_ANSWER)
        assert result == "REPLACE"

    def test_deprecated_in_new_keeps_existing(self):
        """New answer with deprecated terms -> KEEP_EXISTING."""
        result = apply_hard_gates(CLEAN_ANSWER, DEPRECATED_ANSWER)
        assert result == "KEEP_EXISTING"

    def test_deprecated_in_existing_replaces(self):
        """Existing with deprecated terms, clean new -> REPLACE."""
        result = apply_hard_gates(DEPRECATED_ANSWER, CLEAN_ANSWER)
        assert result == "REPLACE"

    def test_client_name_in_new_flagged(self):
        """Client name leakage in new answer adds flags."""
        new = "Acme Corp uses Blue Yonder for demand planning."
        result = apply_hard_gates(CLEAN_ANSWER, new, client_name="Acme Corp")
        assert result == "KEEP_EXISTING"

    def test_no_flags_continues(self):
        """Two clean answers -> None (continue to scoring)."""
        result = apply_hard_gates(CLEAN_ANSWER, GENERIC_ANSWER)
        assert result is None

    def test_both_have_flags_continues(self):
        """Both answers have flags -> None (no clear winner)."""
        result = apply_hard_gates(RED_FLAG_ANSWER, RED_FLAG_ANSWER)
        assert result is None

    def test_count_red_flags(self):
        """Red flag counter works."""
        assert _count_red_flags("see attached document") >= 1
        assert _count_red_flags("TBD") >= 1
        assert _count_red_flags("N/A") >= 1
        assert _count_red_flags("Blue Yonder platform") == 0

    def test_count_deprecated(self):
        """Deprecated term counter works."""
        assert _count_deprecated("JDA Software solution") >= 1
        assert _count_deprecated("Luminate Planning") >= 1
        assert _count_deprecated("Blue Yonder platform") == 0


# ---------------------------------------------------------------------------
# Stage 1: Similarity Bucketing
# ---------------------------------------------------------------------------

class TestSimilarityBucketing:
    def test_high_similarity_compares(self):
        assert get_similarity_action(0.90) == "COMPARE"
        assert get_similarity_action(0.85) == "COMPARE"

    def test_mid_similarity_topic_check(self):
        assert get_similarity_action(0.80) == "TOPIC_CHECK"
        assert get_similarity_action(0.70) == "TOPIC_CHECK"

    def test_low_similarity_adds_new(self):
        assert get_similarity_action(0.65) == "ADD_NEW"
        assert get_similarity_action(0.30) == "ADD_NEW"

    def test_boundary_085(self):
        """Exactly 0.85 -> COMPARE."""
        assert get_similarity_action(0.85) == "COMPARE"

    def test_boundary_070(self):
        """Exactly 0.70 -> TOPIC_CHECK."""
        assert get_similarity_action(0.70) == "TOPIC_CHECK"


# ---------------------------------------------------------------------------
# Stage 2: Heuristic Scoring
# ---------------------------------------------------------------------------

class TestScoring:
    def test_specific_answer_scores_higher(self):
        """Answer with BY terms scores higher than generic."""
        specific = score_answer(CLEAN_ANSWER)
        generic = score_answer(GENERIC_ANSWER)
        assert specific["total"] > generic["total"]
        assert specific["specificity"] > 0

    def test_concrete_details_boost(self):
        """Percentages and SLAs add points."""
        answer = "We guarantee 99.97% uptime with RTO < 4 hours and SOC 2 compliance."
        scores = score_answer(answer)
        assert scores["concrete_details"] > 0

    def test_structured_answer_bonus(self):
        """Answer with bullet points gets +2."""
        answer = "Key features:\n1. Real-time forecasting\n2. ML optimization\n3. Cloud-native"
        scores = score_answer(answer)
        assert scores["structure"] == 2

    def test_modern_terms_bonus(self):
        """Cloud-native/microservices/SaaS get +2."""
        answer = "Our cloud-native microservices architecture runs on Kubernetes."
        scores = score_answer(answer)
        assert scores["currency"] == 2

    def test_wall_of_text_penalty(self):
        """>500 chars without newlines gets -1."""
        scores = score_answer(WALL_OF_TEXT)
        assert scores["structure"] == -1

    def test_deprecated_terms_penalty(self):
        """JDA/Luminate terms get -3 each."""
        scores = score_answer(DEPRECATED_ANSWER)
        assert scores["deprecated"] < 0

    def test_red_flags_penalty(self):
        """Red flags give -5 each."""
        scores = score_answer(RED_FLAG_ANSWER)
        assert scores["red_flags"] < 0

    def test_total_is_sum_of_components(self):
        """Total equals sum of all component scores."""
        scores = score_answer(CLEAN_ANSWER)
        components = {k: v for k, v in scores.items() if k != "total"}
        assert scores["total"] == sum(components.values())


# ---------------------------------------------------------------------------
# Stage 3: Decision Logic
# ---------------------------------------------------------------------------

class TestDecisionLogic:
    def test_clear_winner_replaces(self):
        """New scoring much higher -> REPLACE."""
        result = make_decision(GENERIC_ANSWER, CLEAN_ANSWER, similarity=0.90)
        assert result["decision"] == "REPLACE"
        assert result["stage"] == "scoring"

    def test_clear_loser_keeps(self):
        """Existing scoring much higher -> KEEP_EXISTING."""
        result = make_decision(CLEAN_ANSWER, GENERIC_ANSWER, similarity=0.90)
        assert result["decision"] == "KEEP_EXISTING"
        assert result["stage"] == "scoring"

    def test_tie_goes_to_llm(self):
        """Similar scores -> LLM_JUDGE."""
        # Two similar-quality answers
        a = "Blue Yonder provides demand planning on Azure."
        b = "Blue Yonder offers supply planning on Azure."
        result = make_decision(a, b, similarity=0.90)
        assert result["decision"] == "LLM_JUDGE"

    def test_low_similarity_adds_new(self):
        """< 0.70 similarity -> ADD_NEW regardless of scores."""
        result = make_decision(CLEAN_ANSWER, GENERIC_ANSWER, similarity=0.50)
        assert result["decision"] == "ADD_NEW"
        assert result["stage"] == "similarity"

    def test_hard_gate_overrides_all(self):
        """Red flags trigger before scoring."""
        result = make_decision(CLEAN_ANSWER, RED_FLAG_ANSWER, similarity=0.90)
        assert result["decision"] == "KEEP_EXISTING"
        assert result["stage"] == "gate"

    def test_recency_bonus_applied(self):
        """Newer answer gets +2 recency bonus."""
        a = "Blue Yonder platform runs on Azure."
        b = "Blue Yonder platform runs on Azure."
        result = make_decision(a, b, similarity=0.90,
                               existing_date="2023-01-01", new_date="2025-06-01")
        new_scores = result["scores"]["new"]
        assert new_scores.get("recency", 0) == 2

    def test_topic_check_flagged(self):
        """Mid-similarity (0.70-0.85) flags needs_topic_check."""
        result = make_decision(CLEAN_ANSWER, GENERIC_ANSWER, similarity=0.75)
        assert result.get("needs_topic_check") is True


# ---------------------------------------------------------------------------
# Stage 4: LLM Judge
# ---------------------------------------------------------------------------

class TestLLMJudge:
    def test_topic_guard_different_adds_new(self):
        """LLM says not same topic -> ADD_NEW."""
        def mock_llm(prompt):
            return '{"same_topic": false, "confidence": 9, "reason": "different topics"}'

        result = llm_topic_check("What DB?", "How handle backups?", mock_llm)
        assert result["same_topic"] is False

    def test_topic_guard_same_continues(self):
        """LLM says same topic -> same_topic=True."""
        def mock_llm(prompt):
            return '{"same_topic": true, "confidence": 9, "reason": "same topic"}'

        result = llm_topic_check("What DB?", "Which database?", mock_llm)
        assert result["same_topic"] is True

    def test_judge_low_confidence_keeps(self):
        """LLM confidence < 8 -> winner forced to A (keep existing)."""
        def mock_llm(prompt):
            return '{"winner": "B", "confidence": 6, "reason": "slightly better"}'

        result = llm_judge("Q?", "existing", "new", mock_llm)
        assert result["winner"] == "A"
        assert "Low confidence" in result["reason"]

    def test_judge_picks_new_replaces(self):
        """LLM confidence >= 8, winner B -> REPLACE signal."""
        def mock_llm(prompt):
            return '{"winner": "B", "confidence": 9, "reason": "much more specific"}'

        result = llm_judge("Q?", "existing", "new", mock_llm)
        assert result["winner"] == "B"

    def test_llm_error_defaults_conservative(self):
        """LLM exception -> keep existing."""
        def mock_llm(prompt):
            raise RuntimeError("API error")

        result = llm_judge("Q?", "existing", "new", mock_llm)
        assert result["winner"] == "A"
        assert "error" in result["reason"].lower()

    def test_topic_check_error_conservative(self):
        """Topic check LLM error -> not same topic (conservative)."""
        def mock_llm(prompt):
            raise RuntimeError("timeout")

        result = llm_topic_check("Q1", "Q2", mock_llm)
        assert result["same_topic"] is False

    def test_parse_llm_json_obj_direct(self):
        """Direct JSON object parse."""
        result = _parse_llm_json_obj('{"winner": "A", "confidence": 8}')
        assert result["winner"] == "A"

    def test_parse_llm_json_obj_fenced(self):
        """Markdown fenced JSON parse."""
        text = '```json\n{"winner": "B", "confidence": 9}\n```'
        result = _parse_llm_json_obj(text)
        assert result["winner"] == "B"

    def test_parse_llm_json_obj_embedded(self):
        """Regex extracts JSON from surrounding text."""
        text = 'Here is my answer: {"winner": "A", "confidence": 7} done.'
        result = _parse_llm_json_obj(text)
        assert result["winner"] == "A"

    def test_parse_llm_json_obj_empty(self):
        """Empty input returns empty dict."""
        assert _parse_llm_json_obj("") == {}

    def test_budget_exhausted_topic_check(self):
        """No budget for topic check -> ADD_NEW (conservative)."""
        result = select_answer(
            "What DB?", CLEAN_ANSWER,
            "How handle backups?", GENERIC_ANSWER,
            similarity=0.75,
            llm_call=None,
            llm_calls_remaining=0,
        )
        assert result["decision"] == "ADD_NEW"
        assert "unavailable" in result["reason"].lower() or "budget" in result["stage"]


# ---------------------------------------------------------------------------
# Full Pipeline (select_answer)
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_add_new_low_similarity(self):
        """Low similarity -> ADD_NEW without LLM."""
        result = select_answer(
            "What database?", CLEAN_ANSWER,
            "Describe training plan", GENERIC_ANSWER,
            similarity=0.40,
        )
        assert result["decision"] == "ADD_NEW"
        assert result["llm_used"] is False

    def test_keep_existing_red_flag(self):
        """New has red flags -> KEEP_EXISTING via gate."""
        result = select_answer(
            "Q?", CLEAN_ANSWER,
            "Q?", RED_FLAG_ANSWER,
            similarity=0.90,
        )
        assert result["decision"] == "KEEP_EXISTING"
        assert result["stage"] == "gate"

    def test_replace_clear_winner(self):
        """New clearly better -> REPLACE via scoring."""
        result = select_answer(
            "Q?", GENERIC_ANSWER,
            "Q?", CLEAN_ANSWER,
            similarity=0.90,
        )
        assert result["decision"] == "REPLACE"
        assert result["stage"] == "scoring"

    def test_conservative_on_tie_no_llm(self):
        """Tie without LLM -> KEEP_EXISTING."""
        a = "Blue Yonder provides demand planning on Azure."
        b = "Blue Yonder offers supply planning on Azure."
        result = select_answer("Q?", a, "Q?", b, similarity=0.90, llm_call=None)
        assert result["decision"] == "KEEP_EXISTING"
        assert "conservative" in result["reason"].lower()

    def test_tie_with_llm_judge_keep(self):
        """Tie with LLM -> LLM decides (keep)."""
        def mock_llm(prompt):
            return '{"winner": "A", "confidence": 9, "reason": "existing is better"}'

        a = "Blue Yonder provides demand planning on Azure."
        b = "Blue Yonder offers supply planning on Azure."
        result = select_answer("Q?", a, "Q?", b, similarity=0.90, llm_call=mock_llm)
        assert result["decision"] == "KEEP_EXISTING"
        assert result["stage"] == "llm_judge"
        assert result["llm_used"] is True

    def test_tie_with_llm_judge_replace(self):
        """Tie with LLM -> LLM decides (replace, high confidence)."""
        def mock_llm(prompt):
            return '{"winner": "B", "confidence": 9, "reason": "new is much more specific"}'

        a = "Blue Yonder provides demand planning on Azure."
        b = "Blue Yonder offers supply planning on Azure."
        result = select_answer("Q?", a, "Q?", b, similarity=0.90, llm_call=mock_llm)
        assert result["decision"] == "REPLACE"
        assert result["stage"] == "llm_judge"

    def test_topic_check_different_adds_new(self):
        """Mid-similarity + LLM says different topic -> ADD_NEW."""
        def mock_llm(prompt):
            if "SAME topic" in prompt:
                return '{"same_topic": false, "confidence": 9, "reason": "different"}'
            return '{"winner": "A", "confidence": 5, "reason": "n/a"}'

        result = select_answer(
            "What database?", CLEAN_ANSWER,
            "Training schedule?", GENERIC_ANSWER,
            similarity=0.75,
            llm_call=mock_llm,
        )
        assert result["decision"] == "ADD_NEW"
        assert result["stage"] == "topic_guard"

    def test_topic_check_same_continues_to_scoring(self):
        """Mid-similarity + LLM says same topic -> continues to scoring."""
        def mock_llm(prompt):
            if "SAME topic" in prompt:
                return '{"same_topic": true, "confidence": 9, "reason": "same"}'
            return '{"winner": "A", "confidence": 9, "reason": "existing is better"}'

        result = select_answer(
            "Q?", CLEAN_ANSWER,
            "Q?", GENERIC_ANSWER,
            similarity=0.75,
            llm_call=mock_llm,
        )
        # After topic check passes, scoring should decide (CLEAN >> GENERIC)
        assert result["decision"] == "KEEP_EXISTING"

    def test_no_needs_topic_check_in_output(self):
        """Internal flag needs_topic_check should not leak to output."""
        result = select_answer(
            "Q?", CLEAN_ANSWER, "Q?", GENERIC_ANSWER,
            similarity=0.75, llm_call=None,
        )
        assert "needs_topic_check" not in result

    def test_replace_deprecated_existing(self):
        """Existing has deprecated terms, new is clean -> REPLACE via gate."""
        result = select_answer(
            "Q?", DEPRECATED_ANSWER,
            "Q?", CLEAN_ANSWER,
            similarity=0.90,
        )
        assert result["decision"] == "REPLACE"
        assert result["stage"] == "gate"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class TestReporting:
    def test_print_report_no_crash(self, capsys):
        """print_improve_report runs without error."""
        decisions = {"KEEP_EXISTING": 5, "REPLACE": 2, "ADD_NEW": 3}
        audit_log = [
            {"decision": "KEEP_EXISTING", "stage": "scoring", "reason": "test"},
            {"decision": "REPLACE", "stage": "gate", "reason": "test"},
        ]
        print_improve_report(decisions, audit_log, llm_budget_used=5)
        captured = capsys.readouterr()
        assert "IMPROVE" in captured.out
        assert "5" in captured.out

    def test_save_report(self, tmp_path):
        """save_improve_report writes valid JSON."""
        audit_log = [
            {"decision": "KEEP_EXISTING", "reason": "test"},
            {"decision": "REPLACE", "reason": "better"},
        ]
        path = save_improve_report(audit_log, "test.xlsx", output_dir=tmp_path)
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert data["summary"]["kept"] == 1
        assert data["summary"]["replaced"] == 1
        assert data["summary"]["total"] == 2
