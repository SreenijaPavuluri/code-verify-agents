"""Command-line entry point: ``python -m codeverify.eval.cli`` / ``codeverify-bench``.

Examples::

    codeverify-bench --validate-only             # check the dataset, no LLM needed
    codeverify-bench --oracle                    # harness self-test with a perfect "LLM"
    codeverify-bench --out benchmarks/results    # real run against the configured model
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codeverify.config import Settings
from codeverify.eval.dataset import DEFAULT_DATASET, BenchmarkItem, load_dataset
from codeverify.eval.harness import (
    TaskResult,
    run_benchmark,
    summarize,
    validate_dataset,
    write_report,
)
from codeverify.llm import ScriptedLLM
from codeverify.observability import configure_logging


def oracle_llm(items: list[BenchmarkItem]) -> ScriptedLLM:
    """A "perfect" patcher that returns the reference fix — validates the harness plumbing."""
    fixes = {it.task.source_code: it.reference_fix for it in items}

    def respond(system: str, user: str) -> str:
        for buggy, fix in fixes.items():
            if buggy.splitlines()[0] in user and all(line in user for line in buggy.splitlines()[:3]):
                return (
                    '```json\n{"root_cause": "reference", "change_summary": "apply reference fix", '
                    '"confidence": 1.0}\n```\n```python\n' + fix + "```"
                )
        raise RuntimeError("oracle: unknown task")

    return ScriptedLLM(respond)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the code-repair benchmark.")
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p.add_argument("--tasks", nargs="*", help="subset of task ids")
    p.add_argument("--out", type=Path, default=Path("benchmarks/results"))
    p.add_argument("--tag", default=None, help="results file prefix (default: model name)")
    p.add_argument("--resume", action="store_true", help="skip tasks already in the results file")
    p.add_argument("--validate-only", action="store_true")
    p.add_argument("--oracle", action="store_true", help="use reference fixes as the LLM (no API calls)")
    p.add_argument("--max-attempts", type=int, default=None)
    p.add_argument("--report-only", action="store_true", help="re-summarise an existing results file")
    args = p.parse_args(argv)

    items = load_dataset(args.dataset, args.tasks)
    problems = validate_dataset(items)
    print(f"dataset: {len(items)} tasks, {len(problems)} problems")
    for prob in problems:
        print("  -", prob)
    if args.validate_only or problems:
        return 1 if problems else 0

    overrides: dict[str, object] = {}
    if args.max_attempts:
        overrides["max_patch_attempts"] = args.max_attempts
    llm = None
    if args.oracle:
        overrides["provider"] = "scripted"
        overrides["model"] = "oracle"
        llm = oracle_llm(items)
    settings = Settings.from_env(**overrides)

    tag = args.tag or settings.model.replace("/", "_").replace(":", "_")
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / f"{tag}.jsonl"
    if args.report_only:
        results = [TaskResult.model_validate_json(line) for line in results_path.read_text().splitlines()]
        summary = summarize(results)
        _write_artifacts(args.out, tag, settings, results, summary)
        print(json.dumps(summary, indent=2))
        return 0

    # Structured JSON event log (tokens, tool calls, node latency). Appends, so --resume keeps history.
    configure_logging("WARNING", log_file=args.out / f"{tag}_events.jsonl")

    def show(r) -> None:
        mark = "PASS" if r.resolved else "FAIL"
        print(f"  [{mark}] {r.task_id:<22} attempts={r.patch_attempts} tools={r.tool_calls} "
              f"llm={r.llm_calls} {r.latency_ms / 1000:.1f}s {r.status}", flush=True)  # fmt: skip

    results, summary = run_benchmark(
        items, settings, llm=llm, results_path=results_path, resume=args.resume, on_result=show
    )
    _write_artifacts(args.out, tag, settings, results, summary)
    print(json.dumps(summary, indent=2))
    return 0


def _write_artifacts(out: Path, tag: str, settings: Settings, results: list[TaskResult], summary: dict) -> None:
    (out / f"{tag}_summary.json").write_text(
        json.dumps({"settings": settings.model_dump(), "summary": summary}, indent=2) + "\n"
    )
    write_report(results, summary, settings, out / f"{tag}_report.md")


if __name__ == "__main__":
    sys.exit(main())
