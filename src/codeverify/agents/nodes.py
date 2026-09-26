"""The five specialised agent nodes of the state graph.

Each node is a small, single-responsibility function ``(state) -> partial
state update``. Nodes are created by :func:`make_nodes` as closures over an
:class:`AgentDeps` bundle (settings, LLM client, tool registry), which keeps
them pure with respect to the graph and trivial to unit-test with a
:class:`~codeverify.llm.ScriptedLLM`.

Handoff contract (what each node hands to the next):

====================  ==========================================================
Planner               ``plan`` (hypothesis + ordered tool steps)
Code Inspector        ``baseline_tests``, ``ast_report``, ``lint_report``, ``inspection``
Patch Generator       ``patch`` (full module) and ``patch_attempts += 1``
Execution Verifier    ``verification`` (+ history); ``final_code`` on success
Human Review          ``review`` and final ``status``
====================  ==========================================================
"""

from __future__ import annotations

import ast
import functools
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt

from codeverify.agents import prompts
from codeverify.config import Settings
from codeverify.llm import LLMClient, LLMError, StructuredOutputError, structured_call
from codeverify.observability import log_event
from codeverify.schemas import (
    AstReport,
    InspectionReport,
    LintReport,
    PatchProposal,
    Plan,
    PlanStep,
    ReviewDecision,
    SuspectLocation,
    TestReport,
    TransitionRecord,
    VerificationResult,
)
from codeverify.state import AgentState
from codeverify.tools import ToolRegistry, check_patch_safety

NodeFn = Callable[[AgentState], dict[str, Any]]


@dataclass(frozen=True)
class AgentDeps:
    settings: Settings
    llm: LLMClient
    tools: ToolRegistry


