"""KB client interface -- all knowledge base access goes through this."""

from typing import Protocol, Optional

from corp_rfp_agent.core.types import KBMatch


class KBClient(Protocol):
    """Protocol for KB retrieval. Agents query KB through this."""

    def query(
        self,
        question: str,
        *,
        family: Optional[str] = None,
        category: Optional[str] = None,
        top_k: int = 5,
        threshold: float = 0.75,
    ) -> list[KBMatch]:
        """Query KB for relevant entries.

        Args:
            question: The question to find matches for
            family: Filter by product family code
            category: Filter by category
            top_k: Maximum results to return
            threshold: Minimum similarity score

        Returns:
            List of KBMatch sorted by similarity (descending)
        """
        ...

    def count(self, family: Optional[str] = None) -> int:
        """Count entries, optionally filtered by family."""
        ...

    def families(self) -> dict[str, int]:
        """Return entry counts per family."""
        ...
