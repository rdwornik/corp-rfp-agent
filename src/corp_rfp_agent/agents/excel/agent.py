"""Excel RFP Agent v2 -- composition-based architecture.

Pipeline per green cell:
1. Detect green cell + extract question
2. Anonymize question (mandatory)
3. Query KB for context
4. Build prompt with question + context
5. Send to LLM
6. De-anonymize response
7. Apply overrides to response
8. Write answer to cell
"""

import logging
from pathlib import Path
from typing import Optional

from corp_rfp_agent.agents.excel.cell_detector import CellDetector
from corp_rfp_agent.agents.excel.answer_writer import AnswerWriter
from corp_rfp_agent.agents.excel.models import GreenCell, AnswerResult, ExcelAgentResult

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parents[4] / "prompts" / "rfp_system_prompt_universal.txt"


class ExcelAgent:
    """Excel RFP answering agent using new architecture."""

    def __init__(
        self,
        llm_client,
        kb_client,
        anonymizer=None,
        override_store=None,
        family: str = "planning",
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ):
        self._llm = llm_client
        self._kb = kb_client
        self._anonymizer = anonymizer
        self._overrides = override_store
        self._family = family
        self._model = model
        self._system_prompt = system_prompt or self._default_system_prompt()

    def process(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        sheet_name: Optional[str] = None,
        interactive: bool = False,
        dry_run: bool = False,
    ) -> ExcelAgentResult:
        """Process an Excel RFP file."""
        if output_path is None:
            output_path = input_path.with_stem(input_path.stem + "_BY_RESPONSE")

        result = ExcelAgentResult(
            input_path=str(input_path),
            output_path=str(output_path),
        )

        detector = CellDetector(input_path)
        green_cells = detector.scan_green_cells(sheet_name)
        result.total_green_cells = len(green_cells)

        if dry_run:
            self._print_dry_run(green_cells)
            detector.close()
            return result

        writer = AnswerWriter(detector.workbook)

        for i, cell in enumerate(green_cells, 1):
            logger.info("[%d/%d] %s", i, result.total_green_cells, cell.question_text[:80])

            try:
                answer_result = self._answer_cell(cell)

                if interactive and not self._interactive_review(
                    cell, answer_result, i, result.total_green_cells
                ):
                    answer_result.skipped = True
                    answer_result.skip_reason = "User skipped"

                if not answer_result.skipped:
                    writer.write_answer(cell, answer_result.answer)
                    result.answered += 1
                else:
                    result.skipped += 1

            except Exception as e:
                logger.error("Error answering row %d: %s", cell.row, e)
                answer_result = AnswerResult(cell=cell, error=str(e))
                result.errors += 1

            result.results.append(answer_result)

        writer.save(output_path)
        detector.close()

        self._print_summary(result)
        return result

    def _answer_cell(self, cell: GreenCell) -> AnswerResult:
        """Generate answer for one green cell."""
        answer_result = AnswerResult(cell=cell)

        # Anonymize
        question = cell.question_text
        if self._anonymizer:
            question = self._anonymizer.anonymize(question)

        # KB query
        kb_matches = self._kb.query(
            question,
            family=self._family,
            top_k=5,
            threshold=0.65,
        )
        answer_result.kb_matches = [
            {"id": m.entry_id, "similarity": m.similarity} for m in kb_matches
        ]

        # Build context
        context = self._build_context(kb_matches)

        # Build prompt and call LLM
        prompt = self._build_prompt(question, context, cell.category)
        response = self._llm.generate(
            prompt,
            model=self._model,
            system_prompt=self._system_prompt,
        )
        answer = response.text
        answer_result.model_used = response.model

        # De-anonymize
        if self._anonymizer:
            answer = self._anonymizer.de_anonymize(answer)

        # Apply overrides to final answer
        if self._overrides:
            override_result = self._overrides.apply(answer, family=self._family)
            if override_result.changed:
                answer = override_result.modified
                answer_result.overrides_applied = [
                    m.override_id for m in override_result.matches
                ]

        answer_result.answer = answer
        return answer_result

    def _build_context(self, matches: list) -> str:
        """Build context string from KB matches."""
        if not matches:
            return ""
        parts = []
        for m in matches:
            parts.append(f"Q: {m.question}\nA: {m.answer}")
        return "\n---\n".join(parts)

    def _build_prompt(self, question: str, context: str, category: str = "") -> str:
        """Build the LLM prompt."""
        parts = [f"Question: {question}"]
        if category:
            parts.append(f"Category: {category}")
        if context:
            parts.append(f"\nRelevant KB entries:\n{context}")
        parts.append(
            "\nProvide a professional answer from Blue Yonder's perspective. "
            "Be specific about product capabilities. 2-5 sentences."
        )
        return "\n".join(parts)

    def _default_system_prompt(self) -> str:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
        return (
            "You are a Blue Yonder pre-sales engineer responding to an RFP. "
            "Answer professionally and specifically about Blue Yonder's products "
            "and capabilities. If unsure about a capability, say it would need "
            "to be discussed during a technical session. Do not make up features."
        )

    def _interactive_review(self, cell, result, idx, total) -> bool:
        """Show answer for review. Returns True if accepted."""
        print(f"\n--- Cell {idx}/{total} --- {cell.sheet_name} row {cell.row}")
        print(f"Q: {cell.question_text[:200]}")
        print(f"A: {result.answer[:300]}")
        while True:
            choice = input("[Y]es / [N]o / [Q]uit > ").strip().upper()
            if choice in ("Y", ""):
                return True
            elif choice == "N":
                return False
            elif choice == "Q":
                return False

    def _print_dry_run(self, cells: list[GreenCell]) -> None:
        """Print green cells without answering."""
        print(f"\n[DRY RUN] Found {len(cells)} green cells:")
        for cell in cells:
            print(f"  {cell.sheet_name} row {cell.row}: {cell.question_text[:80]}")
        print("[DRY RUN] No changes made.")

    def _print_summary(self, result: ExcelAgentResult) -> None:
        """Print processing summary."""
        print(f"\nExcel Agent Complete")
        print(f"  Green cells: {result.total_green_cells}")
        print(f"  Answered:    {result.answered}")
        print(f"  Skipped:     {result.skipped}")
        print(f"  Errors:      {result.errors}")
        print(f"  Output:      {result.output_path}")
