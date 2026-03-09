"""Data models for the Excel agent."""

from dataclasses import dataclass, field


@dataclass
class GreenCell:
    """A detected green cell that needs an answer."""
    sheet_name: str
    row: int
    answer_col_idx: int       # 1-based column index of the green cell
    question_col_idx: int     # 1-based column index of the question
    question_text: str
    header_row: int = 1
    category: str = ""
    existing_answer: str = ""
    green_cell_col_idx: int = 0  # Which col was actually green


@dataclass
class AnswerResult:
    """Result of answering one green cell."""
    cell: GreenCell
    answer: str = ""
    kb_matches: list[dict] = field(default_factory=list)
    model_used: str = ""
    overrides_applied: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""


@dataclass
class ExcelAgentResult:
    """Overall result of processing one Excel file."""
    input_path: str = ""
    output_path: str = ""
    total_green_cells: int = 0
    answered: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[AnswerResult] = field(default_factory=list)
