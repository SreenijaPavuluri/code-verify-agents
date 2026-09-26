"""Linting via Ruff (static; the code is piped over stdin and never executed)."""

from __future__ import annotations

import json
import subprocess

from codeverify.schemas import LintIssue, LintReport

# Correctness-oriented rules only: syntax (E9), pyflakes (F), bugbear (B).
_RULES = "E9,F,B"


def _ruff_bin() -> str | None:
    try:
        from ruff.__main__ import find_ruff_bin

        return find_ruff_bin()
    except (ImportError, FileNotFoundError):
        return None


def lint_code(source: str, filename: str = "solution.py", timeout_s: float = 15.0) -> LintReport:
    """Lint ``source`` and return structured issues.

    Falls back to a pure ``compile()`` syntax check when Ruff is unavailable.
    """
    ruff = _ruff_bin()
    if ruff is None:
        try:
            compile(source, filename, "exec")
            return LintReport(engine="compile")
        except SyntaxError as exc:
            return LintReport(
                engine="compile",
                issues=[LintIssue(code="E999", message=exc.msg, line=exc.lineno or 1, column=exc.offset or 0)],
            )
    proc = subprocess.run(
        [ruff, "check", "--isolated", "--select", _RULES, "--output-format", "json",
         "--stdin-filename", filename, "-"],
        input=source, capture_output=True, text=True, timeout=timeout_s,
    )  # fmt: skip
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ruff failed: {proc.stderr[:500]}")
    raw = json.loads(proc.stdout or "[]")
    issues = [
        LintIssue(
            code=item.get("code") or "E999",
            message=item["message"],
            line=item["location"]["row"],
            column=item["location"]["column"],
        )
        for item in raw
    ]
    return LintReport(issues=issues, engine="ruff")
