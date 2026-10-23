# Implementation Plan: Universal Agentic Data Reasoning Engine (`udre`)

## 1. Repository Inspection Findings

- **Existing Workspace**: Contains `dba_ref.txt` (specification of 39 tables across 8 domains: `contracts`, `app`, `ref`, `erp`, `crm`, `plan`, `reporting`, `ops`). No existing Python packages or source code were present.
- **Environment**: Python 3.12.10 with `langgraph`, `langchain-core`, `langchain-openai`, `fastapi`, `pydantic`, `sqlalchemy`, `sqlglot`, `duckdb`, `pyarrow`, `pandas`, `openpyxl`, `rapidfuzz`, `jellyfish`, `scikit-learn`, `tiktoken`, `psycopg2-binary`, and `pytest`.
- **Database Runtime**: No PostgreSQL daemon on port 5432 and Docker is not running, so the project uses `pgserver` (an embedded, real PostgreSQL 16.2 with pgvector, data in `.pgdata/`). Set `UDRE_PG_URI` to use an external PostgreSQL instead.

## Progress Status (parked 23-Oct-2026 10:55)

Done and verified:
- `udre/contracts/models.py`: typed Pydantic contracts.
- `udre/synthetic/schema_spec.py`: all 39 DBA tables mapped to 8 fictional schemas, DDL with PKs/FKs/indexes/comments, mapping-doc generator.
- `udre/synthetic/generator.py`: seeded event-driven generator. A run produced 17,782 rows across all 39 tables in 1.2s.
- `udre/synthetic/loader.py`: loads into embedded PostgreSQL (1.3s) and exports CSV/Parquet/Excel/JSON uploads.

Written but not yet run:
- `udre/synthetic/ground_truth.py`: 9 SQL financial invariants and a 24-question benchmark (10 dev, 14 held-out).

Next steps, in order:
1. Run invariants and benchmark SQL against PostgreSQL, then fix any generator bugs.
2. Source gateway (Postgres, DuckDB files, Mongo/REST interfaces), then the 12 collection tools.
3. Profiler, semantic compiler, knowledge graph (pgvector), and ontology builder.
4. Explorer and Hercules agents, validators, adjudicator, and memory.
5. LangGraph workflow, exporters, FastAPI, demo script, benchmark runner, tests, and docs.

---

## 2. Concrete Module & File Architecture

### Package Layout (`udre/`)
1. **`udre/contracts/models.py`**
   - Typed Pydantic v2 reasoning contracts for Source Gateway, Ontology/Knowledge Graph, Dynamic Collection Skills, Explorer State, Hercules Context, Candidate Outputs, Verification Findings, Adjudication Decisions, and Persistent Learning Records.
2. **`udre/gateway/`**
   - `base.py`: Abstract `DataSourceAdapter` defining capabilities, permissions, collection discovery, schema inspection, column profiling, data sampling, value similarity, read-only query execution, and query-plan inspection.
   - `postgres_adapter.py`: PostgreSQL adapter (supports live `psycopg2`/`sqlalchemy` connections and embedded schema-qualified PostgreSQL compatibility engine).
   - `file_adapter.py`: DuckDB-backed analytical adapter for `CSV`, `Excel (.xlsx)`, `JSON`, and `Parquet` files.
   - `extensible_adapters.py`: Extensible interface implementations for `MongoDBSourceAdapter` (document pipeline query model) and `RestApiSourceAdapter` (scoped connector model).
   - `registry.py`: `SourceGateway` managing registered sources, access controls, read-only enforcement, and resource limits.
3. **`udre/skills/tools.py`**
   - Fixed 12-tool collection-independent API (`discover_sources`, `discover_collections`, `describe_collection`, `inspect_column`, `search_column_values`, `sample_rows`, `explore_relationships`, `lookup_knowledge`, `execute_query`, `explain_query`, `compare_results`, `export_results`) + dynamic lightweight collection skill descriptor retriever.
