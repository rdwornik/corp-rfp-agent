"""Archive registry -- tracks all processed RFP files."""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ArchiveEntry:
    """A single archived RFP file."""
    archive_id: str = ""
    original_filename: str = ""
    archived_filename: str = ""
    client: str = ""
    client_industry: str = ""
    family_code: str = ""
    solution_codes: list[str] = field(default_factory=list)
    rfp_type: str = "response"
    date_estimated: str = ""
    date_processed: str = ""
    region: str = "EMEA"
    extraction_stats: dict = field(default_factory=dict)
    canonical_entries_added: int = 0
    structure_file: str = ""
    extraction_file: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)


class ArchiveRegistry:
    """Manages the archive_registry.json file."""

    def __init__(self, archive_dir: Path):
        self._path = archive_dir / "archive_registry.json"
        self._archive_dir = archive_dir
        self._entries: list[ArchiveEntry] = []

        if self._path.exists():
            self._load()

    def _load(self) -> None:
        """Load registry from JSON."""
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)

        raw_entries = data.get("entries", [])
        self._entries = []
        for entry_data in raw_entries:
            # Filter to only known fields
            known_fields = {f.name for f in ArchiveEntry.__dataclass_fields__.values()}
            filtered = {k: v for k, v in entry_data.items() if k in known_fields}
            self._entries.append(ArchiveEntry(**filtered))

        logger.info("Loaded %d archive entries", len(self._entries))

    def save(self) -> None:
        """Write registry back to JSON."""
        data = {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "total_files": len(self._entries),
            "total_qa_extracted": sum(
                e.extraction_stats.get("total_qa_extracted", 0) for e in self._entries
            ),
            "entries": [asdict(e) for e in self._entries],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Saved %d archive entries", len(self._entries))

    def add(self, entry: ArchiveEntry) -> str:
        """Add a new archive entry and save. Returns assigned ID."""
        if not entry.date_processed:
            entry.date_processed = datetime.now().strftime("%Y-%m-%d")
        if not entry.archive_id:
            entry.archive_id = self.next_id()
        self._entries.append(entry)
        self.save()
        return entry.archive_id

    def next_id(self) -> str:
        """Generate next archive ID."""
        return f"ARC-{len(self._entries) + 1:04d}"

    def search(
        self,
        *,
        client: Optional[str] = None,
        family: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[ArchiveEntry]:
        """Search archive entries by criteria."""
        results = list(self._entries)
        if client:
            results = [e for e in results if client.lower() in e.client.lower()]
        if family:
            results = [e for e in results if e.family_code == family]
        if date_from:
            results = [e for e in results if e.date_estimated >= date_from]
        if date_to:
            results = [e for e in results if e.date_estimated <= date_to]
        return results

    def get_by_id(self, archive_id: str) -> Optional[ArchiveEntry]:
        """Find entry by archive ID."""
        for entry in self._entries:
            if entry.archive_id == archive_id:
                return entry
        return None

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[ArchiveEntry]:
        return list(self._entries)