def instrumented(name: str) -> Callable[[NodeFn], NodeFn]:
    """Wrap a node: bump the step counter, time it, and emit a transition record."""

    def deco(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        def wrapper(state: AgentState) -> dict[str, Any]:
            step = state.get("step_count", 0) + 1
            log_event("node_start", node=name, step=step)
            started = time.perf_counter()
            update = fn(state)
            latency = round((time.perf_counter() - started) * 1000, 2)
            record = TransitionRecord(node=name, step=step, latency_ms=latency)
            log_event("node_end", node=name, step=step, latency_ms=latency)
            return {**update, "step_count": step, "transitions": [record]}

        return wrapper

    return deco


def can_afford_retry(step_count: int, max_steps: int) -> bool:
    """A retry costs Patch + Verify, and Human Review must still be able to close the run."""
    return step_count + 3 <= max_steps


_TB_LINE = re.compile(r'File "(?:[^"]*/)?(\w+)\.py", line (\d+)')


def _traceback_lines(report: TestReport, module: str) -> list[int]:
    """Line numbers inside the module under test that appear in failure tracebacks."""
    lines: list[int] = []
    texts = [c.traceback or "" for c in report.cases if not c.passed] + [report.collection_error or ""]
    for text in texts:
        for mod, line in _TB_LINE.findall(text):
            if mod == module:
                lines.append(int(line))
    return list(dict.fromkeys(lines))


def rules_plan(source: str, description: str) -> Plan:
    """Deterministic planner policy (zero LLM cost)."""
    try:
        ast.parse(source)
        steps = [
            PlanStep(tool="run_tests", purpose="reproduce the failure and capture tracebacks"),
            PlanStep(tool="ast_analyze", purpose="find static bug patterns near the failing code"),
            PlanStep(tool="lint", purpose="catch undefined names and bugbear issues"),
        ]
        risk = "high" if len(source.splitlines()) > 80 else "medium" if len(source.splitlines()) > 25 else "low"
    except SyntaxError:
        steps = [
            PlanStep(tool="ast_analyze", purpose="locate the syntax error"),
            PlanStep(tool="lint", purpose="confirm syntax error location"),
            PlanStep(tool="run_tests", purpose="confirm the module fails to import"),
        ]
        risk = "low"
    hypothesis = description.strip() or "behaviour deviates from the tests; localise via failing tracebacks"
    return Plan(bug_hypothesis=hypothesis[:1000], steps=steps, risk_level=risk)


def make_nodes(deps: AgentDeps) -> dict[str, NodeFn]:
    s, tools = deps.settings, deps.tools

    # ------------------------------------------------------------------ Planner
    @instrumented("planner")
    def planner(state: AgentState) -> dict[str, Any]:
        task = state["task"]
        llm_calls = []
        plan = None
        if s.planner_mode == "llm":
            try:
                plan, rec = structured_call(
                    deps.llm, Plan, system=prompts.PLANNER_SYSTEM, user=prompts.planner_prompt(task),
                    node="planner", max_repairs=s.max_structured_repairs,
                )  # fmt: skip
                llm_calls.append(rec)
            except StructuredOutputError as exc:
                llm_calls.append(exc.record)
            except LLMError as exc:
                log_event("planner_llm_unavailable", error=str(exc)[:200])
        if plan is None:  # graceful degradation to the deterministic policy
            plan = rules_plan(task.source_code, task.description)
        return {
            "plan": plan,
            "llm_calls": llm_calls,
            "status": "running",
            "patch_attempts": 0,
            "attempt_limit": s.max_patch_attempts,
            "patch_error": None,
        }

    # ------------------------------------------------------------ Code Inspector
    @instrumented("inspector")
    def inspector(state: AgentState) -> dict[str, Any]:
        task, plan = state["task"], state["plan"]
        tool_calls, executed = [], []
        baseline: TestReport | None = None
        ast_report: AstReport | None = None
        lint_report: LintReport | None = None
        for step in dict.fromkeys(st.tool for st in plan.steps):  # execute plan in order, deduped
            kwargs: dict[str, Any] = {"source_code": task.source_code}
            if step == "run_tests":
                kwargs |= {"test_code": task.test_code, "module_name": task.module_name}
            result, rec = tools.invoke(step, node="inspector", **kwargs)
            tool_calls.append(rec)
            executed.append(step)
            if step == "run_tests":
                baseline = result  # type: ignore[assignment]
            elif step == "ast_analyze":
                ast_report = result  # type: ignore[assignment]
            elif step == "lint":
                lint_report = result  # type: ignore[assignment]

        baseline = baseline or TestReport(collection_error="tests were not run")
        failing = [c.name for c in baseline.cases if not c.passed]
        suspects: dict[int, str] = {}
        for line in _traceback_lines(baseline, task.module_name):
            suspects.setdefault(line, "appears in a failing test traceback")
        for f in ast_report.findings if ast_report else []:
            suspects.setdefault(f.line, f"static finding [{f.rule}]: {f.message}")
        for i in lint_report.fatal if lint_report else []:
            suspects.setdefault(i.line, f"lint {i.code}: {i.message}")
        if ast_report and not ast_report.syntax_ok:
            summary = f"Module has a syntax error ({ast_report.syntax_error})."
        elif baseline.all_passed:
            summary = "All provided tests already pass; no fix required."
        else:
            summary = (
                f"{len(failing)} of {baseline.total} tests fail"
                + (" (module failed to import)" if baseline.collection_error else "")
                + f". Hypothesis: {plan.bug_hypothesis}"
            )
        n_lines = len(task.source_code.splitlines())
        inspection = InspectionReport(
            summary=summary[:2000],
            suspect_locations=[SuspectLocation(line=ln, reason=r) for ln, r in suspects.items() if 1 <= ln <= n_lines][:10],
            failing_tests=failing,
            already_passing=baseline.all_passed,
        )

        llm_calls = []
        if s.inspector_llm and not baseline.all_passed:
            try:
                refined, rec = structured_call(
                    deps.llm, InspectionReport, system=prompts.INSPECTOR_SYSTEM,
                    user=prompts.inspector_prompt(task, baseline.failure_digest(), ast_report, lint_report),
                    node="inspector", max_repairs=s.max_structured_repairs,
                )  # fmt: skip
                llm_calls.append(rec)
                # Keep grounded evidence; accept only in-range LLM line citations.
                valid = [x for x in refined.suspect_locations if x.line <= n_lines]
                inspection = inspection.model_copy(
                    update={"summary": refined.summary, "suspect_locations": (valid + inspection.suspect_locations)[:10]}
                )
            except StructuredOutputError as exc:
                llm_calls.append(exc.record)
            except LLMError:
                pass

        update: dict[str, Any] = {
            "baseline_tests": baseline,
            "inspection": inspection,
            "tool_calls": tool_calls,
            "executed_tools": executed,
            "llm_calls": llm_calls,
        }
        if ast_report:
            update["ast_report"] = ast_report
        if lint_report:
            update["lint_report"] = lint_report
        if inspection.already_passing:
            update |= {"status": "resolved", "final_code": task.source_code,
                       "termination_reason": "tests already pass"}  # fmt: skip
        return update

    # ----------------------------------------------------------- Patch Generator
    @instrumented("patch_generator")
    def patch_generator(state: AgentState) -> dict[str, Any]:
        task = state["task"]
        history = state.get("attempt_history", [])
        last_patch = state.get("patch")
        user = prompts.patcher_prompt(
            task,
            state.get("inspection"),
            history,
            last_patch.patched_code if last_patch else None,
            state.get("review_feedback"),
        )
        attempts = state.get("patch_attempts", 0) + 1
        try:
            patch, rec = structured_call(
                deps.llm, PatchProposal, system=prompts.PATCHER_SYSTEM, user=user,
                node="patch_generator", max_repairs=s.max_structured_repairs, code_field="patched_code",
            )  # fmt: skip
            return {"patch": patch, "patch_attempts": attempts, "llm_calls": [rec], "review_feedback": "",
                    "patch_history": [patch.patched_code]}  # fmt: skip
        except StructuredOutputError as exc:
            return {"patch_attempts": attempts, "llm_calls": [exc.record], "patch_error": str(exc)[:500]}
        except LLMError as exc:
            return {"patch_attempts": attempts, "patch_error": f"LLM unavailable: {exc}"[:500]}

    # ------------------------------------------------------ Execution Verifier
    @instrumented("verifier")
    def verifier(state: AgentState) -> dict[str, Any]:
        task = state["task"]
        attempt = state.get("patch_attempts", 0)
        patch = state.get("patch")
        # The patch must belong to *this* attempt (a failed generation leaves a stale one).
        fresh = patch is not None and state.get("patch_error") is None
        if not fresh:
            err = state.get("patch_error") or "no patch produced"
            result = VerificationResult(
                attempt=attempt, passed=False, syntax_ok=False, lint=LintReport(),
                tests=TestReport(collection_error=f"patch generation failed: {err}"),
            )  # fmt: skip
            return {"verification": result, "attempt_history": [result], "patch_error": None}

        code = patch.patched_code
        tool_calls, executed = [], []
        violation = check_patch_safety(code, task.source_code)
        if violation:
            log_event("guardrail_block", node="verifier", reason=violation)
            result = VerificationResult(
                attempt=attempt, passed=False, syntax_ok=True, lint=LintReport(),
                tests=TestReport(sandbox_violation=violation), safety_violation=violation,
            )  # fmt: skip
            return {"verification": result, "attempt_history": [result]}

        ast_r, rec = tools.invoke("ast_analyze", node="verifier", source_code=code)
        tool_calls.append(rec)
        executed.append("ast_analyze")
        syntax_ok = bool(ast_r and ast_r.syntax_ok)  # type: ignore[union-attr]
        lint_r, rec = tools.invoke("lint", node="verifier", source_code=code)
        tool_calls.append(rec)
        executed.append("lint")
        tests, rec = tools.invoke(
            "run_tests", node="verifier", source_code=code, test_code=task.test_code, module_name=task.module_name
        )
        tool_calls.append(rec)
        executed.append("run_tests")
        tests = tests or TestReport(collection_error="sandbox failure")
        passed = syntax_ok and tests.all_passed  # type: ignore[union-attr]
        result = VerificationResult(
            attempt=attempt, passed=passed, syntax_ok=syntax_ok,
            tests=tests, lint=lint_r or LintReport(),  # type: ignore[arg-type]
        )  # fmt: skip
        log_event("verification", attempt=attempt, passed=passed, tests_passed=tests.passed, tests_total=tests.total)  # type: ignore[union-attr]
        update: dict[str, Any] = {
            "verification": result,
            "attempt_history": [result],
            "tool_calls": tool_calls,
            "executed_tools": executed,
        }
        if passed:
            update["final_code"] = code
        return update

    # ------------------------------------------------------ Human-in-the-loop
    @instrumented("human_review")
    def human_review(state: AgentState) -> dict[str, Any]:
        verification = state.get("verification")
        passed = bool(verification and verification.passed)
        attempts = state.get("patch_attempts", 0)
        budget_hit = not can_afford_retry(state.get("step_count", 0), s.max_graph_steps)

        if s.require_human_review:
            # Pause the graph; resumed via Command(resume=...) from the API / CLI.
            raw = interrupt({
                "reason": "patch verified" if passed else "automatic repair exhausted; escalating",
                "patch": state["patch"].patched_code if state.get("patch") else None,
                "verification": verification.model_dump() if verification else None,
                "attempts": attempts,
            })  # fmt: skip
            decision = ReviewDecision.model_validate(raw)
        else:
            decision = ReviewDecision(decision="approve" if passed else "reject", reviewer="auto-policy")

        if decision.decision == "revise":
            return {"review": decision, "review_feedback": decision.feedback, "status": "running",
                    "attempt_limit": max(state.get("attempt_limit", s.max_patch_attempts), attempts) + 1}  # fmt: skip
        if decision.decision == "approve":
            code = state["patch"].patched_code if state.get("patch") else state["task"].source_code
            reason = "verified and approved" if passed else "human override of failing patch"
            return {"review": decision, "status": "resolved", "final_code": code, "termination_reason": reason}
        if passed:
            return {"review": decision, "status": "rejected", "termination_reason": "reviewer rejected patch"}
        reason = (
            f"step budget ({s.max_graph_steps}) exhausted" if budget_hit
            else f"max patch attempts ({attempts}) exhausted"
        )  # fmt: skip
        status = "rejected" if s.require_human_review else "failed"
        return {"review": decision, "status": status, "termination_reason": reason}

    return {
        "planner": planner,
        "inspector": inspector,
        "patch_generator": patch_generator,
        "verifier": verifier,
        "human_review": human_review,
    }
