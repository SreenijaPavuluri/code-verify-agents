"""The LangGraph state schema.

Append-only telemetry channels use ``operator.add`` reducers so every node can
emit records without clobbering earlier ones; everything else is last-write-wins.
Keeping telemetry *in the state* (rather than in a side channel) means it is
checkpointed alongside the run and survives human-in-the-loop interrupts.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from codeverify.schemas import (
    AstReport,
    BugTask,
    InspectionReport,
    LintReport,
    LLMCallRecord,
    PatchProposal,
    Plan,
    ReviewDecision,
    RunStatus,
    TestReport,
    ToolCallRecord,
    TransitionRecord,
    VerificationResult,
)


class AgentState(TypedDict, total=False):
    # Input
    task: BugTask

    # Planner / Inspector artefacts
    plan: Plan
    baseline_tests: TestReport
    ast_report: AstReport
    lint_report: LintReport
    inspection: InspectionReport

    # Patch loop
    patch: PatchProposal
    patch_attempts: int
    attempt_limit: int
    patch_error: str | None
    verification: VerificationResult
    attempt_history: Annotated[list[VerificationResult], operator.add]
    patch_history: Annotated[list[str], operator.add]
    review_feedback: str

    # Human in the loop
    review: ReviewDecision

    # Control / guardrails
    step_count: int
    status: RunStatus
    termination_reason: str
    final_code: str

    # Telemetry (append-only)
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    llm_calls: Annotated[list[LLMCallRecord], operator.add]
    transitions: Annotated[list[TransitionRecord], operator.add]
    executed_tools: Annotated[list[str], operator.add]
