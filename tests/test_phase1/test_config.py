"""Tests for config loader."""

import os
from pathlib import Path

import pytest

from corp_rfp_agent.core.config import AppConfig, load_config, LLMConfig, KBConfig


def test_defaults():
    """load_config with no sources returns valid defaults."""
    config = load_config()
    assert isinstance(config, AppConfig)
    assert config.llm.default_model == "gemini"
    assert config.llm.temperature == 0.3
    assert config.kb.default_threshold == 0.75
    assert config.kb.default_top_k == 5
    assert config.anonymization.enabled is True


def test_api_keys_from_environment(monkeypatch):
    """load_config reads API keys from environment."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key-456")

    config = load_config()
    assert config.api_keys.get("GEMINI_API_KEY") == "test-gemini-key-123"
    assert config.api_keys.get("ANTHROPIC_API_KEY") == "test-anthropic-key-456"


def test_yaml_config(tmp_path):
    """load_config with config.yaml populates fields."""
    yaml_content = """
llm:
  default_model: claude
  temperature: 0.5
  max_tokens: 4000
kb:
  default_threshold: 0.80
  default_top_k: 10
anonymization:
  enabled: false
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml_content, encoding="utf-8")

    config = load_config(config_path=config_path)
    assert config.llm.default_model == "claude"
    assert config.llm.temperature == 0.5
    assert config.llm.max_tokens == 4000
    assert config.kb.default_threshold == 0.80
    assert config.kb.default_top_k == 10
    assert config.anonymization.enabled is False


def test_paths_resolve(tmp_path):
    """Paths resolve relative to project root."""
    config = load_config(project_root=tmp_path)
    assert config.kb.chroma_path == str(tmp_path / "data" / "kb" / "chroma_store")
    assert config.kb.canonical_dir == str(tmp_path / "data" / "kb" / "canonical")


def test_env_file_loading(tmp_path, monkeypatch):
    """load_config reads .env file for API keys."""
    # Clear any real env var so .env file value wins
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=from-dotenv-file\n", encoding="utf-8")

    config = load_config(env_file=env_file)
    assert config.api_keys.get("GEMINI_API_KEY") == "from-dotenv-file"


def test_env_overrides_dotenv(tmp_path, monkeypatch):
    """Environment variables override .env file values."""
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")

    config = load_config(env_file=env_file)
    assert config.api_keys["GEMINI_API_KEY"] == "from-env"
