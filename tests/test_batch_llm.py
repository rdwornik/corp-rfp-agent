"""Tests for batch_llm -- Gemini Batch API wrapper."""

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from batch_llm import (
    BatchProcessor,
    BatchRequest,
    BatchResult,
    parse_json_from_batch,
    INLINE_MAX_REQUESTS,
)


# ---------------------------------------------------------------------------
# Mock helpers for google.genai
# ---------------------------------------------------------------------------

def _mock_genai_modules():
    """Create mock google.genai and google.genai.types modules."""
    mock_genai = MagicMock()
    mock_types = MagicMock()
    mock_genai.types = mock_types

    # Make BatchRequest and GenerateContentRequest return their kwargs
    mock_types.BatchRequest = lambda **kw: MagicMock(**kw)
    mock_types.GenerateContentRequest = lambda **kw: MagicMock(**kw)

    mock_google = MagicMock()
    mock_google.genai = mock_genai

    return {
        "google": mock_google,
        "google.genai": mock_genai,
        "google.genai.types": mock_types,
    }, mock_genai


def _make_completed_job(name="batch-123", state="JOB_STATE_SUCCEEDED",
                         total=1, succeeded=1, failed=0,
                         inlined_responses=None, file_name=None):
    """Build a mock batch job object."""
    job = MagicMock()
    job.name = name
    mock_state = MagicMock()
    mock_state.name = state
    job.state = mock_state
    job.batch_stats = MagicMock(
        total_request_count=total,
        success_request_count=succeeded,
        failed_request_count=failed,
    )
    job.dest = MagicMock()
    job.dest.inlined_responses = inlined_responses
    job.dest.file_name = file_name
    return job


def _make_inline_response(key, text):
    """Build a mock inline response."""
    mock_part = MagicMock()
    mock_part.text = text
    mock_content = MagicMock()
    mock_content.parts = [mock_part]
    mock_candidate = MagicMock()
    mock_candidate.content = mock_content
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    resp = MagicMock()
    resp.key = key
    resp.response = mock_response
    return resp


# ===================================================================
# BatchProcessor basics
# ===================================================================

class TestBatchProcessorBasics:

    def test_add_single_request(self):
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="req_0", prompt="Hello")
        assert bp.count == 1

    def test_add_many_requests(self):
        bp = BatchProcessor(api_key="test-key")
        bp.add_many([
            {"key": "a", "prompt": "P1"},
            {"key": "b", "prompt": "P2"},
            {"key": "c", "prompt": "P3", "system_prompt": "sys"},
        ])
        assert bp.count == 3

    def test_count_tracks_requests(self):
        bp = BatchProcessor(api_key="test-key")
        assert bp.count == 0
        bp.add(key="x", prompt="test")
        assert bp.count == 1
        bp.add(key="y", prompt="test2")
        assert bp.count == 2

    def test_clear_removes_requests(self):
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="x", prompt="test")
        bp.clear()
        assert bp.count == 0

    def test_no_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            import os
            env = {k: v for k, v in os.environ.items()
                   if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
            with patch.dict("os.environ", env, clear=True):
                with pytest.raises(ValueError, match="No API key"):
                    BatchProcessor()

    def test_api_key_from_param(self):
        bp = BatchProcessor(api_key="my-key")
        assert bp._api_key == "my-key"

    def test_api_key_from_env_gemini(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "env-key"}):
            bp = BatchProcessor()
            assert bp._api_key == "env-key"

    def test_api_key_from_env_google(self):
        import os
        env = dict(os.environ)
        env.pop("GEMINI_API_KEY", None)
        env["GOOGLE_API_KEY"] = "gk"
        with patch.dict("os.environ", env, clear=True):
            bp = BatchProcessor()
            assert bp._api_key == "gk"


# ===================================================================
# Empty batch
# ===================================================================

class TestEmptyBatch:

    def test_empty_batch_returns_empty(self):
        bp = BatchProcessor(api_key="test-key")
        result = bp.run(verbose=False)
        assert result.state == "EMPTY"
        assert result.results == {}

    def test_clear_after_run(self):
        """Requests cleared after run() -- even for empty batch."""
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="x", prompt="test")
        # Empty run clears immediately
        bp.clear()
        result = bp.run(verbose=False)
        assert bp.count == 0


# ===================================================================
# Inline vs file threshold
# ===================================================================

class TestThresholds:

    def test_inline_threshold_at_100(self):
        bp = BatchProcessor(api_key="test-key")
        for i in range(100):
            bp.add(key=f"r_{i}", prompt=f"prompt {i}")
        assert bp.count <= INLINE_MAX_REQUESTS

    def test_file_threshold_above_100(self):
        bp = BatchProcessor(api_key="test-key")
        for i in range(101):
            bp.add(key=f"r_{i}", prompt=f"prompt {i}")
        assert bp.count > INLINE_MAX_REQUESTS


# ===================================================================
# Submit inline (mocked)
# ===================================================================

class TestSubmitInline:

    def test_submit_inline_creates_job(self):
        bp = BatchProcessor(api_key="test-key", model="gemini-3-flash-preview")
        bp.add(key="req_0", prompt="Classify this")
        bp.add(key="req_1", prompt="Classify that", system_prompt="You are a classifier")

        modules, mock_genai = _mock_genai_modules()

        mock_client = MagicMock()
        job = _make_completed_job(inlined_responses=[])
        mock_client.batches.create.return_value = job
        mock_client.batches.get.return_value = job
        mock_genai.Client.return_value = mock_client

        with patch.dict("sys.modules", modules):
            result = bp.run(display_name="test-batch", verbose=False)

        assert result.job_name == "batch-123"
        assert result.state == "JOB_STATE_SUCCEEDED"
        mock_client.batches.create.assert_called_once()

    def test_submit_inline_system_prompt_merged(self):
        """System prompt is prepended to user prompt."""
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="r", prompt="user text", system_prompt="system text")
        req = bp._requests[0]
        assert req.system_prompt == "system text"
        assert req.prompt == "user text"


