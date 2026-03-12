"""Gemini Batch API wrapper for bulk LLM processing at 50% cost.

Submits requests asynchronously via the Gemini Batch API, polls for
completion, and returns parsed results. Automatically chooses inline
(<=100 requests) vs file upload (>100).

Usage:
    from batch_llm import BatchProcessor, parse_json_from_batch

    processor = BatchProcessor(model="gemini-3-flash-preview")
    for i, item in enumerate(items):
        processor.add(key=f"item_{i}", prompt=item["prompt"])

    result = processor.run(display_name="kb-reclassify")
    # result.results == {"item_0": "response text", ...}
"""

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
INLINE_MAX_REQUESTS = 100
POLL_INTERVAL = 30        # seconds between status checks
MAX_WAIT_HOURS = 24

TERMINAL_STATES = frozenset({
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BatchRequest:
    """A single request in a batch."""
    key: str
    prompt: str
    system_prompt: Optional[str] = None


@dataclass
class BatchResult:
    """Result of a completed batch job."""
    job_name: str
    state: str
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: dict[str, str] = field(default_factory=dict)   # key -> response text
    errors: dict[str, str] = field(default_factory=dict)     # key -> error message


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

class BatchProcessor:
    """Gemini Batch API processor.

    Automatically chooses inline vs file-upload based on batch size.
    Handles polling, error extraction, and result parsing.
    """

    def __init__(
        self,
        model: str = "gemini-3-flash-preview",
        api_key: Optional[str] = None,
    ):
        self._model = model
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        self._requests: list[BatchRequest] = []

        if not self._api_key:
            raise ValueError(
                "No API key found. Set GEMINI_API_KEY or GOOGLE_API_KEY, "
                "or pass api_key= to BatchProcessor."
            )

    def add(
        self,
        key: str,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> None:
        """Add a request to the batch."""
        self._requests.append(BatchRequest(key=key, prompt=prompt, system_prompt=system_prompt))

    def add_many(self, items: list[dict]) -> None:
        """Add multiple requests. Each dict needs 'key' and 'prompt'."""
        for item in items:
            self.add(
                key=item["key"],
                prompt=item["prompt"],
                system_prompt=item.get("system_prompt"),
            )

    @property
    def count(self) -> int:
        return len(self._requests)

    def clear(self) -> None:
        """Remove all queued requests."""
        self._requests.clear()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(
        self,
        display_name: str = "batch-job",
        poll_interval: int = POLL_INTERVAL,
        max_wait_hours: float = MAX_WAIT_HOURS,
        verbose: bool = True,
    ) -> BatchResult:
        """Submit batch, poll until done, return results."""
        from google import genai

        if not self._requests:
            logger.warning("No requests to process")
            return BatchResult(job_name="", state="EMPTY")

        client = genai.Client(api_key=self._api_key)
        use_inline = len(self._requests) <= INLINE_MAX_REQUESTS
        method = "inline" if use_inline else "file"

        if verbose:
            logger.info(
                "Submitting batch: %d requests, model=%s, method=%s",
                len(self._requests), self._model, method,
            )

        # Submit
        job = (
            self._submit_inline(client, display_name)
            if use_inline
            else self._submit_file(client, display_name)
        )

        if verbose:
            logger.info("Job submitted: %s", job.name)

        # Poll
        max_polls = int(max_wait_hours * 3600 / poll_interval)
        for i in range(max_polls):
            job = client.batches.get(name=job.name)
            state = job.state.name if hasattr(job.state, "name") else str(job.state)

            if state in TERMINAL_STATES:
                break

            if verbose and i % 4 == 0:
                logger.info("  Waiting... state=%s (%d/%d)", state, i + 1, max_polls)

            time.sleep(poll_interval)

        # Extract
        result = self._extract_results(job, client)

        if verbose:
            logger.info(
                "Batch complete: %d succeeded, %d failed (state=%s)",
                result.succeeded, result.failed, result.state,
            )

        self._requests.clear()
        return result

    # ------------------------------------------------------------------
    # Submit strategies
    # ------------------------------------------------------------------

    def _submit_inline(self, client, display_name):
        """Submit as inline requests (<=100)."""
        from google.genai import types

        requests = []
        for req in self._requests:
            text = (req.system_prompt + "\n\n" + req.prompt) if req.system_prompt else req.prompt
            requests.append(
                types.BatchRequest(
                    key=req.key,
                    request=types.GenerateContentRequest(
                        model=self._model,
                        contents=[{"role": "user", "parts": [{"text": text}]}],
                    ),
                )
            )

        return client.batches.create(
            model=self._model,
            requests=requests,
            config={"display_name": display_name},
        )

    def _submit_file(self, client, display_name):
        """Submit via JSONL file upload (>100 requests)."""
        lines = []
        for req in self._requests:
            text = (req.system_prompt + "\n\n" + req.prompt) if req.system_prompt else req.prompt
            line = {
                "key": req.key,
                "request": {
                    "model": self._model,
                    "contents": [{"role": "user", "parts": [{"text": text}]}],
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False))

        tmp_path = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            tmp_path.write_text("\n".join(lines), encoding="utf-8")
            uploaded = client.files.upload(file=str(tmp_path))
            logger.info("Uploaded JSONL: %s (%d requests)", uploaded.name, len(lines))
        finally:
            tmp_path.unlink(missing_ok=True)

        return client.batches.create(
            model=self._model,
            src=uploaded.name,
            config={"display_name": display_name},
        )

    # ------------------------------------------------------------------
    # Result extraction
    # ------------------------------------------------------------------

    def _extract_results(self, job, client) -> BatchResult:
        """Parse results from completed batch job."""
        state = job.state.name if hasattr(job.state, "name") else str(job.state)

        result = BatchResult(job_name=job.name, state=state)

        # Stats
        if hasattr(job, "batch_stats") and job.batch_stats:
            result.total = getattr(job.batch_stats, "total_request_count", 0) or 0
            result.succeeded = getattr(job.batch_stats, "success_request_count", 0) or 0
            result.failed = getattr(job.batch_stats, "failed_request_count", 0) or 0

        # Inline responses
        if (
            hasattr(job, "dest")
            and hasattr(job.dest, "inlined_responses")
            and job.dest.inlined_responses
        ):
            for resp in job.dest.inlined_responses:
                try:
                    text = resp.response.candidates[0].content.parts[0].text
                    result.results[resp.key] = text
                except (IndexError, AttributeError) as e:
                    result.errors[resp.key] = str(e)
            return result

        # File-based responses
        if (
            hasattr(job, "dest")
            and hasattr(job.dest, "file_name")
            and job.dest.file_name
        ):
            try:
                file_bytes = client.files.download(file=job.dest.file_name)
                content = file_bytes.decode("utf-8") if isinstance(file_bytes, bytes) else str(file_bytes)
                for line in content.strip().splitlines():
                    try:
                        data = json.loads(line)
                        key = data.get("key", "")
                        if "response" in data:
                            text = data["response"]["candidates"][0]["content"]["parts"][0]["text"]
                            result.results[key] = text
                        elif "error" in data:
                            result.errors[key] = str(data["error"])
                    except (json.JSONDecodeError, KeyError, IndexError) as e:
                        logger.warning("Failed to parse batch result line: %s", e)
            except Exception as e:
                logger.error("Failed to download batch results: %s", e)

        return result


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def parse_json_from_batch(text: str):
    """Parse JSON (object or array) from batch response text.

    Uses 3-strategy fallback consistent with the rest of the codebase:
    1. Direct parse
    2. Strip markdown fences
    3. Regex extract
    """
    if not text:
        raise ValueError("Empty response text")

    # Strategy 1: direct
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: strip fences
    cleaned = re.sub(r"```json\s*", "", str(text))
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 3: regex extract outermost JSON structure
    match = re.search(r"[\[{].*[\]}]", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, TypeError):
            pass

    raise ValueError(f"Cannot parse JSON from batch response: {text[:200]}")
