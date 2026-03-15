"""
anonymization/config.py
Load and manage YAML anonymization configuration.
"""

import yaml
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "config/anonymization.yaml"

DEFAULT_CONFIG = {
    "blocklist": {"kb_sources": [], "customers": [], "projects": [], "internal": []},
    "session": {"customer_name": "", "placeholder": "[CUSTOMER]"},
    "settings": {
        "anonymize_api_calls": True,
        "anonymize_local_calls": False,
        "log_enabled": True,
        "log_path": "logs/anonymization.log",
    },
}


def load_config() -> dict:
    """Load anonymization config from YAML."""
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    """Save config to YAML file."""
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            config, f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )


def get_blocklist() -> List[str]:
    """Get combined blocklist of all sensitive terms."""
    config = load_config()
    blocklist = config.get("blocklist", {})

    if not blocklist:
        return []

    terms = []
    terms.extend(blocklist.get("kb_sources") or [])
    terms.extend(blocklist.get("customers") or [])
    terms.extend(blocklist.get("projects") or [])
    terms.extend(blocklist.get("internal") or [])

    return [t for t in terms if t]


def get_session() -> dict:
    """Get current session config."""
    config = load_config()
    return config.get("session", {"customer_name": "", "placeholder": "[CUSTOMER]"})


def set_session_customer(name: str) -> None:
    """Set current session customer name."""
    config = load_config()
    config["session"]["customer_name"] = name
    save_config(config)


def add_to_blocklist(name: str, category: str = "customers") -> bool:
    """Add name to blocklist. Returns True if added."""
    config = load_config()

    if category not in config["blocklist"]:
        config["blocklist"][category] = []

    if name not in config["blocklist"][category]:
        config["blocklist"][category].append(name)
        save_config(config)
        return True
    return False


def get_settings() -> dict:
    """Get anonymization settings."""
    config = load_config()
    return config.get("settings", {})
