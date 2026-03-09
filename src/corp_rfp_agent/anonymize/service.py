"""Anonymization service -- mandatory gate before LLM calls."""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class Anonymizer:
    """Anonymizes and de-anonymizes text for safe LLM processing.

    This is a MANDATORY gate. Agents must pass text through
    anonymize() before sending to LLM and de_anonymize() after
    receiving response.
    """

    def __init__(
        self,
        client_name: Optional[str] = None,
        extra_terms: Optional[dict[str, str]] = None,
    ):
        """Initialize with client name and additional terms to anonymize.

        Args:
            client_name: Current client name to replace with [Customer]
            extra_terms: Additional {term: replacement} mappings
        """
        self._mappings: dict[str, str] = {}
        self._reverse: dict[str, str] = {}

        if client_name:
            self.add_term(client_name, "[Customer]")

        if extra_terms:
            for term, replacement in extra_terms.items():
                self.add_term(term, replacement)

    def add_term(self, original: str, replacement: str) -> None:
        """Add a term to anonymize."""
        self._mappings[original] = replacement
        self._reverse[replacement] = original

    def anonymize(self, text: str) -> str:
        """Replace sensitive terms with placeholders."""
        result = text
        for original, replacement in self._mappings.items():
            result = re.sub(re.escape(original), replacement, result, flags=re.IGNORECASE)
        return result

    def de_anonymize(self, text: str) -> str:
        """Restore original terms from placeholders."""
        result = text
        for replacement, original in self._reverse.items():
            result = result.replace(replacement, original)
        return result

    @property
    def term_count(self) -> int:
        """Number of anonymization terms configured."""
        return len(self._mappings)
