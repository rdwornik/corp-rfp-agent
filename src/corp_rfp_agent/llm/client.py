"""LLM client interface -- all LLM access goes through this."""

from typing import Protocol, Optional

from corp_rfp_agent.core.types import LLMResponse


class LLMClient(Protocol):
    """Protocol for LLM providers. All agents use this interface."""

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            prompt: User prompt text
            model: Model override (uses default from config if None)
            system_prompt: System prompt (optional)
            temperature: Sampling temperature
            max_tokens: Maximum response tokens

        Returns:
            LLMResponse with generated text and metadata
        """
        ...

    def generate_json(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> dict:
        """Generate a JSON response, with parsing and retry logic.

        Returns parsed dict/list. Raises ValueError if JSON parsing fails
        after retries.
        """
        ...
