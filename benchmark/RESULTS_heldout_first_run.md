# Benchmark results (2026-10-23 12:19:13)

Expected answers are computed independently from the generator's event model. No LLM was configured for this run: both agents used the deterministic planner, so 'context tokens' counts tokens of context and tool observations processed, not model tokens.

## heldout split

| metric | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| answer_accuracy | 0.846 | 0.846 | 0.846 | 0.0 |
| schema_linking_accuracy | 0.923 | 0.923 | 0.923 | 0.0 |
| value_linking_accuracy | 1.0 | 1.0 | 1.0 | 0.0 |
| reconciliation_accuracy | 0.667 | 0.667 | 0.667 | 0.0 |
| wrong_but_confident_rate | 0.154 | 0.154 | 0.154 | 0.0 |
| abstention_on_ambiguous | 1.0 | 1.0 | 1.0 | 0.0 |
| unnecessary_abstention_rate | 0.0 | 0.0 | 0.0 | 0.0 |
| avg_tool_calls | 28.6 | 7.1 | 35.7 | 0 |
| avg_context_tokens | 21285 | 27662 | 48946 | 0 |
| latency_p50_ms | 874.0 | 195.6 | 1268.2 | 22.6 |
| latency_p95_ms | 1205.0 | 370.8 | 1622.8 | 25.3 |
| followups | 0 | 0 | 0 | 0 |

| id | explorer | hercules | dual | dual_memory |
|---|---|---|---|---|
| H01 | PASS | PASS | PASS | FAIL |
| H02 | PASS | PASS | PASS | FAIL |
| H03 | PASS | PASS | PASS | FAIL |
| H04 | PASS | PASS | PASS | FAIL |
| H05 | PASS | PASS | PASS | FAIL |
| H06 | PASS | PASS | PASS | FAIL |
| H07 | PASS | PASS | PASS | FAIL |
| H08 | FAIL | FAIL | FAIL | FAIL |
| H09 | PASS | PASS | PASS | FAIL |
| H10 | PASS | PASS | PASS | FAIL |
| H11 | PASS | PASS | PASS | FAIL |
| H12 | FAIL | FAIL | FAIL | FAIL |
| H13 | PASS (abstain) | PASS (abstain) | PASS (abstain) | FAIL |
| H14 | PASS | PASS | PASS | FAIL |

## Source migration

Target: uploads (CSV/Parquet/Excel/JSON via DuckDB); code changes: 0; onboarding (discovery + profiling + ontology): 5.0 s; answer accuracy: 1.0 on 5 questions.
