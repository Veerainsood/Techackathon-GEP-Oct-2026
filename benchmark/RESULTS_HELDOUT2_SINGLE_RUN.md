# Benchmark results (2026-10-23 12:24:04)

Expected answers are computed independently from the generator's event model. No LLM was configured for this run: both agents used the deterministic planner, so 'context tokens' counts tokens of context and tool observations processed, not model tokens.

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
| avg_context_tokens | 22048 | 27783 | 49829 | 52767 |
| latency_p50_ms | 868.0 | 218.8 | 1367.9 | 1926.9 |
| latency_p95_ms | 1169.9 | 439.0 | 1741.8 | 2424.6 |
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

Target: uploads (CSV/Parquet/Excel/JSON via DuckDB); code changes: 0; onboarding (discovery + profiling + ontology): 5.1 s; answer accuracy: 1.0 on 5 questions.
