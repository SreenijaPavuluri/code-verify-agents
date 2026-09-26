"""System prompts and prompt builders for the LLM-backed agents."""

from __future__ import annotations

from codeverify.schemas import AstReport, BugTask, InspectionReport, LintReport, VerificationResult

PLANNER_SYSTEM = """You are the PLANNER in a multi-agent bug-fixing system.
Given buggy Python code and its tests, form a hypothesis about the bug and choose an
ordered inspection plan using ONLY these tools:
- ast_analyze: static structure + bug-pattern findings (never executes code)
- lint: Ruff correctness lint
- run_tests: execute the tests in an isolated sandbox (always required)
Be concise."""

INSPECTOR_SYSTEM = """You are the CODE INSPECTOR in a multi-agent bug-fixing system.
You receive the source (with line numbers), test failures with tracebacks, and static-analysis
evidence. Localise the fault: list the most likely buggy lines with a short reason each.
Only cite line numbers that exist in the source. Do not propose code."""

PATCHER_SYSTEM = """You are the PATCH GENERATOR in a multi-agent bug-fixing system.
Fix the bug(s) in the given Python module so that the intended behaviour (described by the task
and the tests) holds. Rules:
- Return the COMPLETE corrected module, not a diff. Keep public function names and signatures.
- Make the minimal change that fixes the root cause; do not special-case test inputs.
- Do NOT import os, sys, subprocess, socket, inspect or use eval/exec/open.
- Do NOT include line numbers in the code."""


def numbered(source: str) -> str:
    return "\n".join(f"{i:>4} | {line}" for i, line in enumerate(source.splitlines(), 1))


def planner_prompt(task: BugTask) -> str:
    return (
        f"TASK: {task.description or '(no description)'}\n\n"
        f"SOURCE ({task.module_name}.py):\n{numbered(task.source_code)}\n\n"
        f"TESTS:\n{task.test_code}"
    )


def inspector_prompt(task: BugTask, failure_digest: str, ast_report: AstReport | None,
                     lint: LintReport | None) -> str:  # fmt: skip
    findings = "\n".join(f"- line {f.line}: [{f.rule}] {f.message}" for f in (ast_report.findings if ast_report else []))
    lints = "\n".join(f"- line {i.line}: {i.code} {i.message}" for i in (lint.issues if lint else []))
    return (
        f"TASK: {task.description or '(no description)'}\n\n"
        f"SOURCE:\n{numbered(task.source_code)}\n\n"
        f"TEST FAILURES:\n{failure_digest}\n\n"
        f"STATIC FINDINGS:\n{findings or '- none'}\n\nLINT:\n{lints or '- none'}"
    )


def patcher_prompt(
    task: BugTask,
    inspection: InspectionReport | None,
    history: list[VerificationResult],
    last_patch_code: str | None,
    review_feedback: str | None,
) -> str:
    parts = [
        f"TASK: {task.description or '(no description)'}",
        f"BUGGY MODULE ({task.module_name}.py), shown with line numbers for reference:\n{numbered(task.source_code)}",
        f"TESTS (must pass):\n{task.test_code}",
    ]
    if inspection:
        suspects = "\n".join(f"- line {s.line}: {s.reason}" for s in inspection.suspect_locations)
        parts.append(f"INSPECTOR REPORT:\n{inspection.summary}\nSuspect lines:\n{suspects or '- none'}")
    if history and last_patch_code:
        last = history[-1]
        why = last.safety_violation or last.tests.failure_digest()
        parts.append(
            f"YOUR PREVIOUS ATTEMPT #{last.attempt} FAILED VERIFICATION.\n"
            f"Previous patched code:\n```python\n{last_patch_code}```\n"
            f"Verification feedback (tracebacks):\n{why}\n"
            "Diagnose why it failed and produce a different, corrected module."
        )
    if review_feedback:
        parts.append(f"HUMAN REVIEWER FEEDBACK (highest priority):\n{review_feedback}")
    return "\n\n".join(parts)
