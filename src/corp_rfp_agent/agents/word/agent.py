"""Word RFP Agent v2 -- composition-based architecture.

Pipeline per answerable section:
1. Parse document into section tree
2. Collect answerable sections
3. For each: anonymize -> KB query -> build prompt -> LLM -> de-anonymize -> overrides
4. Insert answers as blue BY Response paragraphs (backwards to avoid index shift)
"""

import logging
from pathlib import Path
from typing import Optional

from docx import Document

_PROMPT_PATH = Path(__file__).resolve().parents[4] / "prompts" / "rfp_system_prompt_universal.txt"

from corp_rfp_agent.agents.word.models import (
    AnswerableBlock,
    SectionAnswer,
    WordAgentResult,
)
from corp_rfp_agent.agents.word.section_parser import (
    build_section_tree,
    collect_answerable_sections,
    count_sections_recursive,
    print_section_tree,
)
from corp_rfp_agent.agents.word.answer_inserter import (
    insert_answer_after,
    insert_blank_after,
    has_existing_response,
)

logger = logging.getLogger(__name__)


class WordAgent:
    """Word RFP answering agent using new architecture."""

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
        interactive: bool = False,
        dry_run: bool = False,
        skip_existing: bool = True,
    ) -> WordAgentResult:
        """Process a Word RFP document."""
        input_path = Path(input_path)
        if output_path is None:
            output_path = input_path.with_stem(input_path.stem + "_BY_RESPONSE")

        result = WordAgentResult(
            input_path=str(input_path),
            output_path=str(output_path),
        )

        doc = Document(str(input_path))
        sections = build_section_tree(doc)
        result.total_sections = count_sections_recursive(sections)

        blocks = collect_answerable_sections(sections)
        result.answerable = len(blocks)

        if dry_run:
            self._print_dry_run(blocks)
            return result

        # Filter out sections that already have a BY Response
        if skip_existing:
            blocks = [b for b in blocks if not has_existing_response(doc, b.insert_after_para)]

        # Answer each block
        for i, block in enumerate(blocks, 1):
            logger.info("[%d/%d] %s", i, len(blocks), block.breadcrumb[:80])

            try:
                section_answer = self._answer_block(block)

                if interactive and not self._interactive_review(
                    block, section_answer, i, len(blocks)
                ):
                    section_answer.skipped = True
                    section_answer.skip_reason = "User skipped"

                if not section_answer.skipped:
                    block.answer = section_answer.answer
                    result.answered += 1
                else:
                    result.skipped += 1

            except Exception as e:
                logger.error("Error answering section '%s': %s", block.breadcrumb, e)
                section_answer = SectionAnswer(block=block, error=str(e))
                result.errors += 1

            result.results.append(section_answer)

        # Insert answers into document (BACKWARDS to avoid index shift)
        answered_blocks = [b for b in blocks if b.answer]
        blocks_sorted = sorted(answered_blocks, key=lambda b: b.insert_after_para, reverse=True)

        for block in blocks_sorted:
            insert_blank_after(doc, block.insert_after_para)
            insert_answer_after(doc, block.insert_after_para, block.answer)

        doc.save(str(output_path))
        self._print_summary(result)
        return result

    def _answer_block(self, block: AnswerableBlock) -> SectionAnswer:
        """Generate answer for one answerable section."""
        section_answer = SectionAnswer(block=block)

        # Anonymize
        content = block.content
        if self._anonymizer:
            content = self._anonymizer.anonymize(content)

        # KB query using section title + content
        query_text = f"{block.section.title}\n{content}"
        kb_matches = self._kb.query(
            query_text,
            family=self._family,
            top_k=5,
            threshold=0.65,
        )
        section_answer.kb_matches = [
            {"id": m.entry_id, "similarity": m.similarity} for m in kb_matches
        ]

        # Build context and prompt
        context = self._build_context(kb_matches)
        prompt = self._build_prompt(block.breadcrumb, content, context)
        response = self._llm.generate(
            prompt,
            model=self._model,
            system_prompt=self._system_prompt,
        )
        answer = response.text
        section_answer.model_used = response.model

        # De-anonymize
        if self._anonymizer:
            answer = self._anonymizer.de_anonymize(answer)

        # Apply overrides
        if self._overrides:
            override_result = self._overrides.apply(answer, family=self._family)
            if override_result.changed:
                answer = override_result.modified
                section_answer.overrides_applied = [
                    m.override_id for m in override_result.matches
                ]

        section_answer.answer = answer
        return section_answer

    def _build_context(self, matches: list) -> str:
        """Build context string from KB matches."""
        if not matches:
            return ""
        parts = []
        for m in matches:
            parts.append(f"Q: {m.question}\nA: {m.answer}")
        return "\n---\n".join(parts)

    def _build_prompt(self, breadcrumb: str, content: str, context: str) -> str:
        """Build the LLM prompt for a Word section."""
        parts = [
            f"Section: {breadcrumb}",
            f"\nClient Requirement:\n{content}",
        ]
        if context:
            parts.append(f"\nRelevant KB entries:\n{context}")
        parts.append(
            "\nProvide a professional response from Blue Yonder's perspective. "
            "Be specific about product capabilities. 2-5 sentences. "
            "Start with 'Blue Yonder...' and do not make up features."
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

    def _interactive_review(self, block, result, idx, total) -> bool:
        """Show answer for review. Returns True if accepted."""
        print(f"\n--- Section {idx}/{total} --- {block.breadcrumb}")
        print(f"Content: {block.content[:200]}")
        print(f"Answer: {result.answer[:300]}")
        while True:
            choice = input("[Y]es / [N]o / [Q]uit > ").strip().upper()
            if choice in ("Y", ""):
                return True
            elif choice in ("N", "Q"):
                return False

    def _print_dry_run(self, blocks: list[AnswerableBlock]) -> None:
        """Print answerable sections without generating answers."""
        print(f"\n[DRY RUN] Found {len(blocks)} answerable sections:")
        for i, block in enumerate(blocks, 1):
            content_preview = block.content[:120].replace("\n", " | ")
            print(f"  [{i:3d}] {block.breadcrumb}")
            print(f"        {content_preview}...")
        print("[DRY RUN] No changes made.")

    def _print_summary(self, result: WordAgentResult) -> None:
        """Print processing summary."""
        print(f"\nWord Agent Complete")
        print(f"  Sections:    {result.total_sections}")
        print(f"  Answerable:  {result.answerable}")
        print(f"  Answered:    {result.answered}")
        print(f"  Skipped:     {result.skipped}")
        print(f"  Errors:      {result.errors}")
        print(f"  Output:      {result.output_path}")
