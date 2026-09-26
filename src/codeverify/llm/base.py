"""Provider-agnostic LLM interface used by every agent node."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class LLMResponse(BaseModel):
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class LLMError(RuntimeError):
    """Raised when every model in the fallback chain failed."""


class BudgetExceeded(LLMError):
    """Raised when the per-process LLM request budget is exhausted (cost guardrail)."""


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> LLMResponse:
        """Single-turn completion: a system prompt plus a user message."""
        ...
