"""Runtime configuration, resolved from environment variables.

All knobs live in one immutable Pydantic model so a run's configuration can be
logged, serialised into benchmark reports, and reproduced exactly.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["openrouter", "openai", "anthropic", "scripted"]
PlannerMode = Literal["rules", "llm"]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class Settings(BaseModel):
    """Every tunable of the pipeline. Override via ``CODEVERIFY_*`` env vars."""

    model_config = ConfigDict(frozen=True)

    # --- LLM backend -------------------------------------------------------
    provider: Provider = "openrouter"
    model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    fallback_models: tuple[str, ...] = (
        "google/gemma-4-31b-it:free",
        "qwen/qwen3.8-27b:free",
    )
    temperature: float = 0.0
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 3
    max_output_tokens: int = 4096

    # --- Agent behaviour ---------------------------------------------------
    planner_mode: PlannerMode = "rules"
    inspector_llm: bool = False
    max_patch_attempts: int = Field(3, ge=1, le=10)
    max_graph_steps: int = Field(24, ge=4, le=200)
    max_structured_repairs: int = Field(2, ge=0, le=5)
    require_human_review: bool = False

    # --- Sandbox -----------------------------------------------------------
    sandbox_backend: Literal["subprocess", "docker"] = "subprocess"
    sandbox_timeout_s: float = 10.0
    sandbox_memory_mb: int = 512
    docker_image: str = "python:3.11-slim"

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        """Build settings from ``CODEVERIFY_*`` environment variables."""
        fallbacks = _env("CODEVERIFY_FALLBACK_MODELS", "")
        values: dict[str, object] = {
            "provider": _env("CODEVERIFY_PROVIDER", "openrouter"),
            "model": _env("CODEVERIFY_MODEL", cls.model_fields["model"].default),
            "planner_mode": _env("CODEVERIFY_PLANNER_MODE", "rules"),
            "inspector_llm": _env("CODEVERIFY_INSPECTOR_LLM", "0") == "1",
            "max_patch_attempts": int(_env("CODEVERIFY_MAX_PATCH_ATTEMPTS", "3")),
            "max_graph_steps": int(_env("CODEVERIFY_MAX_GRAPH_STEPS", "24")),
            "require_human_review": _env("CODEVERIFY_REQUIRE_REVIEW", "0") == "1",
            "sandbox_backend": _env("CODEVERIFY_SANDBOX", "subprocess"),
            "sandbox_timeout_s": float(_env("CODEVERIFY_SANDBOX_TIMEOUT", "10")),
        }
        if fallbacks:
            values["fallback_models"] = tuple(m.strip() for m in fallbacks.split(",") if m.strip())
        values.update(overrides)
        return cls(**values)
