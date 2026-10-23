# Phase 2: skills, LLM agents, external ingestion, reliability

Everything below was run in this repository on 23 Oct 2026. Test and benchmark numbers come from those runs (`benchmark/baseline_phase2.*`, `benchmark/phase2_final.*`, `benchmark/phase2_post_demo.*`).

## What is verified, and how

| Capability | Status | Evidence |
|---|---|---|
| Synthetic 39-table finance database | unchanged, working | 9 invariants pass, dataset tests pass |
| Skill + versioned collection reference files | implemented, tested | `tests/test_skills_llm.py` |
| Progressive, permission-checked context loading | implemented, tested | same |
| LLM tool-calling loop (both agents) | implemented, tested **with a scripted test double only** | `tests/test_skills_llm.py` |
| Live LLM reasoning | **not verified**: `.env` holds an Azure AI Foundry Anthropic endpoint but no key, and the endpoint returns HTTP 401 | n/a |
| Microsoft Intune connector | implemented, tested against a **mock** Graph API | `tests/test_connectors.py` |
| HubSpot connector | implemented, tested against a **mock** HubSpot API | same |
| Live Intune / HubSpot integration | **not verified** (no credentials) | n/a |
| Cross-source candidates | implemented, tested (never auto-asserted) | same |
| Adjudication fixes | implemented, tested, benchmarked | `tests/test_reliability.py`, benchmarks |

Test suite: 42 tests before Phase 2, **74 now, all passing**.

## Phase A: baseline (before any change)

* 42/42 tests passing.
* `benchmark/baseline_phase2.*` reproduced the earlier numbers exactly.
  * `heldout2`: Explorer 0.667, Hercules 0.778, dual 0.667, dual + memory 0.667; wrong-but-confident rate 0.333 / 0.222 / 0.222 / 0.222.
  * `dev` and `heldout`: 1.0 everywhere. Both splits informed earlier fixes, so they are not generalization estimates.
* Before making any change, a new split `heldout3` (12 questions) was written, checked against its verification SQL, and set aside to be scored once at the end.

## Phase B: skill-driven knowledge

Files: `udre/skills/database-investigation/SKILL.md`, `udre/skills/context_generator.py`, `udre/skills/registry.py`, `udre/skills/tools.py`, `udre/gateway/postgres_adapter.py` (indexes), `udre/contracts/models.py`, `udre/engine.py`, `udre/orchestration/workflow.py`.

* **Root skill:** `SKILL.md` covers the trust model, the investigation state to keep, the 11-step procedure and when to abstain.
* **Generated references:** one Markdown file per collection, in three clearly separated sections: authoritative catalog metadata (types, nullability, keys, FK targets, indexes), observed profiles/values/samples/relationships/reconciliation rules, and inferred hypotheses. Plus value-profile, relationship, metric and verified-query files, and per-tenant `catalog.json` and `global-context.json`.
* **Versioning and atomicity:** each version is `<schema fingerprint>-<inputs hash>`, and the inputs hash includes a generator version. All files are written first, then a pointer file is swapped atomically with `os.replace`; readers only resolve files through the pointer. Previous versions are pruned, and schema changes trigger regeneration (tested).
* **Tenant scoping:** files only contain collections the tenant may access.
* **Masking:** sensitive-looking columns and any email-like value are masked in samples, profiles, value lists and the compressed context. Tests found two real leaks (profile min/max, and `user_principal_name`); both are fixed.
* **New tools:** `list_skills`, `load_skill`, `list_collection_contexts`, `load_collection_context` and `execute_federated_query`, all permission-checked. The existing 12 tools are unchanged.
* **Trust handling:** retrieved content is marked `trust: untrusted_data` and wrapped in `<untrusted_data>` tags, and closing tags inside data are escaped (tested with an injection payload stored in a CSV).
* **Honest limitation:** "verified query examples" are queries that passed deterministic verification, not queries checked against ground truth. One shape-wrong query (the N02 grouped answer) was stored as verified before the Phase E fix.

## Phase C: LLM agents

