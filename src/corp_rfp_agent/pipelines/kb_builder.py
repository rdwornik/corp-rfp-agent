"""KB build and merge pipeline."""

import json
import logging
from pathlib import Path

from corp_rfp_agent.kb.entry import KBEntry
from corp_rfp_agent.domain.entry_adapter import EntryAdapter
from corp_rfp_agent.pipelines.kb_loader import KBLoader, get_canonical_filename

logger = logging.getLogger(__name__)


class KBBuilder:
    """Builds and merges canonical KB files."""

    def __init__(self, canonical_dir: Path):
        self._canonical_dir = canonical_dir

    def merge_unified(self) -> int:
        """Merge all family canonical files into UNIFIED canonical.

        Reads all RFP_Database_*_CANONICAL.json files,
        deduplicates by entry ID, writes UNIFIED.
        Returns total entry count.
        """
        all_entries: dict[str, KBEntry] = {}

        for json_file in sorted(self._canonical_dir.glob("RFP_Database_*_CANONICAL.json")):
            if "UNIFIED" in json_file.name:
                continue
            entries = EntryAdapter.load_json(json_file)
            for entry in entries:
                key = entry.id if entry.id else f"legacy-{hash(entry.question + entry.answer)}"
                all_entries[key] = entry

        # Write unified
        unified_path = self._canonical_dir / "RFP_Database_UNIFIED_CANONICAL.json"
        entries_list = [EntryAdapter.to_dict(e) for e in all_entries.values()]
        self._canonical_dir.mkdir(parents=True, exist_ok=True)
        with open(unified_path, "w", encoding="utf-8") as f:
            json.dump(entries_list, f, indent=2, ensure_ascii=False)

        logger.info("Merged %d entries into %s", len(entries_list), unified_path.name)
        return len(entries_list)

    def append_to_family(self, family: str, new_entries: list[KBEntry]) -> int:
        """Append new entries to a family canonical file.

        Loads existing entries, deduplicates by ID, saves back.
        Returns count of newly added entries.
        """
        filename = get_canonical_filename(family)
        if not filename:
            logger.error("Unknown family: %s", family)
            return 0

        path = self._canonical_dir / filename

        # Load existing
        existing: list[KBEntry] = []
        if path.exists():
            existing = EntryAdapter.load_json(path)

        existing_ids = {e.id for e in existing if e.id}

        added = 0
        for entry in new_entries:
            if entry.id and entry.id in existing_ids:
                logger.debug("Skipping duplicate: %s", entry.id)
                continue
            existing.append(entry)
            existing_ids.add(entry.id)
            added += 1

        # Save
        entries_list = [EntryAdapter.to_dict(e) for e in existing]
        self._canonical_dir.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries_list, f, indent=2, ensure_ascii=False)

        logger.info("Appended %d entries to %s (total: %d)", added, filename, len(existing))
        return added
