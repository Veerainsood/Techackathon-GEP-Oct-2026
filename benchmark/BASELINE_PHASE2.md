# Benchmark results (2026-10-23 15:39:35)

Expected answers are computed independently from the generator's event model. No LLM was configured for this run: both agents used the deterministic planner, so 'context tokens' counts tokens of context and tool observations processed, not model tokens.

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
| avg_context_tokens | 21472 | 27772 | 49242 | 50108 |
| latency_p50_ms | 476.4 | 109.9 | 701.9 | 1092.8 |
| latency_p95_ms | 691.0 | 121.5 | 931.7 | 1269.6 |
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
| avg_context_tokens | 21282 | 27659 | 48940 | 52035 |
| latency_p50_ms | 435.2 | 135.0 | 681.4 | 1042.2 |
| latency_p95_ms | 671.9 | 252.3 | 859.7 | 1420.7 |
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
| answer_accuracy | 0.667 | 0.778 | 0.667 | 0.667 |
| schema_linking_accuracy | 0.778 | 0.889 | 0.778 | 0.778 |
| value_linking_accuracy | 1.0 | 1.0 | 1.0 | 1.0 |
| reconciliation_accuracy | 0.667 | 1.0 | 0.667 | 0.667 |
| wrong_but_confident_rate | 0.333 | 0.222 | 0.222 | 0.222 |
| abstention_on_ambiguous | 1.0 | 1.0 | 1.0 | 1.0 |
| unnecessary_abstention_rate | 0.0 | 0.0 | 0.111 | 0.111 |
| avg_tool_calls | 29.8 | 8 | 37.8 | 37.8 |
| avg_context_tokens | 22046 | 27781 | 49827 | 52679 |
| latency_p50_ms | 467.1 | 142.7 | 735.3 | 845.2 |
| latency_p95_ms | 582.9 | 338.5 | 930.1 | 1133.3 |
| followups | 0 | 0 | 0 | 0 |

| id | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| N01 | PASS | PASS | PASS | PASS |
| N02 | FAIL | FAIL | FAIL | FAIL |
| N03 | PASS | PASS | PASS | PASS |
| N04 | FAIL | FAIL | FAIL | FAIL |
| N05 | PASS | PASS | PASS | PASS |
| N06 | PASS | PASS | PASS | PASS |
| N07 | PASS | PASS | PASS | PASS |
| N08 | FAIL | PASS | FAIL (abstain) | FAIL (abstain) |
| N09 | PASS (abstain) | PASS (abstain) | PASS (abstain) | PASS (abstain) |
| N10 | PASS | PASS | PASS | PASS |

## Source migration

Target: uploads (CSV/Parquet/Excel/JSON via DuckDB); code changes: 0; onboarding (discovery + profiling + ontology): 3.0 s; answer accuracy: 1.0 on 5 questions.
