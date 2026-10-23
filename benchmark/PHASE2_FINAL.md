# Benchmark results (2026-10-23 16:11:58)

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
| avg_context_tokens | 21528 | 27880 | 49408 | 50273 |
| latency_p50_ms | 664.6 | 269.9 | 926.5 | 1356.2 |
| latency_p95_ms | 815.2 | 399.8 | 1084.3 | 1628.6 |
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
| avg_context_tokens | 21335 | 27738 | 49071 | 52178 |
| latency_p50_ms | 714.2 | 310.5 | 907.6 | 1280.8 |
| latency_p95_ms | 862.5 | 422.1 | 1083.9 | 1471.8 |
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
| avg_tool_calls | 29.9 | 8 | 37.9 | 37.9 |
| avg_context_tokens | 22122 | 27879 | 50000 | 52899 |
| latency_p50_ms | 630.8 | 294.5 | 869.6 | 972.1 |
| latency_p95_ms | 745.6 | 393.4 | 1080.9 | 1203.8 |
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
| avg_tool_calls | 29.2 | 7.4 | 36.7 | 36.7 |
| avg_context_tokens | 21488 | 27706 | 49193 | 52163 |
| latency_p50_ms | 599.8 | 301.6 | 876.1 | 962.1 |
| latency_p95_ms | 792.4 | 382.3 | 957.7 | 1377.2 |
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

Target: uploads (CSV/Parquet/Excel/JSON via DuckDB); code changes: 0; onboarding (discovery + profiling + ontology): 3.3 s; answer accuracy: 1.0 on 5 questions.
