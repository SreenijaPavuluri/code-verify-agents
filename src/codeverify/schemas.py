"""Strict Pydantic v2 contracts shared by every agent, tool, and API surface.

Two families of models live here:

* **Agent outputs** (``Plan``, ``InspectionReport``, ``PatchProposal``,
  ``ReviewDecision``) — what LLM-backed nodes must emit. They are validated
  with ``extra="forbid"`` so hallucinated fields are rejected and trigger the
  structured-output repair loop (see :mod:`codeverify.llm.structured`).
* **Tool results / telemetry** (``TestReport``, ``LintReport``,
  ``AstReport``, ``ToolCallRecord`` …) — deterministic, machine-generated.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ToolName = Literal["ast_analyze", "lint", "run_tests"]
TOOL_NAMES: tuple[ToolName, ...] = ("ast_analyze", "lint", "run_tests")


class StrictModel(BaseModel):
    """Base model that forbids unknown keys (LLM hallucination guard)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Task input
# ---------------------------------------------------------------------------


class BugTask(StrictModel):
    """A unit of work: buggy source plus the tests the agent is allowed to see."""

    task_id: str = Field(min_length=1, max_length=128)
    source_code: str = Field(min_length=1, max_length=50_000)
    test_code: str = Field(min_length=1, max_length=50_000)
    description: str = Field(default="", max_length=4_000)
    module_name: str = Field(default="solution", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------------------------------------------------------------------------
# Tool results
# ---------------------------------------------------------------------------


class TestCaseResult(StrictModel):
    name: str
    passed: bool
    error_type: str | None = None
    message: str | None = None
    traceback: str | None = None


class TestReport(StrictModel):
    """Outcome of executing a test file against candidate code in the sandbox."""

    __test__ = False  # stop pytest from collecting this as a test class

    passed: int = 0
    failed: int = 0
    errors: int = 0
    total: int = 0
    cases: list[TestCaseResult] = Field(default_factory=list)
    collection_error: str | None = None
    timed_out: bool = False
    sandbox_violation: str | None = None
    duration_ms: float = 0.0

    @property
    def all_passed(self) -> bool:
        return (
            self.total > 0
            and self.passed == self.total
            and self.collection_error is None
            and not self.timed_out
            and self.sandbox_violation is None
        )

    def failure_digest(self, max_chars: int = 3_000) -> str:
        """Compact, LLM-friendly summary of what failed (tracebacks included)."""
        parts: list[str] = []
        if self.collection_error:
            parts.append(f"COLLECTION/IMPORT ERROR:\n{self.collection_error}")
        if self.timed_out:
            parts.append("TIMEOUT: execution exceeded the sandbox time limit (infinite loop?).")
        if self.sandbox_violation:
            parts.append(f"SANDBOX VIOLATION: {self.sandbox_violation}")
        for case in self.cases:
            if not case.passed:
                parts.append(
                    f"FAILED {case.name}: {case.error_type}: {case.message}\n{case.traceback or ''}"
                )
        text = "\n\n".join(parts) or "No failures."
        return text[:max_chars]


class LintIssue(StrictModel):
    code: str
    message: str
    line: int
    column: int = 0


class LintReport(StrictModel):
    issues: list[LintIssue] = Field(default_factory=list)
    engine: str = "ruff"

    @property
    def fatal(self) -> list[LintIssue]:
        """Issues that almost certainly break execution (syntax / undefined names)."""
        return [i for i in self.issues if i.code.startswith(("E9", "F82", "F63", "F7"))]


class FunctionInfo(StrictModel):
    name: str
    lineno: int
    end_lineno: int
    args: list[str]
    complexity: int


class AstFinding(StrictModel):
    """A static bug-pattern hint (e.g. mutable default argument)."""

    rule: str
    line: int
    message: str


class AstReport(StrictModel):
    syntax_ok: bool
    syntax_error: str | None = None
    functions: list[FunctionInfo] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    findings: list[AstFinding] = Field(default_factory=list)
    forbidden_imports: list[str] = Field(default_factory=list)


class ToolCallRecord(StrictModel):
    """Telemetry for a single tool invocation (drives tool-success metrics)."""

    tool: str
    node: str
    success: bool
    latency_ms: float
    error: str | None = None


class LLMCallRecord(StrictModel):
    """Telemetry for a single LLM request (drives token/latency metrics)."""

    node: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    repairs: int = 0
    success: bool = True


class TransitionRecord(StrictModel):
    """One node execution in the state graph (drives transition-latency metrics)."""

    node: str
    step: int
    latency_ms: float
    next_hint: str | None = None


# ---------------------------------------------------------------------------
# Agent outputs (LLM-facing contracts)
# ---------------------------------------------------------------------------


class PlanStep(StrictModel):
    tool: ToolName
    purpose: str = Field(min_length=3, max_length=300)


class Plan(StrictModel):
    """Planner output: a bug hypothesis plus an ordered inspection tool plan."""

    bug_hypothesis: str = Field(min_length=3, max_length=1_000)
    steps: list[PlanStep] = Field(min_length=1, max_length=6)
    risk_level: Literal["low", "medium", "high"] = "medium"

    @field_validator("steps")
    @classmethod
    def _must_run_tests(cls, steps: list[PlanStep]) -> list[PlanStep]:
        # Guardrail: a plan that never executes the tests cannot localise a bug.
        if not any(s.tool == "run_tests" for s in steps):
            raise ValueError("plan must include at least one 'run_tests' step")
        return steps


class SuspectLocation(StrictModel):
    line: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class InspectionReport(StrictModel):
    """Code Inspector output: grounded fault localisation."""

    summary: str = Field(min_length=3, max_length=2_000)
    suspect_locations: list[SuspectLocation] = Field(default_factory=list, max_length=10)
    failing_tests: list[str] = Field(default_factory=list)
    already_passing: bool = False


class PatchProposal(StrictModel):
    """Patch Generator output. ``patched_code`` is the *full* corrected module."""

    root_cause: str = Field(min_length=3, max_length=1_500)
    change_summary: str = Field(min_length=3, max_length=1_500)
    patched_code: str = Field(min_length=1, max_length=50_000)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("patched_code")
    @classmethod
    def _strip_fences(cls, code: str) -> str:
        code = code.strip("\n")
        if code.startswith("```"):
            lines = code.splitlines()[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            code = "\n".join(lines)
        return code + "\n"


class VerificationResult(StrictModel):
    attempt: int
    passed: bool
    tests: TestReport
    lint: LintReport
    syntax_ok: bool
    safety_violation: str | None = None


class ReviewDecision(StrictModel):
    """Human (or auto-policy) verdict at the Human-in-the-Loop node."""

    decision: Literal["approve", "reject", "revise"]
    feedback: str = Field(default="", max_length=4_000)
    reviewer: str = Field(default="human", max_length=128)


RunStatus = Literal[
    "running", "resolved", "failed", "escalated", "rejected", "awaiting_review", "error"
]
