"""Tests for core types."""

from corp_rfp_agent.core.types import (
    Family, Category, Confidence, LLMResponse, KBMatch,
)


def test_family_enum_has_all_families():
    """Family enum has all 11 product families."""
    assert len(Family) == 11
    assert Family.PLANNING == "planning"
    assert Family.WMS == "wms"
    assert Family.NETWORK == "network"
    assert Family.AIML == "aiml"


def test_category_enum_has_4_categories():
    """Category enum has 4 RFP response team categories."""
    assert len(Category) == 4
    assert Category.TECHNICAL == "technical"
    assert Category.FUNCTIONAL == "functional"
    assert Category.CUSTOMER_EXECUTIVE == "customer_executive"
    assert Category.CONSULTING == "consulting"


def test_confidence_enum():
    """Confidence enum has expected values."""
    assert Confidence.VERIFIED == "verified"
    assert Confidence.DRAFT == "draft"
    assert Confidence.NEEDS_REVIEW == "needs_review"
    assert Confidence.OUTDATED == "outdated"


def test_llm_response_creation():
    """LLMResponse dataclass creates correctly."""
    resp = LLMResponse(text="hello", model="gemini", provider="google")
    assert resp.text == "hello"
    assert resp.model == "gemini"
    assert resp.provider == "google"
    assert resp.tokens_in == 0
    assert resp.latency_ms == 0.0


def test_kb_match_creation():
    """KBMatch dataclass creates correctly with defaults."""
    match = KBMatch(
        entry_id="PLN-FUNC-0001",
        question="How does planning work?",
        answer="Blue Yonder uses AI.",
        similarity=0.92,
    )
    assert match.entry_id == "PLN-FUNC-0001"
    assert match.similarity == 0.92
    assert match.family_code == ""
    assert match.metadata == {}