Files: `udre/llm/{base,providers,config,scripted}.py`, `udre/agents/llm_agent.py`, `udre/agents/prompts.py`, `udre/agents/explorer.py`, `udre/agents/hercules.py`, `udre/agents/planner.py` (`plan_from_sql`, `structured_from_rows`), `udre/orchestration/workflow.py` (events), `udre/api/app.py` (`/ask/stream`).

* **Providers:** async httpx clients for the Anthropic Messages API (works with Azure AI Foundry `.../anthropic/v1/messages`) and OpenAI-compatible Chat Completions. Retries on 408/409/425/429/5xx/529 and timeouts, honouring `Retry-After`. Tracks token usage and cost (prices configurable). Keys are redacted from errors.
* **Configuration:** all from the environment, per agent (see below). No model names or keys are hardcoded. `UDRE_REASONING_MODE=auto|offline|llm`.
* **Agent loop:** the model chooses every tool call, and each call must carry a `gap` argument. Tool results carry bounded real rows (20), column names and observed types, row counts, verbatim errors and provenance. The final answer is a Pydantic-validated `CandidateSubmission`; invalid submissions, and SQL that fails when the system re-executes it, go back to the model for revision. The loop enforces turn and tool-call budgets (when exhausted, the model is forced to submit), emits events, and never trusts results the model claims: the system re-runs the SQL.
* **Explorer:** gets the question, the full `SKILL.md`, a lightweight catalog and verified knowledge, with no preloaded global context (tested).
* **Hercules:** gets a different prompt and strategy (plan from the global overview first, then verify) plus the compressed overview of all authorized collections, with an inclusion report covering included versus omitted collections and full versus rendered tokens. Hercules never receives Explorer's state; a test checks that Explorer's failed probe never appears in Hercules' requests.
* **Offline fallback:** with no provider configured, the deterministic planner runs, labelled `deterministic_offline`. If a configured provider fails, the agent falls back on a fresh tool session, labelled `deterministic_fallback` with the error recorded.
* **Validators on LLM SQL:** `plan_from_sql` turns arbitrary SQL into a plan, so the deterministic validators run on model-written SQL too.
* **Not done:** token-level streaming (agent/tool events stream instead). Live provider runs: no key is available.

## Phase D: connectors and ingestion

Files: `udre/ingestion/{base,snapshot,pipeline,intune,hubspot,mocks}.py`, `udre/ontology/cross_source.py`, `udre/ontology/builder.py` (relationship properties), `udre/domain_packs/{crm,device_management}.yaml`, `udre/gateway/registry.py` (`grant`/`revoke`), `udre/api/app.py` (`/connectors/*`, `/sources/{id}/sync`, `/webhooks/hubspot/{id}`).

* **Lifecycle:** register → authenticate → discover → fetch/normalize (per object, checkpointed) → derived tables → profile + ontology → collection files + global context → cross-source candidates → queryable.
  * A failing object is recorded and skipped without advancing its checkpoint, and the next sync recovers it (tested).
  * Authentication failures stop the run before any data is touched (tested).
* **Snapshots:** DuckDB snapshot per tenant and source.
  * Nested JSON is flattened (`a__b`), arrays are kept as JSON, and column names are snake_case.
  * Records are upserted by stable id, and new properties add columns.
  * Tombstones: `_udre_archived` mirrors the source flag; `_udre_present = false` when a full refresh stops returning a record.
  * Every query result reports `provenance.mode = snapshot` and `synced_at`; Postgres and file sources report `live`.
* **Intune (Microsoft Graph v1.0 only):**
  * Client-credentials token and `@odata.nextLink` paging; 429/503 handled with `Retry-After`.
  * Resources: managed devices, compliance policies, per-policy device statuses (child resource, FK declared), detected apps, and an optional report export job.
  * No delta queries are available on these resources, so every resource is a full refresh with reconciliation (documented in the connector and its capabilities).
  * Permissions: `DeviceManagementManagedDevices.Read.All`, `DeviceManagementConfiguration.Read.All`.
  * Secrets, token and tenant ID never appear in generated files (tested).
