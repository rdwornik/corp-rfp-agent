"""Acceptance tests for LLM router -- retry, error handling, and retrieval."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_router import retry_with_backoff, extract_question, extract_answer


# ---------------------------------------------------------------------------
# Test 1: Retry on transient failure
# ---------------------------------------------------------------------------
def test_retry_on_transient_failure():
    """Router retries on transient 429 errors and eventually succeeds."""
    call_count = 0

    def flaky_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("429 Too Many Requests")
        return "success"

    result = retry_with_backoff(flaky_func, max_retries=5, base_delay=0.01)
    assert result == "success"
    assert call_count == 3


# ---------------------------------------------------------------------------
# Test 2: Backoff between retries
# ---------------------------------------------------------------------------
def test_backoff_timing():
    """Router applies increasing delays between retry attempts."""
    timestamps = []

    def always_fail():
        timestamps.append(time.time())
        raise Exception("429 rate limited")

    # Use base_delay=2 so exponential growth (2, 4, 8...) dominates
    # the random jitter (0-1s). We verify total time shows backoff happened.
    with pytest.raises(Exception, match="Max retries"):
        retry_with_backoff(always_fail, max_retries=3, base_delay=2)

    assert len(timestamps) == 3

    # Total elapsed should be at least base_delay*(2^0) + base_delay*(2^1) = 2 + 4 = 6s
    # (minus jitter noise). Check that delays are non-trivial.
    delay1 = timestamps[1] - timestamps[0]
    delay2 = timestamps[2] - timestamps[1]
    assert delay1 >= 1.5, f"First delay too short: {delay1:.3f}s"
    assert delay2 >= 3.0, f"Second delay too short: {delay2:.3f}s"
    assert delay2 > delay1, f"Backoff not increasing: {delay1:.3f}s -> {delay2:.3f}s"


# ---------------------------------------------------------------------------
# Test 3: Max retries exceeded
# ---------------------------------------------------------------------------
def test_max_retries_exceeded():
    """Router raises after max retries exhausted."""
    call_count = 0

    def always_fail():
        nonlocal call_count
        call_count += 1
        raise Exception("429 Too Many Requests")

    with pytest.raises(Exception, match="Max retries"):
        retry_with_backoff(always_fail, max_retries=3, base_delay=0.01)

    assert call_count == 3


# ---------------------------------------------------------------------------
# Test 4: Non-rate-limit errors are not retried
# ---------------------------------------------------------------------------
def test_non_rate_limit_error_not_retried():
    """Non-rate-limit errors are raised immediately without retry."""
    call_count = 0

    def auth_error():
        nonlocal call_count
        call_count += 1
        raise ValueError("Invalid API key")

    with pytest.raises(ValueError, match="Invalid API key"):
        retry_with_backoff(auth_error, max_retries=5, base_delay=0.01)

    assert call_count == 1  # No retries for non-rate-limit errors


# ---------------------------------------------------------------------------
# Test 5: Model registry has expected entries
# ---------------------------------------------------------------------------
def test_model_registry():
    """Model registry contains exactly 4 models with correct providers."""
    from llm_router import MODELS

    # Verify exactly 4 models
    assert len(MODELS) == 4
    assert set(MODELS.keys()) == {"gemini", "gemini-flash", "sonnet", "gpt"}

    # Verify structure
    for key, config in MODELS.items():
        assert "name" in config, f"Model {key} missing 'name'"
        assert "provider" in config, f"Model {key} missing 'provider'"

    # Verify providers
    assert MODELS["gemini"]["provider"] == "google"
    assert MODELS["gemini-flash"]["provider"] == "google"
    assert MODELS["sonnet"]["provider"] == "anthropic"
    assert MODELS["gpt"]["provider"] == "openai"

    # Verify gemini points to pro model
    assert (
        "pro" in MODELS["gemini"]["name"].lower() or "3.1" in MODELS["gemini"]["name"]
    )


# ---------------------------------------------------------------------------
# Test 6: Sonnet in model registry
# ---------------------------------------------------------------------------
def test_models_dict_has_sonnet():
    """Model registry has 'sonnet' pointing to claude-sonnet-4-6."""
    from llm_router import MODELS

    assert "sonnet" in MODELS
    assert MODELS["sonnet"]["provider"] == "anthropic"
    assert "sonnet-4-6" in MODELS["sonnet"]["name"]


# ---------------------------------------------------------------------------
# Test 7: Only 3 providers remain
# ---------------------------------------------------------------------------
def test_only_three_providers():
    """Only google, anthropic, and openai providers exist."""
    from llm_router import MODELS

    providers = {config["provider"] for config in MODELS.values()}
    assert providers == {"google", "anthropic", "openai"}


# ---------------------------------------------------------------------------
# Test 8: Compare mode calls both models
# ---------------------------------------------------------------------------
def test_compare_mode_calls_both(monkeypatch):
    """compare_models() calls generate_answer for each requested model."""
    from unittest.mock import patch

    calls = []

    def fake_generate(self, query, model="gemini"):
        calls.append(model)
        return f"Answer from {model}"

    # Patch LLMRouter.__init__ to avoid ChromaDB/file dependencies
    with (
        patch("llm_router.LLMRouter.__init__", return_value=None),
        patch("llm_router.LLMRouter.generate_answer", fake_generate),
    ):
        from llm_router import compare_models

        results = compare_models("test query", models=["gemini", "sonnet"])

    assert "gemini" in calls
    assert "sonnet" in calls
    assert len(results) == 2
    assert results["gemini"]["answer"] == "Answer from gemini"
    assert results["sonnet"]["answer"] == "Answer from sonnet"
    assert results["gemini"]["chars"] > 0
    assert results["sonnet"]["elapsed"] >= 0


# ---------------------------------------------------------------------------
# Test 9: extract_question from markdown
# ---------------------------------------------------------------------------
def test_extract_question_from_markdown():
    """extract_question() finds ## Question section in vault markdown."""
    content = """---
id: kb-rfp-wms-0001
doc_type: rfp_response
---

## Question
How does Blue Yonder handle warehouse integration?

## Answer
Blue Yonder WMS supports REST APIs for warehouse integration."""

    result = extract_question(content)
    assert result == "How does Blue Yonder handle warehouse integration?"


