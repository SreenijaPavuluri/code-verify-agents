"""Derive run-level metrics from the telemetry channels stored in graph state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from codeverify.schemas import LLMCallRecord, ToolCallRecord, TransitionRecord


class ToolStats(BaseModel):
    calls: int = 0
    successes: int = 0
    total_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.calls if self.calls else 0.0


class RunMetrics(BaseModel):
    """Aggregated observability view over one pipeline run."""

    tool_calls: int = 0
    tool_success_rate: float = 0.0
    per_tool: dict[str, dict[str, float]] = Field(default_factory=dict)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    structured_repairs: int = 0
    llm_latency_ms: float = 0.0
    node_latency_ms: dict[str, float] = Field(default_factory=dict)
    graph_steps: int = 0


def _coerce(items: Iterable[Any], model: type[BaseModel]) -> list[Any]:
    return [i if isinstance(i, model) else model.model_validate(i) for i in items or []]


def compute_run_metrics(state: Mapping[str, Any]) -> RunMetrics:
    tools: list[ToolCallRecord] = _coerce(state.get("tool_calls", []), ToolCallRecord)
    llms: list[LLMCallRecord] = _coerce(state.get("llm_calls", []), LLMCallRecord)
    trans: list[TransitionRecord] = _coerce(state.get("transitions", []), TransitionRecord)

    per_tool: dict[str, ToolStats] = defaultdict(ToolStats)
    for rec in tools:
        s = per_tool[rec.tool]
        s.calls += 1
        s.successes += int(rec.success)
        s.total_latency_ms += rec.latency_ms

    node_latency: dict[str, float] = defaultdict(float)
    for t in trans:
        node_latency[t.node] += t.latency_ms

    successes = sum(r.success for r in tools)
    return RunMetrics(
        tool_calls=len(tools),
        tool_success_rate=successes / len(tools) if tools else 0.0,
        per_tool={
            name: {
                "calls": s.calls,
                "success_rate": round(s.success_rate, 4),
                "avg_latency_ms": round(s.total_latency_ms / s.calls, 2),
            }
            for name, s in per_tool.items()
        },
        llm_calls=len(llms),
        input_tokens=sum(r.input_tokens for r in llms),
        output_tokens=sum(r.output_tokens for r in llms),
        structured_repairs=sum(r.repairs for r in llms),
        llm_latency_ms=round(sum(r.latency_ms for r in llms), 2),
        node_latency_ms={k: round(v, 2) for k, v in node_latency.items()},
        graph_steps=len(trans),
    )
