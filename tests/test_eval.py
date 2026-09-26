from codeverify.eval import load_dataset, run_benchmark, summarize, validate_dataset
from codeverify.eval.cli import oracle_llm
from codeverify.eval.harness import TaskResult


def test_dataset_has_20_valid_tasks():
    items = load_dataset()
    assert len(items) == 20 and len({i.task.task_id for i in items}) == 20
    assert validate_dataset(items) == []


def test_oracle_benchmark_is_perfect(settings, tmp_path):
    items = load_dataset(ids=["t01_sum_to", "t09_late_binding", "t15_syntax_max"])
    results, summary = run_benchmark(items, settings, llm=oracle_llm(items), results_path=tmp_path / "r.jsonl")
    assert summary["pass_at_1"] == 1.0 and summary["plan_adherence"] == 1.0
    assert summary["tool_call_success_rate"] == 1.0 and summary["overfit_rate"] == 0.0
    assert (tmp_path / "r.jsonl").read_text().count("\n") == 3


def _r(**kw) -> TaskResult:
    base = dict(task_id="t", category="c", difficulty="easy", status="resolved", resolved=True, visible_pass=True,
                first_attempt_resolved=True, patch_attempts=1, tool_calls=6, tool_success_rate=1.0, llm_calls=1,
                input_tokens=10, output_tokens=5, structured_repairs=0, plan_adherence=1.0, latency_ms=100.0,
                graph_steps=5)  # fmt: skip
    return TaskResult(**{**base, **kw})


def test_summary_math():
    rs = [
        _r(),
        _r(first_attempt_resolved=False, patch_attempts=2, latency_ms=300.0),
        _r(status="failed", resolved=False, visible_pass=True, first_attempt_resolved=False, patch_attempts=3),
    ]
    s = summarize(rs)
    assert s["pass_at_1"] == round(2 / 3, 4)
    assert s["first_attempt_pass_at_1"] == round(1 / 3, 4)
    assert s["self_correction_lift"] == round(1 / 3, 4)
    assert s["overfit_rate"] == round(1 / 3, 4)
    assert s["loop_convergence_rate"] == round(2 / 3, 4) and s["avg_iterations_to_converge"] == 1.5
