# Benchmark results (2026-10-23 12:21:10)

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
| avg_context_tokens | 21471 | 27772 | 49242 | 50108 |
| latency_p50_ms | 338.9 | 98.2 | 735.2 | 1783.1 |
| latency_p95_ms | 513.4 | 128.5 | 1026.0 | 2027.5 |
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
| avg_context_tokens | 21281 | 27659 | 48938 | 52034 |
| latency_p50_ms | 808.2 | 186.9 | 1302.3 | 1880.7 |
| latency_p95_ms | 1106.8 | 395.9 | 1658.8 | 2196.3 |
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

## Source migration

Target: uploads (CSV/Parquet/Excel/JSON via DuckDB); code changes: 0; onboarding (discovery + profiling + ontology): 5.2 s; answer accuracy: 1.0 on 5 questions.