* **HubSpot:**
  * Auth: private-app token, or OAuth refresh (tested, including a revoked token).
  * Properties are discovered dynamically: labels, types, descriptions and enumeration options. String values are typed from that metadata, and custom properties and custom objects are discovered automatically (tested).
  * Incremental sync uses the search API on the detected last-modified field and slides the window past the 10k cap; the inclusive boundary gives at-least-once delivery, made idempotent by upserts.
  * Archived records are kept as tombstones.
  * v4 associations are stored per direction with type id, category and label; ontology edges keep `direction` and `role` (tested).
  * Pipelines and stages; `deals.dealstage` and `deals.pipeline` are declared and verified as FKs.
  * Webhook v3 signature verification and a persisted change queue that survives restarts (tested).
* **Cross-source candidates:** built from normalized value overlap in identifier-like columns.
  * Always stored as `inferred_candidate`, with the basis labelled "normalized name overlap only" when names are all there is.
  * Shown in both sides' reference files as "Not an identity assertion".
  * The demo finds `client_conglomerates.conglomerate_name ~ hubspot companies.name` with 25 matches.
* **Federated query tool:** runs bounded sub-queries per source, then joins them locally in DuckDB. It reports per-part provenance and truncation.

## Phase E: reliability

Files: `udre/verification/validators.py`, `udre/verification/adjudicator.py`, `udre/agents/planner.py`, `udre/orchestration/workflow.py`, `tests/test_reliability.py`.

**Why the dual path lost to Hercules on `heldout2` (N08).** Explorer summed rep quota; Hercules summed deal weighted ARR, which was correct. Both passed every structural validator, so their evidence scores tied and the adjudicator abstained. No check compared each plan's physical bindings with the question.

**Fixes (not benchmark-specific; no agent is preferred unconditionally):**

* **`question_coverage` validator:** splits informative question terms into those bound by executed collections/columns/values (full credit) and those only explained by descriptions or concept synonyms (half credit).
* **Adjudication rule on disagreement:** if one plan leaves at least two question terms physically unbound that the other binds, and the other has no blocking errors, the other wins. The analysis names the terms. When both plans leave different terms unbound, the system still abstains (tested).
* **`candidate_completeness` check:** every candidate must carry its executed query, source lineage, result, plan, interpretation and evidence.
* **Ambiguity:** now detected from structured plan flags (`metric_choice`, `relative_time`, `agent_abstained`) instead of string matching.
* **Planner (generic) fixes:**
  * "issued/owned/billed… *by* X" is not a grouping.
  * Time-column phrases ("snapshot period 2025-12") no longer steer measure choice.
  * A phrase used for grouping is not also a filter.
  * Entity choice prefers names mostly explained by the phrase (found by the Phase F demo: `contacts` vs `assoc__companies__contacts`).
* **Explorer budget:** ranks sources by relevance and keeps budget in reserve for the query (found by the demo, where four sources exhausted the budget).

**Numbers are never averaged.** Abstention remains available.

## Benchmarks (deterministic offline mode; no LLM)

| split | config | baseline accuracy | Phase 2 accuracy | baseline wrong-but-confident | Phase 2 wrong-but-confident |
|---|---|---|---|---|---|
| heldout2 (seen) | Explorer | 0.667 | 0.889 | 0.333 | 0.111 |
| heldout2 (seen) | Hercules | 0.778 | 1.0 | 0.222 | 0.0 |
| heldout2 (seen) | dual | 0.667 | **1.0** | 0.222 | **0.0** |
| heldout2 (seen) | dual + memory | 0.667 | 1.0 | 0.222 | 0.0 |
| **heldout3 (scored once)** | Explorer | n/a | 0.909 | n/a | 0.0 |
| **heldout3 (scored once)** | Hercules | n/a | 0.909 | n/a | 0.0 |
| **heldout3 (scored once)** | dual | n/a | 0.909 (1 unnecessary abstention) | n/a | 0.0 |
| **heldout3 (scored once)** | dual + memory | n/a | 0.909 | n/a | 0.0 |

