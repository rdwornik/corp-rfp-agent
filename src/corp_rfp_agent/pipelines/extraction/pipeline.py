"""Main extraction pipeline -- orchestrates 3 stages.

Structure ported in Phase 3. Actual stage logic stays in
src/kb_extract_historical.py until agents are ported.
"""

import logging
from pathlib import Path
from typing import Optional

from corp_rfp_agent.pipelines.extraction.models import ExtractionResult

logger = logging.getLogger(__name__)


class ExtractionPipeline:
    """3-stage extraction pipeline for historical RFP Excel files.

    Stage 1: Structure analysis (prescan + LLM)
    Stage 2: Row-by-row extraction + content filtering
    Stage 3: Classification + interactive review

    NOTE: This is a structural placeholder. The actual implementation
    lives in src/kb_extract_historical.py and works via CLI.
    Full port deferred until agents are ported (Phase 4-5).
    """

    def __init__(
        self,
        family: str,
        mode: str = "create",
        model: str = "gemini-flash",
    ):
        self._family = family
        self._mode = mode
        self._model = model

    def process_file(
        self, filepath: Path, metadata: Optional[dict] = None
    ) -> ExtractionResult:
        """Run the full pipeline on one Excel file.

        Currently raises NotImplementedError -- use the CLI directly:
            python src/kb_extract_historical.py --family {family}
        """
        raise NotImplementedError(
            "Full extraction pipeline port pending. "
            "Use src/kb_extract_historical.py directly for now."
        )

    def process_inbox(self, inbox_dir: Path) -> list[ExtractionResult]:
        """Process all Excel files in an inbox directory."""
        results = []
        for xlsx in sorted(inbox_dir.glob("*.xlsx")):
            logger.info("Processing: %s", xlsx.name)
            result = self.process_file(xlsx)
            results.append(result)
        return results
