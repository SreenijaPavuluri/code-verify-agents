# Benchmark report

- Model: `liquid/lfm-2.5-2.6b:free` (provider `openrouter`, temperature 0.0)
- Planner mode: `rules`, inspector LLM: `False`
- Max patch attempts: 3, max graph steps: 24

## Summary

| Metric | Value |
|---|---|
| n_tasks | 11 |
| pass_at_1 | 72.7% |
| first_attempt_pass_at_1 | 36.4% |
| self_correction_lift | 36.4% |
| visible_pass_rate | 72.7% |
| overfit_rate | 0.0% |
| avg_tool_calls | 5.45 |
| tool_call_success_rate | 100.0% |
| avg_llm_calls | 1.09 |
| avg_input_tokens | 1562.3 |
| avg_output_tokens | 4156.7 |
| structured_output_repairs | 18 |
| plan_adherence | 100.0% |
| loop_convergence_rate | 72.7% |
| avg_iterations_to_converge | 1.5 |
| budget_terminations | 0 |
| unbounded_loops | 0 |
| latency_ms | {"mean": 29911.57, "p50": 18500.21, "p95": 67294.41} |
| llm_failures | 3 |
| n_evaluated | 8 |
| pass_at_1_evaluated | 100.0% |
| first_attempt_pass_at_1_evaluated | 50.0% |
| models_served | {"liquid/lfm-2.5-2.6b:free": 12} |
| by_difficulty | {"hard": {"n": 2, "pass_at_1": 0.5}, "medium": {"n": 9, "pass_at_1": 0.7778}} |

## Per task

| Task | Category | Difficulty | Resolved | 1st-try | Attempts | Tool calls | LLM calls | Latency (s) | Status |
|---|---|---|---|---|---|---|---|---|---|
| t02_binary_search | boundary | medium | ✅ | ✅ | 1 | 6 | 1 | 14.1 | resolved |
| t06_merge_sorted | missing-tail | medium | ✅ | ✅ | 1 | 6 | 1 | 6.5 | resolved |
| t08_flatten | recursion | medium | ✅ | ✅ | 1 | 6 | 1 | 6.7 | resolved |
| t09_late_binding | closure | hard | ✅ | ❌ | 2 | 6 | 2 | 40.7 | resolved |
| t10_remove_negatives | mutate-while-iterating | medium | ✅ | ✅ | 1 | 6 | 1 | 3.3 | resolved |
| t11_median | missing-precondition | medium | ✅ | ❌ | 2 | 6 | 2 | 18.5 | resolved |
| t13_count_digits | infinite-loop | medium | ✅ | ❌ | 2 | 9 | 2 | 87.2 | resolved |
| t14_safe_divide | exception-handling | medium | ✅ | ❌ | 2 | 6 | 2 | 11.9 | resolved |
| t16_rotate | modular-arithmetic | medium | ❌ | ❌ | 3 | 3 | 0 | 46.0 | failed |
| t17_parse_config | parsing | medium | ❌ | ❌ | 3 | 3 | 0 | 46.6 | failed |
| t20_normalize | multi-bug | hard | ❌ | ❌ | 3 | 3 | 0 | 47.4 | failed |
