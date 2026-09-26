from codeverify.config import Settings
from codeverify.llm.base import BudgetExceeded, LLMClient, LLMError, LLMResponse
from codeverify.llm.chat_client import ChatClient
from codeverify.llm.scripted import ScriptedLLM
from codeverify.llm.structured import StructuredOutputError, structured_call


def build_llm(settings: Settings) -> LLMClient:
    """Factory: a real provider client, or raise if ``provider='scripted'`` (inject it)."""
    if settings.provider == "scripted":
        raise LLMError("provider 'scripted' must be injected explicitly (tests only)")
    return ChatClient(settings)


__all__ = [
    "BudgetExceeded",
    "ChatClient",
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "ScriptedLLM",
    "StructuredOutputError",
    "build_llm",
    "structured_call",
]
