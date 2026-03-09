"""Data models for the extraction pipeline."""

from dataclasses import dataclass, field


@dataclass
class StructureMap:
    """Result of Stage 1: Excel structure analysis."""
    filename: str = ""
    sheets: list[dict] = field(default_factory=list)
    file_type: str = "unknown"  # source_rfp | response | combined
    estimated_questions: int = 0


@dataclass
class RawRow:
    """A single row extracted from Excel."""
    row_num: int = 0
    sheet: str = ""
    category_from_excel: str = ""
    question: str = ""
    answer: str = ""
    status: str = "candidate"  # candidate | skip_no_question | skip_empty_answer | suspect_client_data


@dataclass
class ClassifiedRow:
    """A row after LLM classification."""
    row: RawRow = field(default_factory=RawRow)
    classification: str = "UNCLEAR"  # BY_PRODUCT_ANSWER | CLIENT_DATA | CUSTOMER_SPECIFIC | INSTRUCTIONS
    keep: bool = False
    category: str = "general"
    subcategory: str = ""
    tags: list[str] = field(default_factory=list)
    solution_codes: list[str] = field(default_factory=list)
    question_generic: str = ""


@dataclass
class ExtractionResult:
    """Final result of running the pipeline on one file."""
    filename: str = ""
    family: str = ""
    total_rows: int = 0
    extracted: int = 0
    accepted: int = 0
    skipped: int = 0
    by_product_answers: int = 0
    client_data_filtered: int = 0
    empty_filtered: int = 0
    archive_id: str = ""
