"""Canonical KB entry schema v2."""

import hashlib
from dataclasses import dataclass, field
from datetime import date


@dataclass
class KBEntry:
    """A single KB entry. This is the canonical v2 schema.

    All fields have defaults so v1 entries (question/answer/source only)
    can be loaded without errors.
    """
    # Required
    question: str = ""
    answer: str = ""

    # Identity
    id: str = ""

    # Classification
    family_code: str = ""
    category: str = "general"
    subcategory: str = ""
    solution_codes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # RAG enhancement
    question_variants: list[str] = field(default_factory=list)

    # Quality
    confidence: str = "draft"

    # Provenance
    source_rfps: list[str] = field(default_factory=list)
    source: str = ""  # Legacy v1 field
    source_type: str = "legacy"  # legacy | extracted | playbook

    # Metadata
    last_updated: str = ""
    cloud_native_only: bool = False
    notes: str = ""

    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = date.today().isoformat()
        if not self.id and self.question:
            self.id = self._generate_id()

    def _generate_id(self) -> str:
        """Generate stable ID from content."""
        content = f"{self.family_code}:{self.category}:{self.question}"
        hash_suffix = hashlib.sha256(content.encode()).hexdigest()[:8]
        prefix = self.family_code.upper()[:3] or "UNK"
        cat_short = self.category[:4].upper()
        return f"{prefix}-{cat_short}-{hash_suffix}"

    def is_valid(self) -> bool:
        """Check if entry has minimum required fields."""
        return bool(self.question.strip() and self.answer.strip())
