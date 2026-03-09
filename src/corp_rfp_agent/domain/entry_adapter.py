"""Adapter mapping legacy KB formats to canonical KBEntry."""

import json
import logging
from pathlib import Path

from corp_rfp_agent.kb.entry import KBEntry

logger = logging.getLogger(__name__)


class EntryAdapter:
    """Maps between legacy JSONL/JSON formats and KBEntry."""

    @staticmethod
    def from_legacy_jsonl(data: dict) -> KBEntry:
        """Convert legacy v1 JSONL entry (question/answer/source) to KBEntry."""
        return KBEntry(
            question=data.get("question", ""),
            answer=data.get("answer", ""),
            source=data.get("source", ""),
            source_type="legacy",
        )

    @staticmethod
    def from_v2_dict(data: dict) -> KBEntry:
        """Convert v2 canonical JSON entry to KBEntry.

        Handles both new field names (question/answer) and legacy aliases
        (canonical_question/canonical_answer).
        """
        return KBEntry(
            id=data.get("id", data.get("kb_id", "")),
            question=data.get("question", data.get("canonical_question", "")),
            answer=data.get("answer", data.get("canonical_answer", "")),
            question_variants=data.get("question_variants", []),
            solution_codes=data.get("solution_codes", []),
            family_code=data.get("family_code", data.get("domain", "")),
            category=data.get("category", "general"),
            subcategory=data.get("subcategory", ""),
            tags=data.get("tags", []),
            confidence=data.get("confidence", "draft"),
            source_rfps=data.get("source_rfps", []),
            last_updated=data.get("last_updated", ""),
            cloud_native_only=data.get("cloud_native_only", False),
            notes=data.get("notes", ""),
            source_type="extracted",
        )

    @staticmethod
    def to_dict(entry: KBEntry) -> dict:
        """Convert KBEntry to dict for JSON serialization."""
        return {
            "id": entry.id,
            "question": entry.question,
            "answer": entry.answer,
            "question_variants": entry.question_variants,
            "solution_codes": entry.solution_codes,
            "family_code": entry.family_code,
            "category": entry.category,
            "subcategory": entry.subcategory,
            "tags": entry.tags,
            "confidence": entry.confidence,
            "source_rfps": entry.source_rfps,
            "last_updated": entry.last_updated,
            "cloud_native_only": entry.cloud_native_only,
            "notes": entry.notes,
        }

    @classmethod
    def load_jsonl(cls, path: Path) -> list[KBEntry]:
        """Load entries from JSONL file, auto-detecting format."""
        entries = []
        invalid = 0
        with open(path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if "id" in data or "family_code" in data or "kb_id" in data:
                        entry = cls.from_v2_dict(data)
                    else:
                        entry = cls.from_legacy_jsonl(data)

                    if entry.is_valid():
                        entries.append(entry)
                    else:
                        invalid += 1
                        logger.warning("Invalid entry at line %d: empty question or answer", line_num)
                except json.JSONDecodeError:
                    invalid += 1
                    logger.warning("JSON parse error at line %d", line_num)

        if invalid:
            logger.warning("Skipped %d invalid entries from %s", invalid, path.name)
        logger.info("Loaded %d entries from %s", len(entries), path.name)
        return entries

    @classmethod
    def load_json(cls, path: Path) -> list[KBEntry]:
        """Load entries from JSON array file."""
        with open(path, encoding="utf-8") as f:
            data_list = json.load(f)

        entries = []
        for data in data_list:
            if "id" in data or "family_code" in data or "kb_id" in data:
                entry = cls.from_v2_dict(data)
            else:
                entry = cls.from_legacy_jsonl(data)
            if entry.is_valid():
                entries.append(entry)

        logger.info("Loaded %d entries from %s", len(entries), path.name)
        return entries
