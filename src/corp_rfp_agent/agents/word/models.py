"""Word agent data models."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Section:
    """A section in a Word document, detected by heading formatting."""
    level: int                    # 0 = chapter, 1 = section, 2 = subsection
    title: str
    para_index: int               # paragraph index in doc
    content_paragraphs: list = field(default_factory=list)   # [(index, text), ...]
    children: list = field(default_factory=list)             # list[Section]

    @property
    def full_content(self) -> str:
        """All content in this section (excluding children)."""
        return "\n".join(text for _, text in self.content_paragraphs)

    @property
    def full_content_with_children(self) -> str:
        """All content including children -- for context."""
        parts = [self.full_content]
        for child in self.children:
            parts.append(f"\n### {child.title}\n{child.full_content}")
        return "\n".join(parts)


@dataclass
class AnswerableBlock:
    """A section block that needs a BY response."""
    section: Section
    breadcrumb: str              # "Architecture > Integration > APIs"
    content: str                 # all text in this section
    insert_after_para: int       # where to insert the BY response
    answer: str = ""
    kb_matches: list = field(default_factory=list)


@dataclass
class SectionAnswer:
    """Result of answering a single section."""
    block: AnswerableBlock
    answer: str = ""
    kb_matches: list = field(default_factory=list)
    model_used: str = ""
    overrides_applied: list = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""


@dataclass
class WordAgentResult:
    """Result of processing a Word document."""
    input_path: str = ""
    output_path: str = ""
    total_sections: int = 0
    answerable: int = 0
    answered: int = 0
    skipped: int = 0
    errors: int = 0
    results: list = field(default_factory=list)   # list[SectionAnswer]
