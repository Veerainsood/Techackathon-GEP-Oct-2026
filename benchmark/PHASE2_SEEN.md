# Benchmark results (2026-10-23 16:07:01)

Expected answers are computed independently from the generator's event model. Reasoning mode: {'explorer': 'deterministic_offline', 'hercules': 'deterministic_offline'}. No LLM was configured: both agents used the deterministic planner, so 'context tokens' counts tokens of context and tool observations processed, not model tokens.

## dev split

| metric | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| answer_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| schema_linking_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| value_linking_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| reconciliation_accuracy | None | None | None | None |
| wrong_but_confident_rate | 0.0 | 0.0 | 0.0 | 0.0 |
| abstention_on_ambiguous | None | None | None | None |
| unnecessary_abstention_rate | 0.0 | 0.0 | 0.0 | 0.0 |
| avg_tool_calls | 29.3 | 7.3 | 36.6 | 36.6 |
| avg_context_tokens | 21528 | 27868 | 49395 | 50261 |
| latency_p50_ms | 706.1 | 262.9 | 904.9 | 1339.8 |
| latency_p95_ms | 815.7 | 385.8 | 1127.1 | 1575.7 |
| followups | 0 | 0 | 0 | 0 |

| id | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| D01 | PASS | PASS | PASS | PASS |
| D02 | PASS | PASS | PASS | PASS |
| D03 | PASS | PASS | PASS | PASS |
| D04 | PASS | PASS | PASS | PASS |
| D05 | PASS | PASS | PASS | PASS |
| D06 | PASS | PASS | PASS | PASS |
| D07 | PASS | PASS | PASS | PASS |
| D08 | PASS | PASS | PASS | PASS |
| D09 | PASS | PASS | PASS | PASS |
| D10 | PASS | PASS | PASS | PASS |

## heldout split

| metric | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| answer_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| schema_linking_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| value_linking_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| reconciliation_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| wrong_but_confident_rate | 0.0 | 0.0 | 0.0 | 0.0 |
| abstention_on_ambiguous | 1.0 | 1.0 | 1.0 | 1.0 |
| unnecessary_abstention_rate | 0.0 | 0.0 | 0.0 | 0.0 |
| avg_tool_calls | 28.6 | 7.1 | 35.7 | 35.7 |
| avg_context_tokens | 21339 | 27734 | 49071 | 52167 |
| latency_p50_ms | 691.8 | 315.1 | 936.0 | 1276.4 |
| latency_p95_ms | 863.6 | 427.9 | 1060.8 | 1571.1 |
| followups | 0 | 0 | 0 | 0 |

| id | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| H01 | PASS | PASS | PASS | PASS |
| H02 | PASS | PASS | PASS | PASS |
| H03 | PASS | PASS | PASS | PASS |
| H04 | PASS | PASS | PASS | PASS |
| H05 | PASS | PASS | PASS | PASS |
| H06 | PASS | PASS | PASS | PASS |
| H07 | PASS | PASS | PASS | PASS |
| H08 | PASS | PASS | PASS | PASS |
| H09 | PASS | PASS | PASS | PASS |
| H10 | PASS | PASS | PASS | PASS |
| H11 | PASS | PASS | PASS | PASS |
| H12 | PASS | PASS | PASS | PASS |
| H13 | PASS (abstain) | PASS (abstain) | PASS (abstain) | PASS (abstain) |
| H14 | PASS | PASS | PASS | PASS |

## heldout2 split

| metric | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| answer_accuracy | 0.889 | 1.0 | 0.889 | 0.889 |
| schema_linking_accuracy | 0.889 | 1.0 | 0.889 | 0.889 |
| value_linking_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| reconciliation_accuracy | 0.667 | 1.0 | 0.667 | 0.667 |
| wrong_but_confident_rate | 0.111 | 0.0 | 0.0 | 0.0 |
| abstention_on_ambiguous | 1.0 | 1.0 | 1.0 | 1.0 |
| unnecessary_abstention_rate | 0.0 | 0.0 | 0.111 | 0.111 |
| avg_tool_calls | 29.9 | 8 | 37.9 | 37.9 |
| avg_context_tokens | 22125 | 27872 | 49996 | 52851 |
| latency_p50_ms | 703.5 | 315.0 | 944.4 | 1005.6 |
| latency_p95_ms | 829.5 | 434.9 | 1133.3 | 1192.7 |
| followups | 0 | 0 | 0 | 0 |

| id | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| N01 | PASS | PASS | PASS | PASS |
| N02 | PASS | PASS | PASS | PASS |
| N03 | PASS | PASS | PASS | PASS |
| N04 | PASS | PASS | PASS | PASS |
| N05 | PASS | PASS | PASS | PASS |
| N06 | PASS | PASS | PASS | PASS |
| N07 | PASS | PASS | PASS | PASS |
| N08 | FAIL | PASS | FAIL (abstain) | FAIL (abstain) |
| N09 | PASS (abstain) | PASS (abstain) | PASS (abstain) | PASS (abstain) |
| N10 | PASS | PASS | PASS | PASS |

## Source migration

Target: uploads (CSV/Parquet/Excel/JSON via DuckDB); code changes: 0; onboarding (discovery + profiling + ontology): 2.9 s; answer accuracy: 1.0 on 5 questions.
