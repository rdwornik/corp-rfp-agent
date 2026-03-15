"""Shared fixtures for acceptance tests."""

import json
import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def excel_golden():
    """Path to golden Excel fixture."""
    path = FIXTURES_DIR / "excel_golden.xlsx"
    assert path.exists(), (
        f"Fixture not found: {path}. Run: python tests/create_fixtures.py"
    )
    return path


@pytest.fixture
def excel_golden_expected():
    """Expected behavior for golden Excel fixture."""
    path = FIXTURES_DIR / "excel_golden_expected.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def word_golden():
    """Path to golden Word fixture."""
    path = FIXTURES_DIR / "word_golden.docx"
    assert path.exists(), (
        f"Fixture not found: {path}. Run: python tests/create_fixtures.py"
    )
    return path


@pytest.fixture
def word_golden_expected():
    """Expected behavior for golden Word fixture."""
    path = FIXTURES_DIR / "word_golden_expected.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_llm_response():
    """Fixed LLM response for deterministic testing."""
    return "Blue Yonder supports this capability through our cloud-native platform."
