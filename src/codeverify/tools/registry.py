"""Tool registry: one place that invokes tools and records telemetry for each call.

Nodes never call tools directly; they go through :meth:`ToolRegistry.invoke`,
which times the call, catches failures, emits a structured ``tool_call`` log
event and returns a :class:`~codeverify.schemas.ToolCallRecord` for the state.

"Success" means the tool *executed correctly and returned a valid structured
result* — e.g. ``run_tests`` reporting 3 failing tests is still a successful
tool call; a sandbox crash or a Ruff failure is not.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from codeverify.observability import log_event
from codeverify.schemas import ToolCallRecord
from codeverify.tools.ast_tools import analyze_code
from codeverify.tools.linter import lint_code
from codeverify.tools.sandbox import Sandbox


class _CodeArgs(BaseModel):
    source_code: str = Field(description="Full Python module source")


class _TestArgs(_CodeArgs):
    test_code: str = Field(description="Test module; imports the code under test from `solution`")


class ToolRegistry:
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox
        self._tools: dict[str, Callable[..., BaseModel]] = {
            "ast_analyze": lambda source_code: analyze_code(source_code),
            "lint": lambda source_code: lint_code(source_code),
            "run_tests": lambda source_code, test_code, module_name="solution": self.sandbox.run_tests(
                source_code, test_code, module_name
            ),
        }

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def invoke(self, name: str, node: str, **kwargs: Any) -> tuple[BaseModel | None, ToolCallRecord]:
        """Run tool ``name`` on behalf of graph node ``node``; never raises."""
        started = time.perf_counter()
        result: BaseModel | None = None
        error: str | None = None
        try:
            if name not in self._tools:
                raise KeyError(f"unknown tool '{name}'")
            result = self._tools[name](**kwargs)
        except Exception as exc:  # noqa: BLE001 - a tool failure must not kill the graph
            error = f"{type(exc).__name__}: {exc}"[:500]
        # A sandbox that could not produce any result counts as a failed tool call.
        if error is None and name == "run_tests" and getattr(result, "total", 1) == 0 \
                and "no result" in (getattr(result, "collection_error", "") or ""):
            error = "sandbox produced no result"
        record = ToolCallRecord(
            tool=name,
            node=node,
            success=error is None,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            error=error,
        )
        log_event("tool_call", **record.model_dump())
        return result, record

    def as_langchain_tools(self) -> list[StructuredTool]:
        """Expose the same tools to LLM tool-calling agents (e.g. ``create_react_agent``)."""
        return [
            StructuredTool.from_function(
                func=lambda source_code: analyze_code(source_code).model_dump_json(),
                name="ast_analyze",
                description="Parse Python source; return functions, imports and bug-pattern findings.",
                args_schema=_CodeArgs,
            ),
            StructuredTool.from_function(
                func=lambda source_code: lint_code(source_code).model_dump_json(),
                name="lint",
                description="Run Ruff correctness lint rules on Python source.",
                args_schema=_CodeArgs,
            ),
            StructuredTool.from_function(
                func=lambda source_code, test_code: self.sandbox.run_tests(source_code, test_code).model_dump_json(),
                name="run_tests",
                description="Execute the tests against the source inside the isolated sandbox.",
                args_schema=_TestArgs,
            ),
        ]
