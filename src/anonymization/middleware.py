"""
anonymization/middleware.py
Middleware for batch pipeline integration.
"""

from typing import Tuple, Dict
from .core import anonymize, deanonymize


class AnonymizationMiddleware:
    """
    Wraps anonymize/deanonymize for use in batch pipeline.

    Usage:
        middleware = AnonymizationMiddleware()

        # Before LLM call
        clean_question, ctx = middleware.before(question)

        # After LLM call
        final_answer = middleware.after(answer, ctx)
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def before(self, text: str) -> Tuple[str, Dict]:
        """
        Anonymize before sending to LLM.
        Returns: (anonymized_text, context_for_after)
        """
        if not self.enabled:
            return text, {}

        anonymized, mapping = anonymize(text)
        return anonymized, {"mapping": mapping, "original": text}

    def after(self, text: str, context: Dict) -> str:
        """
        De-anonymize LLM response.
        Returns: restored text
        """
        if not self.enabled or not context:
            return text

        return deanonymize(text, context.get("mapping", {}))