4. **`udre/ontology/graph.py`**
   - Relational + vector similarity Ontology & Knowledge Graph (`kg_nodes`, `kg_edges`, `kg_evidence`, `kg_embeddings`).
   - Supports 10 node types (`Source`, `Collection`, `Column`, `Concept`, `Metric`, `Dimension`, `ValueMapping`, `Relationship`, `QueryPattern`, `EvidenceRecord`) and 8 typed edges (`HAS_COLUMN`, `REPRESENTS_CONCEPT`, `REFERENCES`, `DERIVED_FROM`, `MAPS_TO`, `MEASURES`, `USES_DIMENSION`, `VERIFIED_BY`).
   - Tracks provenance, evidence IDs, `VerificationStatus` (`INFERRED_CANDIDATE` vs `VERIFIED_FACT`), schema version, confidence, and validation timestamps.
5. **`udre/compiler/semantic_compiler.py`**
   - Offline and incrementally refreshable `SemanticContextCompiler`.
   - Extracts schemas, constraints, candidate FK/join relationships, cardinality/distribution profiles, categorical value dictionaries, representative samples, and metric candidates.
   - Applies template deduplication and dictionary encoding, measuring raw vs compressed byte size and `tiktoken` token counts.
6. **`udre/agents/`**
   - `llm_engine.py`: Pluggable reasoning backend supporting OpenAI/Anthropic/Compatible LLMs when configured and a schema-agnostic, tool-driven Analytical Planner when running offline.
   - `explorer.py`: Bounded iterative investigation loop (`DISCOVER -> IDENTIFY KNOWLEDGE GAPS -> INSPECT -> QUERY -> OBSERVE -> VERIFY -> ANSWER`) with explicit structured `ExplorerInvestigationState`.
   - `hercules.py`: Independent reasoning path initialized with compiled compressed global semantic context, executing independent verification and queries without state leakage from Explorer.
7. **`udre/verification/`**
   - `validators.py`: Deterministic validators using `sqlglot` AST analysis and result inspection (syntax/column existence, source bindings, execution errors, aggregation grain, join fanout/multiplicity, null handling, currency/unit consistency, date/period interpretation, result reconciliation, financial invariants).
   - `adjudicator.py`: Evidence-based `AdjudicationEngine` that compares Explorer and Hercules candidates, runs discriminating tie-breaker queries when candidates diverge, and produces a grounded answer with full lineage.
8. **`udre/memory/learning_store.py`**
   - `PersistentLearningStore` with tenant/user isolation and schema-version invalidation for verified relationships, value mappings, query templates, failed query patterns, and metric definitions.
9. **`udre/orchestration/workflow.py`**
   - Parallel `LangGraph` state graph executing: `parse_request -> resolve_sources -> retrieve_knowledge -> [run_explorer || run_hercules] -> verify_candidates -> adjudicate -> targeted_followup (conditional) -> finalize_and_persist`.
10. **`udre/export/exporter.py`**
    - Result export engine supporting `CSV`, `Excel (.xlsx)`, `JSON`, and an extensible `ReportExporter` interface (including Markdown Audit Report).
11. **`udre/synthetic/`**
    - `schema_spec.py`: Complete definition and mapping of all 39 DBA-referenced tables across 8 domains using fictional `Aethelgard Enterprise` naming.
    - `generator.py`: Deterministic (seed-controlled) event-driven financial generator producing internally consistent records across all 39 tables plus deliberate edge cases and multi-format exported files (`CSV`, `Parquet`, `Excel`, `JSON`).
    - `ground_truth.py`: Independent ground-truth benchmark with 12 financial & cross-domain questions, deterministic verification queries, and evaluation runner comparing `Explorer`, `Hercules`, `Dual-Path`, and `Dual-Path + Memory`.
12. **`udre/api/app.py` & `run_demo.py`**
    - FastAPI REST service and runnable CLI demonstration script.