# ===================================================================
# Submit file (mocked)
# ===================================================================

class TestSubmitFile:

    def test_submit_file_uploads_jsonl(self):
        bp = BatchProcessor(api_key="test-key")
        for i in range(105):
            bp.add(key=f"r_{i}", prompt=f"prompt {i}")

        modules, mock_genai = _mock_genai_modules()

        mock_uploaded = MagicMock()
        mock_uploaded.name = "uploaded-file-id"

        mock_client = MagicMock()
        mock_client.files.upload.return_value = mock_uploaded
        job = _make_completed_job(
            total=105, succeeded=105,
            inlined_responses=None, file_name=None,
        )
        mock_client.batches.create.return_value = job
        mock_client.batches.get.return_value = job
        mock_genai.Client.return_value = mock_client

        with patch.dict("sys.modules", modules):
            result = bp.run(verbose=False)

        mock_client.files.upload.assert_called_once()
        assert result.state == "JOB_STATE_SUCCEEDED"


# ===================================================================
# Polling
# ===================================================================

class TestPolling:

    def test_poll_waits_for_completion(self):
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="r", prompt="test")

        modules, mock_genai = _mock_genai_modules()

        pending_job = _make_completed_job(state="JOB_STATE_PENDING")
        done_job = _make_completed_job(inlined_responses=[])

        mock_client = MagicMock()
        mock_client.batches.create.return_value = pending_job
        mock_client.batches.get.side_effect = [pending_job, done_job]
        mock_genai.Client.return_value = mock_client

        with patch.dict("sys.modules", modules), \
             patch("batch_llm.time") as mock_time:
            result = bp.run(poll_interval=1, verbose=False)

        assert result.state == "JOB_STATE_SUCCEEDED"
        assert mock_client.batches.get.call_count == 2


# ===================================================================
# Result extraction
# ===================================================================

class TestResultExtraction:

    def test_extract_inline_results(self):
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="r_0", prompt="test")

        modules, mock_genai = _mock_genai_modules()

        inline_resp = _make_inline_response("r_0", '{"category": "technical"}')
        job = _make_completed_job(inlined_responses=[inline_resp])

        mock_client = MagicMock()
        mock_client.batches.create.return_value = job
        mock_client.batches.get.return_value = job
        mock_genai.Client.return_value = mock_client

        with patch.dict("sys.modules", modules):
            result = bp.run(verbose=False)

        assert "r_0" in result.results
        assert "technical" in result.results["r_0"]

    def test_extract_file_results(self):
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="r_0", prompt="test")

        modules, mock_genai = _mock_genai_modules()

        file_content = json.dumps({
            "key": "r_0",
            "response": {
                "candidates": [{"content": {"parts": [{"text": "result text"}]}}]
            }
        })

        job = _make_completed_job(
            inlined_responses=None, file_name="output-file-id",
        )

        mock_client = MagicMock()
        mock_client.batches.create.return_value = job
        mock_client.batches.get.return_value = job
        mock_client.files.download.return_value = file_content.encode("utf-8")
        mock_genai.Client.return_value = mock_client

        with patch.dict("sys.modules", modules):
            result = bp.run(verbose=False)

        assert "r_0" in result.results
        assert result.results["r_0"] == "result text"

    def test_failed_job_reports_errors(self):
        bp = BatchProcessor(api_key="test-key")
        bp.add(key="r_0", prompt="test")

        modules, mock_genai = _mock_genai_modules()

        job = _make_completed_job(
            state="JOB_STATE_FAILED", succeeded=0, failed=1,
            inlined_responses=None, file_name=None,
        )

        mock_client = MagicMock()
        mock_client.batches.create.return_value = job
        mock_client.batches.get.return_value = job
        mock_genai.Client.return_value = mock_client

        with patch.dict("sys.modules", modules):
            result = bp.run(verbose=False)

        assert result.state == "JOB_STATE_FAILED"
        assert result.failed == 1


# ===================================================================
# JSON parsing
# ===================================================================

class TestParseJson:

    def test_parse_json_direct(self):
        result = parse_json_from_batch('{"category": "technical"}')
        assert result == {"category": "technical"}

    def test_parse_json_array(self):
        result = parse_json_from_batch('[{"index": 0, "category": "functional"}]')
        assert isinstance(result, list)
        assert result[0]["category"] == "functional"

    def test_parse_json_with_fences(self):
        text = '```json\n{"winner": "A", "confidence": 9}\n```'
        result = parse_json_from_batch(text)
        assert result["winner"] == "A"

    def test_parse_json_with_preamble(self):
        text = 'Here is the result:\n\n{"same_topic": true, "confidence": 8}'
        result = parse_json_from_batch(text)
        assert result["same_topic"] is True

    def test_parse_json_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse JSON"):
            parse_json_from_batch("This is not JSON at all")

    def test_parse_json_empty_raises(self):
        with pytest.raises(ValueError, match="Empty response"):
            parse_json_from_batch("")

    def test_parse_json_nested_fences(self):
        text = '```json\n[{"index": 0, "category": "consulting", "confidence": 0.95}]\n```'
        result = parse_json_from_batch(text)
        assert result[0]["confidence"] == 0.95

    def test_parse_json_with_extra_text(self):
        text = 'Based on analysis:\n```\n{"decision": "KEEP"}\n```\nDone.'
        result = parse_json_from_batch(text)
        assert result["decision"] == "KEEP"