* `heldout2` became a development split once its failures were analysed, so its Phase 2 numbers are not a generalization estimate.
* `heldout3` was written before Phase E and scored once (`phase2_final`); the post-demo rerun (`phase2_post_demo`) gave identical accuracy.
* `heldout3` misses:
  * **T02:** "doc currency" pulled the measure to `subtotal_amount_doc`, which turned "in USD" into a `USD` filter. The query returned NULL, validators caught it and the dual path abstained, so it failed safely.
  * **T11:** "Which customers are the biggest?" was answered confidently (sum of credit limits from the uploaded file) instead of abstaining. This is a real abstention gap for vague questions.
* `dev` and `heldout`: 1.0 everywhere before and after.
* Source migration (file copy): 5/5, 0 code changes.
* Latency: dual p50 is about 0.8–0.9 s.
* Memory gave no accuracy gain on these splits and adds roughly 0.3–0.5 s.

## Phase F: end-to-end demonstration

`python scripts/run_phase2_demo.py`, output captured on 23 Oct:

* **Mock HubSpot and Intune:** registered and ingested through the common pipeline.
  * HubSpot: 6 objects plus a custom object, 4 association tables and pipelines; 12 collections, 12 verified relationships.
  * Intune: 4 collections; 230 devices across 3 pages, including one 429 retry.
* **Skill files:** generated for every source (39 + 6 + 12 + 4 collection files).
* **Global context:** 142,703 → 33,927 tokens, with 61 of 61 collections included.
* **Cross-source:** candidates found and labelled unverified.
* **Both agents:** answered finance, HubSpot (`contacts` with lifecycle stage customer: 23) and Intune (noncompliant devices: 40) questions with consensus. Snapshot provenance appears on CRM and device answers.
* **Ambiguity:** "revenue last year" abstained with the reading and assumptions.
* **Exports:** Excel and JSON exports were written, and a federated finance × CRM query ran with mixed live/snapshot provenance.

## Credentials and permissions needed for live verification

| Integration | Variables | Permissions / scopes |
|---|---|---|
| LLM (Azure AI Foundry Anthropic) | `UDRE_LLM_BASE_URL=https://<resource>.services.ai.azure.com/anthropic/v1/messages`, `UDRE_LLM_API_KEY=<key>`, `UDRE_LLM_MODEL=<deployment name>`; optional `UDRE_EXPLORER_LLM_*` / `UDRE_HERCULES_LLM_*`, `UDRE_LLM_INPUT_COST_PER_MTOK`, `UDRE_LLM_OUTPUT_COST_PER_MTOK` | key for the Foundry resource |
| Microsoft Intune | `UDRE_INTUNE_TENANT_ID`, `UDRE_INTUNE_CLIENT_ID`, `UDRE_INTUNE_CLIENT_SECRET` | Entra app (application permissions, admin consent): `DeviceManagementManagedDevices.Read.All`, `DeviceManagementConfiguration.Read.All`; Intune licence |
| HubSpot | `UDRE_HUBSPOT_ACCESS_TOKEN` (private app) or `UDRE_HUBSPOT_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` | `crm.objects.companies.read`, `crm.objects.contacts.read`, `crm.objects.deals.read`, `crm.objects.line_items.read`, `e-commerce`, `crm.schemas.custom.read`, `crm.objects.custom.read` |

The current `.env` line (`Claude opus 4.6 urls = https://…`) is not in `KEY=value` form with a `UDRE_` prefix, so the engine ignores it. Put the variables above in `.env` and run `python scripts/run_benchmark.py --split heldout3 --out live_llm` to measure the LLM path. `scripts/run_phase2_demo.py --live` uses real HubSpot/Intune credentials.

## Remaining limitations

* LLM reasoning and both connectors are verified only against test doubles and mocks.
* Explorer and Hercules share one planner in offline mode, so their errors are correlated. Real independence of reasoning needs the LLM path.
* Question coverage is a lexical heuristic. Abstention on vague questions (T11) is weak.
* Intune report export uses a POST job, and the export-job schema varies by report. HubSpot search-based incremental sync can miss records changed mid-sync until the next full refresh.
* Each verified answer still invalidates the cached compiled context, which costs latency in memory mode.
* Token-level streaming is not implemented (agent/tool events stream over `/ask/stream`).
