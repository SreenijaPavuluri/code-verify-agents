"""Assemble the LangGraph state machine and provide a one-call runner.

Topology (conditional edges are dashed in the README diagram)::

    START → planner → inspector ─┬─(tests already pass)──────────────→ END
                                 └→ patch_generator → verifier ─┬─(pass)────────────→ human_review
                                          ↑                     ├─(fail, budget left)→ patch_generator
                                          │                     └─(fail, exhausted)──→ human_review
                                          └──────────(revise + feedback)──────────── human_review → END

Guardrails against runaway loops, from innermost to outermost:

1. ``attempt_limit`` — max Patch→Verify iterations (``max_patch_attempts``);
2. ``max_graph_steps`` — a global node-execution budget checked by every router;
3. LangGraph ``recursion_limit`` — a hard backstop set slightly above (2).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codeverify.agents import AgentDeps, can_afford_retry, make_nodes
from codeverify.config import Settings
from codeverify.llm import LLMClient, build_llm
from codeverify.observability import compute_run_metrics, log_event
from codeverify.observability.logging import current_run_id
from codeverify.schemas import BugTask
from codeverify.state import AgentState
from codeverify.tools import ToolRegistry, get_sandbox


def _budget_left(state: AgentState, settings: Settings) -> bool:
    return can_afford_retry(state.get("step_count", 0), settings.max_graph_steps)


def make_routers(settings: Settings) -> dict[str, Any]:
    def after_inspector(state: AgentState) -> Literal["patch_generator", "__end__"]:
        if state["inspection"].already_passing:
            return END
        return "patch_generator"

    def after_verifier(state: AgentState) -> Literal["human_review", "patch_generator"]:
        if state["verification"].passed:
            return "human_review"
        limit = state.get("attempt_limit", settings.max_patch_attempts)
        if state.get("patch_attempts", 0) < limit and _budget_left(state, settings):
            log_event("route_retry", attempt=state.get("patch_attempts", 0), limit=limit)
            return "patch_generator"  # dynamic feedback loop: carries tracebacks forward
        log_event("route_escalate", attempts=state.get("patch_attempts", 0), steps=state.get("step_count", 0))
        return "human_review"

    def after_review(state: AgentState) -> Literal["patch_generator", "__end__"]:
        if state["review"].decision == "revise" and _budget_left(state, settings):
            return "patch_generator"
        return END

    return {"after_inspector": after_inspector, "after_verifier": after_verifier, "after_review": after_review}


def build_graph(
    settings: Settings,
    llm: LLMClient | None = None,
    tools: ToolRegistry | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the multi-agent graph. Inject ``llm``/``tools`` for testing."""
    deps = AgentDeps(
        settings=settings,
        llm=llm or build_llm(settings),
        tools=tools or ToolRegistry(get_sandbox(settings)),
    )
    nodes = make_nodes(deps)
    routers = make_routers(settings)

    g = StateGraph(AgentState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, "planner")
    g.add_edge("planner", "inspector")
    g.add_conditional_edges("inspector", routers["after_inspector"], ["patch_generator", END])
    g.add_edge("patch_generator", "verifier")
    g.add_conditional_edges("verifier", routers["after_verifier"], ["human_review", "patch_generator"])
    g.add_conditional_edges("human_review", routers["after_review"], ["patch_generator", END])

    if settings.require_human_review and checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()  # interrupts need a checkpointer to resume
    return g.compile(checkpointer=checkpointer)


def graph_config(settings: Settings, thread_id: str | None = None) -> dict[str, Any]:
    return {
        "recursion_limit": settings.max_graph_steps + 4,
        "configurable": {"thread_id": thread_id or uuid.uuid4().hex},
    }


def run_task(
    task: BugTask,
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    graph: CompiledStateGraph | None = None,
) -> dict[str, Any]:
    """Run a task to completion (auto review policy) and return the final state + metrics."""
    settings = settings or Settings.from_env()
    graph = graph or build_graph(settings, llm=llm)
    run_id = uuid.uuid4().hex[:12]
    token = current_run_id.set(run_id)
    started = time.perf_counter()
    try:
        final = graph.invoke({"task": task, "step_count": 0}, graph_config(settings, run_id))
    finally:
        current_run_id.reset(token)
    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    metrics = compute_run_metrics(final)
    log_event(
        "run_complete", run_id=run_id, task_id=task.task_id, status=final.get("status"),
        attempts=final.get("patch_attempts", 0), wall_ms=wall_ms, **metrics.model_dump(exclude={"per_tool", "node_latency_ms"}),
    )  # fmt: skip
    return {"run_id": run_id, "state": final, "metrics": metrics, "wall_ms": wall_ms}
