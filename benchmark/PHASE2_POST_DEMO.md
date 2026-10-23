# Benchmark results (2026-10-23 16:19:40)

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
| avg_tool_calls | 30.3 | 7.3 | 37.6 | 37.6 |
| avg_context_tokens | 22599 | 27881 | 50478 | 51344 |
| latency_p50_ms | 378.1 | 176.9 | 799.2 | 1303.2 |
| latency_p95_ms | 481.9 | 226.4 | 1083.9 | 1497.7 |
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
| avg_tool_calls | 29.6 | 7.1 | 36.7 | 36.7 |
| avg_context_tokens | 22406 | 27737 | 50142 | 53248 |
| latency_p50_ms | 750.0 | 309.1 | 899.6 | 1272.2 |
| latency_p95_ms | 853.4 | 425.9 | 1227.6 | 1468.8 |
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
| answer_accuracy | 0.889 | 1.0 | 1.0 | 1.0 |
| schema_linking_accuracy | 0.889 | 1.0 | 1.0 | 1.0 |
| value_linking_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| reconciliation_accuracy | 0.667 | 1.0 | 1.0 | 1.0 |
| wrong_but_confident_rate | 0.111 | 0.0 | 0.0 | 0.0 |
| abstention_on_ambiguous | 1.0 | 1.0 | 1.0 | 1.0 |
| unnecessary_abstention_rate | 0.0 | 0.0 | 0.0 | 0.0 |
| avg_tool_calls | 30.9 | 8 | 38.9 | 38.9 |
| avg_context_tokens | 23190 | 27877 | 51066 | 53969 |
| latency_p50_ms | 659.2 | 309.3 | 912.2 | 990.8 |
| latency_p95_ms | 838.6 | 459.1 | 1082.6 | 1356.8 |
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
| N08 | FAIL | PASS | PASS | PASS |
| N09 | PASS (abstain) | PASS (abstain) | PASS (abstain) | PASS (abstain) |
| N10 | PASS | PASS | PASS | PASS |

## heldout3 split

| metric | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| answer_accuracy | 0.909 | 0.909 | 0.909 | 0.909 |
| schema_linking_accuracy | 1.0 | 1.0 | 0.909 | 0.909 |
| value_linking_accuracy | 1.0 | 1.0 | 0.889 | 0.889 |
| reconciliation_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| wrong_but_confident_rate | 0.0 | 0.0 | 0.0 | 0.0 |
| abstention_on_ambiguous | 0.0 | 0.0 | 0.0 | 0.0 |
| unnecessary_abstention_rate | 0.0 | 0.0 | 0.091 | 0.091 |
| avg_tool_calls | 30.2 | 7.4 | 37.7 | 37.7 |
| avg_context_tokens | 22556 | 27706 | 50261 | 53231 |
| latency_p50_ms | 605.1 | 273.8 | 808.5 | 1024.7 |
| latency_p95_ms | 804.4 | 376.9 | 1110.6 | 1208.4 |
| followups | 0 | 0 | 0 | 0 |

| id | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| T01 | PASS | PASS | PASS | PASS |
| T02 | FAIL | FAIL | FAIL (abstain) | FAIL (abstain) |
| T03 | PASS | PASS | PASS | PASS |
| T04 | PASS | PASS | PASS | PASS |
| T05 | PASS | PASS | PASS | PASS |
| T06 | PASS | PASS | PASS | PASS |
| T07 | PASS | PASS | PASS | PASS |
| T08 | PASS | PASS | PASS | PASS |
| T09 | PASS | PASS | PASS | PASS |
| T10 | PASS | PASS | PASS | PASS |
| T11 | FAIL | FAIL | FAIL | FAIL |
| T12 | PASS | PASS | PASS | PASS |

## Source migration

Target: uploads (CSV/Parquet/Excel/JSON via DuckDB); code changes: 0; onboarding (discovery + profiling + ontology): 2.8 s; answer accuracy: 1.0 on 5 questions.
