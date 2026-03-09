"""Adapter wrapping existing llm_router.py behind LLMClient interface."""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from corp_rfp_agent.core.types import LLMResponse

logger = logging.getLogger(__name__)

# Add legacy src/ to path so we can import the existing router
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = str(_PROJECT_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


class RouterLLMClient:
    """Wraps existing llm_router.py behind the LLMClient protocol.

    This adapter imports and delegates to the legacy LLMRouter class
    while presenting the clean LLMClient interface.
    """

    def __init__(self, default_model: str = "gemini", solution: Optional[str] = None):
        self._default_model = default_model
        self._solution = solution

        # Lazy-load the legacy router (heavy: loads ChromaDB, embeddings)
        self._router = None

    def _get_router(self):
        """Lazy-load the legacy LLMRouter."""
        if self._router is None:
            from llm_router import LLMRouter
            self._router = LLMRouter(solution=self._solution)
        return self._router

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Route to existing llm_router and wrap response."""
        from llm_router import MODELS, clean_bold_markdown, retry_with_backoff
        from google import genai
        from google.genai import types as genai_types

        model_key = model or self._default_model
        model_config = MODELS.get(model_key, MODELS.get("gemini"))
        model_name = model_config["name"]
        provider = model_config["provider"]

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        start = time.perf_counter()

        # Use Gemini directly (most common path)
        if provider == "google":
            def call_llm():
                client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
                response = client.models.generate_content(
                    model=model_name,
                    contents=[{"role": "user", "parts": [{"text": full_prompt}]}],
                    config=genai_types.GenerateContentConfig(
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                    ),
                )
                return response.text.strip() if response.text else ""

            text = retry_with_backoff(call_llm)
            text = clean_bold_markdown(text)
        else:
            # For non-Google providers, use the legacy router's generate_answer
            # which handles all provider-specific logic
            router = self._get_router()
            text = router.generate_answer(prompt, model=model_key)

        elapsed = (time.perf_counter() - start) * 1000

        return LLMResponse(
            text=text,
            model=model_name,
            provider=provider,
            latency_ms=elapsed,
        )

    def generate_json(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> dict | list:
        """Generate and parse JSON response with retry."""
        response = self.generate(
            prompt, model=model, system_prompt=system_prompt,
        )
        return _parse_json(response.text)


def _parse_json(text: str) -> dict | list:
    """Extract JSON from LLM response text. Handles markdown fences and preamble."""
    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown fences
    cleaned = re.sub(r'```(?:json)?\s*\n?', '', text)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 3: extract outermost JSON structure
    match = re.search(r'[\[{].*[\]}]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Failed to parse JSON from LLM response: {text[:200]}")
