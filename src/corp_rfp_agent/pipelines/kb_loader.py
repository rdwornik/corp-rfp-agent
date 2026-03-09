"""Centralized KB entry loading -- single way to load all KB data."""

import json
import logging
from pathlib import Path
from typing import Optional

from corp_rfp_agent.kb.entry import KBEntry
from corp_rfp_agent.domain.entry_adapter import EntryAdapter

logger = logging.getLogger(__name__)

# Loaded lazily from family_config.json
_family_config: Optional[dict] = None


def _load_family_config() -> dict:
    """Load family_config.json, with hardcoded fallback."""
    global _family_config
    if _family_config is not None:
        return _family_config

    config_path = Path(__file__).resolve().parents[3] / "data" / "kb" / "schema" / "family_config.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            _family_config = json.load(f).get("families", {})
    else:
        # Hardcoded fallback
        _family_config = {
            "planning": {"canonical_file": "RFP_Database_Cognitive_Planning_CANONICAL.json"},
            "wms": {"canonical_file": "RFP_Database_WMS_CANONICAL.json"},
            "logistics": {"canonical_file": "RFP_Database_Logistics_CANONICAL.json"},
            "scpo": {"canonical_file": "RFP_Database_SCPO_CANONICAL.json"},
            "catman": {"canonical_file": "RFP_Database_CatMan_CANONICAL.json"},
            "workforce": {"canonical_file": "RFP_Database_Workforce_CANONICAL.json"},
            "commerce": {"canonical_file": "RFP_Database_Commerce_CANONICAL.json"},
            "flexis": {"canonical_file": "RFP_Database_Flexis_CANONICAL.json"},
            "network": {"canonical_file": "RFP_Database_Network_CANONICAL.json"},
            "doddle": {"canonical_file": "RFP_Database_Doddle_CANONICAL.json"},
            "aiml": {"canonical_file": "RFP_Database_AIML_CANONICAL.json"},
        }
    return _family_config


def get_canonical_filename(family: str) -> Optional[str]:
    """Get the canonical filename for a family code."""
    config = _load_family_config()
    entry = config.get(family)
    if entry:
        return entry.get("canonical_file")
    return None


class KBLoader:
    """Loads KB entries from all sources: legacy JSONL, v2 JSON, canonical files."""

    def __init__(self, canonical_dir: Path):
        self._canonical_dir = canonical_dir

    def load_all(self) -> list[KBEntry]:
        """Load all entries from all canonical files."""
        all_entries = []

        # Load v2 canonical JSON files
        for json_file in sorted(self._canonical_dir.glob("RFP_Database_*_CANONICAL.json")):
            if "UNIFIED" in json_file.name:
                continue
            entries = EntryAdapter.load_json(json_file)
            all_entries.extend(entries)

        logger.info("Total: %d entries loaded from all sources", len(all_entries))
        return all_entries

    def load_family(self, family: str) -> list[KBEntry]:
        """Load entries for a specific family only."""
        filename = get_canonical_filename(family)
        if not filename:
            logger.warning("Unknown family: %s", family)
            return []

        path = self._canonical_dir / filename
        if not path.exists():
            logger.info("No canonical file for family %s at %s", family, path)
            return []

        return EntryAdapter.load_json(path)

    def stats(self) -> dict[str, int]:
        """Return entry counts per source file."""
        result = {}
        for json_file in sorted(self._canonical_dir.glob("RFP_Database_*_CANONICAL.json")):
            if "UNIFIED" in json_file.name:
                continue
            entries = EntryAdapter.load_json(json_file)
            # Extract family name from filename
            stem = json_file.stem
            family = stem.replace("RFP_Database_", "").replace("_CANONICAL", "")
            result[family] = len(entries)
        return result
