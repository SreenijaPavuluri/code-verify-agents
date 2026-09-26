"""Deterministic, offline LLM stand-in for tests and CI (zero API cost)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from codeverify.llm.base import LLMError, LLMResponse

Responder = Callable[[str, str], str]


class ScriptedLLM:
    """Replays canned responses, or computes them with a ``(system, user) -> text`` function.

    Every prompt is recorded in :attr:`calls` so tests can assert on what the
    agents actually sent (e.g. that tracebacks reach the Patch Generator).
    """

    def __init__(self, responses: Iterable[str] | Responder):
        self._fn: Responder | None = responses if callable(responses) else None
        self._queue: list[str] = [] if callable(responses) else list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        if self._fn is not None:
            text = self._fn(system, user)
        elif self._queue:
            text = self._queue.pop(0)
        else:
            raise LLMError("ScriptedLLM ran out of responses")
        return LLMResponse(
            text=text,
            model="scripted",
            input_tokens=len((system + user).split()),
            output_tokens=len(text.split()),
            latency_ms=0.1,
        )
