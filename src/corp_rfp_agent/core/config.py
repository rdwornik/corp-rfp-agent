"""Compatibility config loader -- reads legacy formats, outputs typed config."""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM provider configuration."""
    default_model: str = "gemini"
    answer_model: str = "gemini"
    classify_model: str = "gemini-flash"
    temperature: float = 0.3
    max_tokens: int = 2000
    max_retries: int = 3


@dataclass
class KBConfig:
    """Knowledge base configuration."""
    chroma_path: str = ""
    canonical_dir: str = ""
    historical_dir: str = ""
    archive_dir: str = ""
    default_threshold: float = 0.75
    default_top_k: int = 5


@dataclass
class AnonymizationConfig:
    """Anonymization settings."""
    enabled: bool = True
    client_name: str = ""
    extra_terms: dict = field(default_factory=dict)


@dataclass
class PathsConfig:
    """Project paths."""
    project_root: Path = field(default_factory=Path.cwd)
    data_dir: Path = field(default_factory=lambda: Path.cwd() / "data")
    kb_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "kb")
    config_dir: Path = field(default_factory=lambda: Path.cwd() / "config")


@dataclass
class AppConfig:
    """Root application config. Single source of truth."""
    llm: LLMConfig = field(default_factory=LLMConfig)
    kb: KBConfig = field(default_factory=KBConfig)
    anonymization: AnonymizationConfig = field(default_factory=AnonymizationConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    api_keys: dict = field(default_factory=dict)


# API key names we look for in .env / environment
_API_KEY_NAMES = [
    "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "MOONSHOT_API_KEY",
    "TOGETHER_API_KEY", "XAI_API_KEY", "ZHIPU_API_KEY",
    "PERPLEXITY_API_KEY", "DASHSCOPE_API_KEY",
]


def load_config(
    config_path: Optional[Path] = None,
    env_file: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> AppConfig:
    """Load config from all available sources.

    Priority (highest to lowest):
    1. Environment variables
    2. config.yaml (new format, if exists)
    3. .env file (for API keys)
    4. Hardcoded defaults
    """
    config = AppConfig()

    if project_root:
        config.paths.project_root = project_root

    _load_env_keys(config, env_file)

    if config_path and config_path.exists():
        _load_yaml_config(config, config_path)
    else:
        _load_legacy_env(config)

    _resolve_paths(config)

    return config


def _load_env_keys(config: AppConfig, env_file: Optional[Path] = None) -> None:
    """Load API keys from .env or environment."""
    if env_file and env_file.exists():
        try:
            from dotenv import dotenv_values
            env_vals = dotenv_values(env_file)
            for key in _API_KEY_NAMES:
                if key in env_vals and env_vals[key]:
                    config.api_keys[key] = env_vals[key]
        except ImportError:
            logger.debug("python-dotenv not installed, reading from environment only")

    # Environment variables override .env
    for key in _API_KEY_NAMES:
        val = os.environ.get(key)
        if val:
            config.api_keys[key] = val


def _load_legacy_env(config: AppConfig) -> None:
    """Load config from legacy environment variables."""
    chroma = os.environ.get("CHROMA_DB_PATH")
    if chroma:
        config.kb.chroma_path = chroma
        logger.warning("Using legacy env var CHROMA_DB_PATH -- migrate to config.yaml")

    model = os.environ.get("DEFAULT_MODEL")
    if model:
        config.llm.default_model = model
        logger.warning("Using legacy env var DEFAULT_MODEL -- migrate to config.yaml")


def _load_yaml_config(config: AppConfig, path: Path) -> None:
    """Load from new config.yaml format."""
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "llm" in data:
            for k, v in data["llm"].items():
                if hasattr(config.llm, k):
                    setattr(config.llm, k, v)

        if "kb" in data:
            for k, v in data["kb"].items():
                if hasattr(config.kb, k):
                    setattr(config.kb, k, v)

        if "anonymization" in data:
            for k, v in data["anonymization"].items():
                if hasattr(config.anonymization, k):
                    setattr(config.anonymization, k, v)

        logger.info("Loaded config from %s", path)
    except ImportError:
        logger.warning("PyYAML not installed, skipping config.yaml")


def _resolve_paths(config: AppConfig) -> None:
    """Resolve relative paths to absolute."""
    root = config.paths.project_root
    if not config.kb.chroma_path:
        config.kb.chroma_path = str(root / "data" / "kb" / "chroma_store")
    if not config.kb.canonical_dir:
        config.kb.canonical_dir = str(root / "data" / "kb" / "canonical")
