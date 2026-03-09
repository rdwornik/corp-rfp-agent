"""Write answers back to Excel cells.

Preserves all existing formatting, formulas, and non-green cell content.
"""

import logging
from pathlib import Path

import openpyxl

from corp_rfp_agent.agents.excel.models import GreenCell

logger = logging.getLogger(__name__)


class AnswerWriter:
    """Writes generated answers into Excel green cells."""

    def __init__(self, workbook: openpyxl.Workbook):
        self._wb = workbook

    def write_answer(self, cell: GreenCell, answer: str) -> None:
        """Write answer text into the answer column cell.

        Only modifies the cell's value. Preserves formatting.
        """
        ws = self._wb[cell.sheet_name]
        target = ws.cell(row=cell.row, column=cell.answer_col_idx)
        target.value = answer
        logger.debug("Wrote answer to %s row %d col %d", cell.sheet_name, cell.row, cell.answer_col_idx)

    def save(self, output_path: Path) -> None:
        """Save workbook to output path."""
        self._wb.save(str(output_path))
        logger.info("Saved workbook: %s", output_path.name)
