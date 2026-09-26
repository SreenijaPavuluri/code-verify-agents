"""LangChain-backed chat client with retries, backoff, and model fallback.

Supports any OpenAI-compatible endpoint (OpenRouter, OpenAI, vLLM, Ollama)
through ``langchain-openai`` and Anthropic through ``langchain-anthropic``.
"""

from __future__ import annotations

import os
import random
import threading
import time

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from codeverify.config import Settings
from codeverify.llm.base import BudgetExceeded, LLMError, LLMResponse
from codeverify.observability import get_logger, log_event

_log = get_logger("llm")

_BASE_URLS = {"openrouter": "https://openrouter.ai/api/v1", "openai": None}
_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def _build_model(settings: Settings, model: str) -> BaseChatModel:
    key = os.environ.get(_KEY_ENV[settings.provider])
    if not key:
        raise LLMError(f"missing {_KEY_ENV[settings.provider]} for provider '{settings.provider}'")
    if settings.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model, api_key=key, temperature=settings.temperature,
            max_tokens=settings.max_output_tokens, timeout=settings.llm_timeout_s, max_retries=0,
        )  # fmt: skip
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=key,
        base_url=os.environ.get("CODEVERIFY_BASE_URL") or _BASE_URLS[settings.provider],
        temperature=settings.temperature,
        max_completion_tokens=settings.max_output_tokens,
        timeout=settings.llm_timeout_s,
        max_retries=0,  # we own retry/backoff so it is observable
    )


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # Anthropic-style content blocks
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def _retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(s in text for s in ("429", "rate", "timeout", "timed out", "overloaded", "502", "503", "504",
                                   "connection", "empty response", "provider returned error"))  # fmt: skip


class ChatClient:
    """Implements :class:`codeverify.llm.base.LLMClient` over a fallback chain of models."""

    def __init__(self, settings: Settings, request_budget: int | None = None):
        self.settings = settings
        self.models = [settings.model, *[m for m in settings.fallback_models if m != settings.model]]
        self._cache: dict[str, BaseChatModel] = {}
        budget = request_budget if request_budget is not None else os.environ.get("CODEVERIFY_LLM_BUDGET")
        self.remaining_budget: int | None = int(budget) if budget not in (None, "") else None
        self._lock = threading.Lock()
        self.requests_made = 0

    def _spend(self) -> None:
        with self._lock:
            if self.remaining_budget is not None:
                if self.remaining_budget <= 0:
                    raise BudgetExceeded("LLM request budget exhausted")
                self.remaining_budget -= 1
            self.requests_made += 1

    def _model(self, name: str) -> BaseChatModel:
        if name not in self._cache:
            self._cache[name] = _build_model(self.settings, name)
        return self._cache[name]

    def complete(self, system: str, user: str) -> LLMResponse:
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        last_error: Exception | None = None
        for model_name in self.models:
            for attempt in range(self.settings.llm_max_retries):
                self._spend()
                started = time.perf_counter()
                try:
                    msg = self._model(model_name).invoke(messages)
                    text = _text(msg.content).strip()
                    if not text:
                        raise LLMError("empty response")
                    usage = msg.usage_metadata or {}
                    return LLMResponse(
                        text=text,
                        model=(msg.response_metadata or {}).get("model_name") or model_name,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    )
                except BudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    log_event("llm_error", model=model_name, attempt=attempt, error=str(exc)[:300])
                    if not _retryable(exc):
                        break  # non-transient: move on to the next model
                    time.sleep(min(30.0, 2.0 * 2**attempt) + random.random())
        raise LLMError(f"all models failed; last error: {last_error}")