def test_extract_question_fallback():
    """extract_question() returns first non-header line when no ## Question."""
    content = "Blue Yonder WMS supports REST APIs.\nMore details here."
    result = extract_question(content)
    assert result == "Blue Yonder WMS supports REST APIs."


# ---------------------------------------------------------------------------
# Test 10: extract_answer from markdown
# ---------------------------------------------------------------------------
def test_extract_answer_from_markdown():
    """extract_answer() finds ## Answer section in vault markdown."""
    content = """## Question
How does Blue Yonder handle warehouse integration?

## Answer
Blue Yonder WMS supports REST APIs for warehouse integration.
It also supports EDI and flat file interfaces."""

    result = extract_answer(content)
    assert "REST APIs" in result
    assert "EDI" in result


def test_extract_answer_fallback():
    """extract_answer() returns full content when no ## Answer header."""
    content = "Blue Yonder WMS supports REST APIs."
    result = extract_answer(content)
    assert result == "Blue Yonder WMS supports REST APIs."


# ---------------------------------------------------------------------------
# Test 11: retrieve_context uses vault primary
# ---------------------------------------------------------------------------
def test_get_context_from_vault():
    """LLMRouter.retrieve_context() uses vault as primary retrieval."""
    vault_notes = [
        {
            "note_id": 42,
            "content": "## Question\nHow does WMS work?\n\n## Answer\nWMS handles warehouse ops.",
            "topics": ["WMS"],
            "products": ["wms"],
            "relevance_score": 0.9,
        }
    ]

    with (
        patch("llm_router.LLMRouter.__init__", return_value=None),
        patch("llm_router.vault_retrieve", return_value=vault_notes),
    ):
        from llm_router import LLMRouter

        router = LLMRouter.__new__(LLMRouter)
        router.family = "wms"
        router.collection = None  # no ChromaDB
        items = router.retrieve_context("warehouse operations", k=5)

    assert len(items) == 1
    assert items[0]["canonical_question"] == "How does WMS work?"
    assert "warehouse ops" in items[0]["canonical_answer"]
    assert items[0]["kb_id"] == "42"


# ---------------------------------------------------------------------------
# Test 12: retrieve_context falls back to ChromaDB
# ---------------------------------------------------------------------------
def test_get_context_fallback_to_chromadb():
    """When vault fails, retrieve_context() falls back to ChromaDB."""
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["planning_kb_0001"]],
        "distances": [[0.3]],
    }
    kb_item = {
        "kb_id": "kb_0001",
        "canonical_question": "What is demand planning?",
        "canonical_answer": "Demand planning forecasts future demand.",
        "category": "Planning",
        "subcategory": "Demand",
        "domain": "planning",
    }

    with (
        patch("llm_router.LLMRouter.__init__", return_value=None),
        patch("llm_router.vault_retrieve", side_effect=Exception("vault down")),
    ):
        from llm_router import LLMRouter

        router = LLMRouter.__new__(LLMRouter)
        router.family = "planning"
        router.collection = mock_collection
        router.kb_lookup = {"planning_kb_0001": kb_item}
        items = router.retrieve_context("demand planning", k=5)

    assert len(items) == 1
    assert items[0]["canonical_question"] == "What is demand planning?"
    mock_collection.query.assert_called_once()
