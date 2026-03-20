"""Smoke test: verify required API keys are available."""

import os

import pytest

# Keys this repo actually uses (found by auditing src/)
REQUIRED_KEYS = [
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
]

_any_key_set = any(os.environ.get(k) for k in REQUIRED_KEYS)


@pytest.mark.skipif(
    not _any_key_set,
    reason="API keys not loaded (run from PowerShell with profile)",
)
@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_api_key_available(key):
    """API key is set in environment (loaded by PS profile from global .env)."""
    value = os.environ.get(key)
    assert value is not None, (
        f"{key} not found. Run 'keys list' in PowerShell. "
        f"Keys should be in Documents/.secrets/.env"
    )
    assert len(value) > 10, f"{key} too short ({len(value)} chars)"
