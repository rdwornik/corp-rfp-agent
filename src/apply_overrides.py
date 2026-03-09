"""Bridge module -- apply overrides to generated answers.

This is the integration point for existing agents (rfp_excel_agent.py,
rfp_answer_word.py). They can import and call apply_overrides() on
generated answer text before inserting it into the document.

Usage in agents:
    from apply_overrides import apply_overrides
    answer = apply_overrides(answer_text, family="wms")
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded singleton store
_store = None


def _get_store():
    """Lazy-load the override store singleton."""
    global _store
    if _store is None:
        try:
            from corp_rfp_agent.overrides.store import YAMLOverrideStore

            # Find config/overrides.yaml relative to this file
            project_root = Path(__file__).resolve().parent.parent
            yaml_path = project_root / "config" / "overrides.yaml"
            if yaml_path.exists():
                _store = YAMLOverrideStore(yaml_path=yaml_path)
                logger.info(
                    "Override store loaded: %d overrides", _store.count()
                )
            else:
                logger.debug("No overrides.yaml found at %s", yaml_path)
                _store = YAMLOverrideStore()  # Empty store
        except Exception as e:
            logger.warning("Failed to load override store: %s", e)
            _store = None
    return _store


def apply_overrides(
    text: str,
    *,
    family: Optional[str] = None,
) -> str:
    """Apply text overrides to generated answer.

    Args:
        text: Generated answer text from LLM.
        family: Product family code for family-specific overrides.

    Returns:
        Modified text with overrides applied, or original if no store / no matches.
    """
    store = _get_store()
    if store is None:
        return text

    result = store.apply(text, family=family)
    if result.changed:
        logger.info(
            "Applied %d override(s) with %d replacement(s)",
            len(result.matches),
            result.total_replacements,
        )
    return result.modified


def reset_store() -> None:
    """Reset the singleton store (for testing)."""
    global _store
    _store = None
