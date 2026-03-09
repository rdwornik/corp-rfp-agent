"""Override data models."""

from dataclasses import dataclass, field


@dataclass
class Override:
    """A single text override rule."""
    id: str
    find: str
    replace: str
    description: str = ""
    whole_word: bool = False
    enabled: bool = True
    family: str = ""
    added: str = ""

    def is_valid(self) -> bool:
        """Check if override has required fields."""
        return bool(self.id.strip() and self.find.strip())


@dataclass
class OverrideMatch:
    """A single match found during override application."""
    override_id: str
    find: str
    replace: str
    count: int


@dataclass
class OverrideResult:
    """Result of applying overrides to text."""
    original: str
    modified: str
    matches: list[OverrideMatch] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Whether any overrides were applied."""
        return self.original != self.modified

    @property
    def total_replacements(self) -> int:
        """Total number of replacements made."""
        return sum(m.count for m in self.matches)
