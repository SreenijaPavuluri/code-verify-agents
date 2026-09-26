# Benchmark report

- Model: `nvidia/nemotron-3-super-120b-a12b:free` (provider `openrouter`, temperature 0.0)
- Planner mode: `rules`, inspector LLM: `False`
- Max patch attempts: 3, max graph steps: 24

## Summary

| Metric | Value |
|---|---|
| n_tasks | 20 |
| pass_at_1 | 100.0% |
| first_attempt_pass_at_1 | 100.0% |
| self_correction_lift | 0.0% |
| visible_pass_rate | 100.0% |
| overfit_rate | 0.0% |
| avg_tool_calls | 6.0 |
| tool_call_success_rate | 100.0% |
| avg_llm_calls | 1.0 |
| avg_input_tokens | 546.3 |
| avg_output_tokens | 442.9 |
| structured_output_repairs | 0 |
| plan_adherence | 100.0% |
| loop_convergence_rate | 100.0% |
| avg_iterations_to_converge | 1 |
| budget_terminations | 0 |
| unbounded_loops | 0 |
| latency_ms | {"mean": 17758.86, "p50": 11247.14, "p95": 53696.04} |
| llm_failures | 0 |
| n_evaluated | 20 |
| pass_at_1_evaluated | 100.0% |
| first_attempt_pass_at_1_evaluated | 100.0% |
| models_served | {"qwen/qwen3.8-27b:free": 4, "nvidia/nemotron-3-super-120b-a12b:free": 16} |
| by_difficulty | {"easy": {"n": 9, "pass_at_1": 1.0}, "hard": {"n": 2, "pass_at_1": 1.0}, "medium": {"n": 9, "pass_at_1": 1.0}} |

## Per task

| Task | Category | Difficulty | Resolved | 1st-try | Attempts | Tool calls | LLM calls | Latency (s) | Status |
|---|---|---|---|---|---|---|---|---|---|
| t01_sum_to | off-by-one | easy | ✅ | ✅ | 1 | 6 | 1 | 53.2 | resolved |
| t02_binary_search | boundary | medium | ✅ | ✅ | 1 | 6 | 1 | 11.3 | resolved |
| t03_mutable_default | mutable-default | easy | ✅ | ✅ | 1 | 6 | 1 | 3.7 | resolved |
| t04_palindrome | string-logic | easy | ✅ | ✅ | 1 | 6 | 1 | 10.4 | resolved |
| t05_fizzbuzz | branch-order | easy | ✅ | ✅ | 1 | 6 | 1 | 3.4 | resolved |
| t06_merge_sorted | missing-tail | medium | ✅ | ✅ | 1 | 6 | 1 | 18.9 | resolved |
| t07_word_count | accumulator | easy | ✅ | ✅ | 1 | 6 | 1 | 3.4 | resolved |
| t08_flatten | recursion | medium | ✅ | ✅ | 1 | 6 | 1 | 12.0 | resolved |
| t09_late_binding | closure | hard | ✅ | ✅ | 1 | 6 | 1 | 11.6 | resolved |
| t10_remove_negatives | mutate-while-iterating | medium | ✅ | ✅ | 1 | 6 | 1 | 41.9 | resolved |
| t11_median | missing-precondition | medium | ✅ | ✅ | 1 | 6 | 1 | 52.0 | resolved |
| t12_fibonacci | base-case | easy | ✅ | ✅ | 1 | 6 | 1 | 3.2 | resolved |
| t13_count_digits | infinite-loop | medium | ✅ | ✅ | 1 | 6 | 1 | 11.2 | resolved |
| t14_safe_divide | exception-handling | medium | ✅ | ✅ | 1 | 6 | 1 | 15.5 | resolved |
| t15_syntax_max | syntax-error | easy | ✅ | ✅ | 1 | 6 | 1 | 2.4 | resolved |
| t16_rotate | modular-arithmetic | medium | ✅ | ✅ | 1 | 6 | 1 | 7.9 | resolved |
| t17_parse_config | parsing | medium | ✅ | ✅ | 1 | 6 | 1 | 63.3 | resolved |
| t18_stack | class-state | easy | ✅ | ✅ | 1 | 6 | 1 | 13.4 | resolved |
| t19_chunk | slicing | easy | ✅ | ✅ | 1 | 6 | 1 | 8.5 | resolved |
| t20_normalize | multi-bug | hard | ✅ | ✅ | 1 | 6 | 1 | 8.0 | resolved |
