"""Automated benchmark harness.

For each task we run the full graph once (temperature 0, single sample) and
grade ``final_code`` on visible **and hidden** tests in the sandbox.

Reported metrics
----------------
* **Pass@1** — fraction of tasks whose final patch passes all visible + hidden tests.
* **First-attempt Pass@1** — same grading applied to the *first* patch only;
  the gap to Pass@1 is the lift from the verify→retry feedback loop.
* **Overfit rate** — patches that pass visible tests but fail hidden ones.
* **Avg tool calls / LLM calls / tokens**, **latency** (mean, p50, p95).
* **Tool-call success rate** — tool executed and returned a valid structured result.
* **Plan adherence** — share of planner-selected tools the inspector executed.
* **Loop convergence** — share of repair loops that ended with a verified patch,
  mean iterations to converge, and runs cut off by the step budget.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverify.config import Settings
from codeverify.eval.dataset import BenchmarkItem
from codeverify.graph import run_task
from codeverify.observability import get_logger
from codeverify.tools import SubprocessSandbox

_log = get_logger("eval")


class TaskResult(BaseModel):
    task_id: str
    category: str
    difficulty: str
    status: str
    termination_reason: str = ""
    resolved: bool  # passes visible + hidden tests
    visible_pass: bool
    first_attempt_resolved: bool
    patch_attempts: int
    tool_calls: int
    tool_success_rate: float
    llm_calls: int
    input_tokens: int
    output_tokens: int
    structured_repairs: int
    plan_adherence: float
    latency_ms: float
    graph_steps: int
    llm_failure: bool = False
    models: list[str] = []
    error: str | None = None


def validate_dataset(items: list[BenchmarkItem]) -> list[str]:
    """Sanity-check the dataset: bugs must reproduce, reference fixes must pass."""
    sb = SubprocessSandbox(timeout_s=15, per_test_timeout_s=3)
    problems: list[str] = []
    for it in items:
        if sb.run_tests(it.task.source_code, it.task.test_code).all_passed:
            problems.append(f"{it.task.task_id}: buggy code passes visible tests")
        for name, tests in (("visible", it.task.test_code), ("hidden", it.hidden_tests)):
            if not sb.run_tests(it.reference_fix, tests).all_passed:
                problems.append(f"{it.task.task_id}: reference fix fails {name} tests")
    return problems


def _grade(sb: SubprocessSandbox, code: str | None, item: BenchmarkItem) -> tuple[bool, bool]:
    if not code:
        return False, False
    visible = sb.run_tests(code, item.task.test_code).all_passed
    hidden = sb.run_tests(code, item.hidden_tests).all_passed
    return visible and hidden, visible


def evaluate_item(item: BenchmarkItem, settings: Settings, llm=None) -> TaskResult:
    sb = SubprocessSandbox(timeout_s=15, per_test_timeout_s=3)
    started = time.perf_counter()
    try:
        out = run_task(item.task, settings, llm=llm)
    except Exception as exc:  # noqa: BLE001 - one bad task must not abort the benchmark
        _log.exception("task crashed", extra={"task_id": item.task.task_id})
        return TaskResult(
            task_id=item.task.task_id, category=item.category, difficulty=item.difficulty,
            status="error", resolved=False, visible_pass=False, first_attempt_resolved=False,
            patch_attempts=0, tool_calls=0, tool_success_rate=0, llm_calls=0, input_tokens=0,
            output_tokens=0, structured_repairs=0, plan_adherence=0,
            latency_ms=round((time.perf_counter() - started) * 1000, 2), graph_steps=0, error=str(exc)[:500],
        )  # fmt: skip
    state, m = out["state"], out["metrics"]
    final_code = state.get("final_code") if state.get("status") == "resolved" else None
    resolved, visible = _grade(sb, final_code, item)

    history = state.get("attempt_history", [])
    first_generated = bool(history) and not (history[0].tests.collection_error or "").startswith(
        "patch generation failed"
    )
    first_code = state["patch_history"][0] if first_generated and state.get("patch_history") else None
    first_resolved, _ = _grade(sb, first_code, item)

    planned = [s.tool for s in state["plan"].steps]
    inspected = [t.tool for t in state.get("tool_calls", []) if t.node == "inspector"]
    adherence = sum(p in inspected for p in dict.fromkeys(planned)) / len(set(planned))
    llm_failure = any("LLM unavailable" in (h.tests.collection_error or "") for h in history)

    return TaskResult(
        task_id=item.task.task_id,
        category=item.category,
        difficulty=item.difficulty,
        status=state.get("status", "unknown"),
        termination_reason=state.get("termination_reason", ""),
        resolved=resolved,
        visible_pass=visible,
        first_attempt_resolved=first_resolved,
        patch_attempts=state.get("patch_attempts", 0),
        tool_calls=m.tool_calls,
        tool_success_rate=m.tool_success_rate,
        llm_calls=m.llm_calls,
        input_tokens=m.input_tokens,
        output_tokens=m.output_tokens,
        structured_repairs=m.structured_repairs,
        plan_adherence=round(adherence, 4),
        latency_ms=out["wall_ms"],
        graph_steps=m.graph_steps,
        llm_failure=llm_failure,
        models=[c.model for c in state.get("llm_calls", [])],
    )


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def summarize(results: list[TaskResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"n_tasks": 0}
    lat = [r.latency_ms for r in results]
    looped = [r for r in results if r.patch_attempts > 0]
    converged = [r for r in looped if r.status == "resolved"]
    total_tools = sum(r.tool_calls for r in results)
    by_diff: dict[str, list[TaskResult]] = {}
    for r in results:
        by_diff.setdefault(r.difficulty, []).append(r)
    return {
        "n_tasks": n,
        "pass_at_1": round(sum(r.resolved for r in results) / n, 4),
        "first_attempt_pass_at_1": round(sum(r.first_attempt_resolved for r in results) / n, 4),
        "self_correction_lift": round(
            (sum(r.resolved for r in results) - sum(r.first_attempt_resolved for r in results)) / n, 4
        ),
        "visible_pass_rate": round(sum(r.visible_pass for r in results) / n, 4),
        "overfit_rate": round(sum(r.visible_pass and not r.resolved for r in results) / n, 4),
        "avg_tool_calls": round(total_tools / n, 2),
        "tool_call_success_rate": round(
            sum(r.tool_success_rate * r.tool_calls for r in results) / total_tools, 4
        ) if total_tools else 0.0,
        "avg_llm_calls": round(sum(r.llm_calls for r in results) / n, 2),
        "avg_input_tokens": round(sum(r.input_tokens for r in results) / n, 1),
        "avg_output_tokens": round(sum(r.output_tokens for r in results) / n, 1),
        "structured_output_repairs": sum(r.structured_repairs for r in results),
        "plan_adherence": round(statistics.mean(r.plan_adherence for r in results), 4),
        "loop_convergence_rate": round(len(converged) / len(looped), 4) if looped else 0.0,
        "avg_iterations_to_converge": round(statistics.mean(r.patch_attempts for r in converged), 2)
        if converged else 0.0,
        "budget_terminations": sum("budget" in r.termination_reason for r in results),
        "unbounded_loops": 0,  # guaranteed by construction; asserted below
        "latency_ms": {
            "mean": round(statistics.mean(lat), 2),
            "p50": _pct(lat, 0.5),
            "p95": _pct(lat, 0.95),
        },
        "llm_failures": sum(r.llm_failure for r in results),
        # Tasks where the provider never answered (outage / quota) say nothing about the agent;
        # report Pass@1 over the tasks that were actually evaluated as well.
        "n_evaluated": len(evaluated := [r for r in results if not r.llm_failure]),
        "pass_at_1_evaluated": round(sum(r.resolved for r in evaluated) / len(evaluated), 4) if evaluated else 0.0,
        "first_attempt_pass_at_1_evaluated": round(
            sum(r.first_attempt_resolved for r in evaluated) / len(evaluated), 4
        ) if evaluated else 0.0,
        "models_served": dict(Counter(m for r in results for m in r.models)),
        "by_difficulty": {
            d: {"n": len(rs), "pass_at_1": round(sum(r.resolved for r in rs) / len(rs), 4)}
            for d, rs in sorted(by_diff.items())
        },
    }


def run_benchmark(
    items: list[BenchmarkItem],
    settings: Settings,
    *,
    llm=None,
    results_path: Path | None = None,
    resume: bool = False,
    on_result: Callable[[TaskResult], None] | None = None,
) -> tuple[list[TaskResult], dict[str, Any]]:
    """Evaluate every item sequentially, persisting results incrementally (JSONL)."""
    done: dict[str, TaskResult] = {}
    if results_path and resume and results_path.exists():
        for line in results_path.read_text().splitlines():
            r = TaskResult.model_validate_json(line)
            if not r.llm_failure and r.status != "error":
                done[r.task_id] = r
    results: list[TaskResult] = []
    for item in items:
        if item.task.task_id in done:
            results.append(done[item.task.task_id])
            continue
        r = evaluate_item(item, settings, llm=llm)
        results.append(r)
        if on_result:
            on_result(r)
    if results_path:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text("".join(r.model_dump_json() + "\n" for r in results))
    summary = summarize(results)
    assert all(r.graph_steps <= settings.max_graph_steps for r in results), "step budget violated"
    return results, summary


def write_report(results: list[TaskResult], summary: dict[str, Any], settings: Settings, path: Path) -> None:
    """Render a Markdown report next to the JSON artefacts."""
    lines = [
        "# Benchmark report",
        "",
        f"- Model: `{settings.model}` (provider `{settings.provider}`, temperature {settings.temperature})",
        f"- Planner mode: `{settings.planner_mode}`, inspector LLM: `{settings.inspector_llm}`",
        f"- Max patch attempts: {settings.max_patch_attempts}, max graph steps: {settings.max_graph_steps}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for k, v in summary.items():
        if isinstance(v, dict):
            v = json.dumps(v)
        elif isinstance(v, float) and ("rate" in k or "pass" in k or "lift" in k or k == "plan_adherence"):
            v = f"{v:.1%}"
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## Per task",
        "",
        "| Task | Category | Difficulty | Resolved | 1st-try | Attempts | Tool calls | LLM calls | Latency (s) | Status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.task_id} | {r.category} | {r.difficulty} | {'✅' if r.resolved else '❌'} | "
            f"{'✅' if r.first_attempt_resolved else '❌'} | {r.patch_attempts} | {r.tool_calls} | "
            f"{r.llm_calls} | {r.latency_ms / 1000:.1f} | {r.status} |"
        )
    path.write_text("\n".join(lines) + "\n")
