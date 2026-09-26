"""Background execution of graph runs, with event fan-out, webhooks and HITL resume.

Each run executes in a worker thread using ``graph.stream(stream_mode="updates")``;
every node update is converted into a compact, JSON-safe event appended to the
run's log. The SSE endpoint tails that log. When the graph hits the
``human_review`` interrupt the run parks in ``awaiting_review`` until
:meth:`RunManager.submit_review` resumes it with ``Command(resume=...)``.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from codeverify.config import Settings
from codeverify.graph import build_graph, graph_config
from codeverify.llm import LLMClient
from codeverify.observability import compute_run_metrics, get_logger, log_event
from codeverify.observability.logging import current_run_id
from codeverify.schemas import BugTask, ReviewDecision

_log = get_logger("api.runs")
TERMINAL = {"resolved", "failed", "escalated", "rejected", "error"}


def _summarize_update(node: str, update: dict[str, Any]) -> dict[str, Any]:
    """Project a raw node update onto a small, client-friendly payload."""
    out: dict[str, Any] = {}
    if node == "planner" and "plan" in update:
        plan = update["plan"]
        out = {"hypothesis": plan.bug_hypothesis, "steps": [s.tool for s in plan.steps], "risk": plan.risk_level}
    elif node == "inspector" and "inspection" in update:
        ins = update["inspection"]
        out = {"summary": ins.summary, "failing_tests": ins.failing_tests,
               "suspect_lines": [s.line for s in ins.suspect_locations]}  # fmt: skip
    elif node == "patch_generator":
        patch = update.get("patch")
        out = {"attempt": update.get("patch_attempts")}
        if patch is not None and "patch_error" not in update:
            out |= {"root_cause": patch.root_cause, "change_summary": patch.change_summary,
                    "confidence": patch.confidence}  # fmt: skip
        if update.get("patch_error"):
            out["error"] = update["patch_error"]
    elif node == "verifier" and "verification" in update:
        v = update["verification"]
        out = {"attempt": v.attempt, "passed": v.passed, "tests_passed": v.tests.passed,
               "tests_total": v.tests.total, "safety_violation": v.safety_violation,
               "failure_digest": None if v.passed else v.tests.failure_digest(800)}  # fmt: skip
    elif node == "human_review" and "review" in update:
        out = {"decision": update["review"].decision, "reviewer": update["review"].reviewer,
               "status": update.get("status")}  # fmt: skip
    if update.get("transitions"):
        out["latency_ms"] = update["transitions"][-1].latency_ms
        out["step"] = update["transitions"][-1].step
    if update.get("tool_calls"):
        out["tool_calls"] = [{"tool": t.tool, "success": t.success, "latency_ms": t.latency_ms}
                             for t in update["tool_calls"]]  # fmt: skip
    if update.get("llm_calls"):
        out["llm"] = [{"model": c.model, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                       "repairs": c.repairs} for c in update["llm_calls"]]  # fmt: skip
    return out


@dataclass
class RunRecord:
    run_id: str
    task: BugTask
    require_review: bool
    webhook_url: str | None = None
    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    pending_review: dict[str, Any] | None = None
    final_state: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started: float = field(default_factory=time.perf_counter)
    wall_ms: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, type_: str, **data: Any) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events), "type": type_,
                                "ts": datetime.now(UTC).isoformat(), **data})  # fmt: skip


class RunManager:
    def __init__(self, settings: Settings, llm: LLMClient | None = None, max_workers: int = 4):
        self.settings = settings
        self.checkpointer = InMemorySaver()
        self._graphs: dict[bool, CompiledStateGraph] = {}
        self._llm = llm
        self.runs: dict[str, RunRecord] = {}
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cv-run")

    def _graph(self, require_review: bool) -> CompiledStateGraph:
        if require_review not in self._graphs:
            s = self.settings.model_copy(update={"require_human_review": require_review})
            self._graphs[require_review] = build_graph(s, llm=self._llm, checkpointer=self.checkpointer)
        return self._graphs[require_review]

    # ------------------------------------------------------------------ API
    def start(self, task: BugTask, require_review: bool, webhook_url: str | None) -> RunRecord:
        run = RunRecord(run_id=uuid.uuid4().hex[:12], task=task, require_review=require_review,
                        webhook_url=webhook_url)  # fmt: skip
        self.runs[run.run_id] = run
        run.emit("run_started", task_id=task.task_id)
        self.pool.submit(self._execute, run, {"task": task, "step_count": 0})
        return run

    def submit_review(self, run_id: str, decision: ReviewDecision) -> RunRecord:
        run = self.runs[run_id]
        if run.status != "awaiting_review":
            raise ValueError(f"run {run_id} is not awaiting review (status={run.status})")
        run.status, run.pending_review = "running", None
        run.emit("review_submitted", decision=decision.decision, reviewer=decision.reviewer)
        self.pool.submit(self._execute, run, Command(resume=decision.model_dump()))
        return run

    # ------------------------------------------------------------- internals
    def _execute(self, run: RunRecord, graph_input: Any) -> None:
        token = current_run_id.set(run.run_id)
        graph = self._graph(run.require_review)
        config = graph_config(self.settings, run.run_id)
        try:
            for chunk in graph.stream(graph_input, config, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        payload = update[0].value if update else {}
                        run.pending_review = payload
                        run.status = "awaiting_review"
                        run.emit("review_required", payload={k: v for k, v in payload.items() if k != "verification"})
                        self._webhook(run, "review_required")
                        return
                    run.emit("node_update", node=node, data=_summarize_update(node, update or {}))
            final = graph.get_state(config).values
            run.final_state = final
            run.status = final.get("status", "error")
            run.wall_ms = round((time.perf_counter() - run.started) * 1000, 2)
            metrics = compute_run_metrics(final)
            run.emit("run_completed", status=run.status, termination_reason=final.get("termination_reason"),
                     metrics=metrics.model_dump(), wall_ms=run.wall_ms)  # fmt: skip
            log_event("api_run_completed", status=run.status, wall_ms=run.wall_ms)
            self._webhook(run, "run_completed")
        except Exception as exc:  # noqa: BLE001
            _log.exception("run failed")
            run.status = "error"
            run.emit("run_failed", error=f"{type(exc).__name__}: {exc}"[:500])
            self._webhook(run, "run_failed")
        finally:
            current_run_id.reset(token)

    def _webhook(self, run: RunRecord, event: str) -> None:
        if not run.webhook_url:
            return
        body = {"run_id": run.run_id, "event": event, "status": run.status, "task_id": run.task.task_id}
        for attempt in range(3):
            try:
                resp = httpx.post(run.webhook_url, json=body, timeout=5.0)
                log_event("webhook", url=run.webhook_url, status_code=resp.status_code, attempt=attempt)
                if resp.status_code < 500:
                    return
            except httpx.HTTPError as exc:
                log_event("webhook_error", url=run.webhook_url, error=str(exc)[:200], attempt=attempt)
            time.sleep(0.5 * 2**attempt)

    def aggregate_metrics(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        tool_calls = tool_ok = in_tok = out_tok = 0
        for r in self.runs.values():
            by_status[r.status] = by_status.get(r.status, 0) + 1
            if r.final_state:
                m = compute_run_metrics(r.final_state)
                tool_calls += m.tool_calls
                tool_ok += round(m.tool_success_rate * m.tool_calls)
                in_tok += m.input_tokens
                out_tok += m.output_tokens
        return {
            "runs_total": len(self.runs),
            "runs_by_status": by_status,
            "tool_calls_total": tool_calls,
            "tool_call_success_rate": round(tool_ok / tool_calls, 4) if tool_calls else None,
            "input_tokens_total": in_tok,
            "output_tokens_total": out_tok,
        }
