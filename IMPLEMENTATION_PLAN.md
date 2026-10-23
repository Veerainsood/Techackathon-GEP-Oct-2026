# Implementation Plan: Universal Agentic Data Reasoning Engine (`backend`)

## 1. Repository Inspection Findings

- **Existing Workspace**: Contains `dba_ref.txt` (specification of 39 tables across 8 domains: `contracts`, `app`, `ref`, `erp`, `crm`, `plan`, `reporting`, `ops`). No existing Python packages or source code were present.
- **Environment**: Python 3.12.10 with `langgraph`, `langchain-core`, `langchain-openai`, `fastapi`, `pydantic`, `sqlalchemy`, `sqlglot`, `duckdb`, `pyarrow`, `pandas`, `openpyxl`, `rapidfuzz`, `jellyfish`, `scikit-learn`, `tiktoken`, `psycopg2-binary`, and `pytest`.
- **Database Runtime**: No PostgreSQL daemon on port 5432 and Docker is not running, so the project uses `pgserver` (an embedded, real PostgreSQL 16.2 with pgvector, data in `.pgdata/`). Set `BACKEND_PG_URI` to use an external PostgreSQL instead.

## Progress Status (23-Oct-2026)

All five planned phases are built and tested. See `README.md` for the current split between implemented, partially implemented and planned features, and for benchmark results. The section below is the original plan; module names changed slightly during implementation (for example, the embedded database is managed by `backend/pg_embedded.py` rather than through `pgserver.get_server`, which hung during crash recovery on Windows).

---

## 2. Concrete Module & File Architecture

### Package Layout (`backend/`)
1. **`backend/contracts/models.py`**
   - Typed Pydantic v2 reasoning contracts for Source Gateway, Ontology/Knowledge Graph, Dynamic Collection Skills, Explorer State, Hercules Context, Candidate Outputs, Verification Findings, Adjudication Decisions, and Persistent Learning Records.
2. **`backend/gateway/`**
   - `base.py`: Abstract `DataSourceAdapter` defining capabilities, permissions, collection discovery, schema inspection, column profiling, data sampling, value similarity, read-only query execution, and query-plan inspection.
   - `postgres_adapter.py`: PostgreSQL adapter (supports live `psycopg2`/`sqlalchemy` connections and embedded schema-qualified PostgreSQL compatibility engine).
   - `file_adapter.py`: DuckDB-backed analytical adapter for `CSV`, `Excel (.xlsx)`, `JSON`, and `Parquet` files.
   - `extensible_adapters.py`: Extensible interface implementations for `MongoDBSourceAdapter` (document pipeline query model) and `RestApiSourceAdapter` (scoped connector model).
   - `registry.py`: `SourceGateway` managing registered sources, access controls, read-only enforcement, and resource limits.
3. **`backend/skills/tools.py`**
   - Fixed 12-tool collection-independent API (`discover_sources`, `discover_collections`, `describe_collection`, `inspect_column`, `search_column_values`, `sample_rows`, `explore_relationships`, `lookup_knowledge`, `execute_query`, `explain_query`, `compare_results`, `export_results`) + dynamic lightweight collection skill descriptor retriever.
4. **`backend/ontology/graph.py`**
   - Relational + vector similarity Ontology & Knowledge Graph (`kg_nodes`, `kg_edges`, `kg_evidence`, `kg_embeddings`).
   - Supports 10 node types (`Source`, `Collection`, `Column`, `Concept`, `Metric`, `Dimension`, `ValueMapping`, `Relationship`, `QueryPattern`, `EvidenceRecord`) and 8 typed edges (`HAS_COLUMN`, `REPRESENTS_CONCEPT`, `REFERENCES`, `DERIVED_FROM`, `MAPS_TO`, `MEASURES`, `USES_DIMENSION`, `VERIFIED_BY`).
   - Tracks provenance, evidence IDs, `VerificationStatus` (`INFERRED_CANDIDATE` vs `VERIFIED_FACT`), schema version, confidence, and validation timestamps.
5. **`backend/compiler/semantic_compiler.py`**
   - Offline and incrementally refreshable `SemanticContextCompiler`.
   - Extracts schemas, constraints, candidate FK/join relationships, cardinality/distribution profiles, categorical value dictionaries, representative samples, and metric candidates.
   - Applies template deduplication and dictionary encoding, measuring raw vs compressed byte size and `tiktoken` token counts.
6. **`backend/agents/`**
   - `llm_engine.py`: Pluggable reasoning backend supporting OpenAI/Anthropic/Compatible LLMs when configured and a schema-agnostic, tool-driven Analytical Planner when running offline.
   - `explorer.py`: Bounded iterative investigation loop (`DISCOVER -> IDENTIFY KNOWLEDGE GAPS -> INSPECT -> QUERY -> OBSERVE -> VERIFY -> ANSWER`) with explicit structured `ExplorerInvestigationState`.
   - `hercules.py`: Independent reasoning path initialized with compiled compressed global semantic context, executing independent verification and queries without state leakage from Explorer.
7. **`backend/verification/`**
   - `validators.py`: Deterministic validators using `sqlglot` AST analysis and result inspection (syntax/column existence, source bindings, execution errors, aggregation grain, join fanout/multiplicity, null handling, currency/unit consistency, date/period interpretation, result reconciliation, financial invariants).
   - `adjudicator.py`: Evidence-based `AdjudicationEngine` that compares Explorer and Hercules candidates, runs discriminating tie-breaker queries when candidates diverge, and produces a grounded answer with full lineage.
8. **`backend/memory/learning_store.py`**
   - `PersistentLearningStore` with tenant/user isolation and schema-version invalidation for verified relationships, value mappings, query templates, failed query patterns, and metric definitions.
9. **`backend/orchestration/workflow.py`**
   - Parallel `LangGraph` state graph executing: `parse_request -> resolve_sources -> retrieve_knowledge -> [run_explorer || run_hercules] -> verify_candidates -> adjudicate -> targeted_followup (conditional) -> finalize_and_persist`.
10. **`backend/export/exporter.py`**
    - Result export engine supporting `CSV`, `Excel (.xlsx)`, `JSON`, and an extensible `ReportExporter` interface (including Markdown Audit Report).
11. **`backend/synthetic/`**
    - `schema_spec.py`: Complete definition and mapping of all 39 DBA-referenced tables across 8 domains using fictional `Aethelgard Enterprise` naming.
    - `generator.py`: Deterministic (seed-controlled) event-driven financial generator producing internally consistent records across all 39 tables plus deliberate edge cases and multi-format exported files (`CSV`, `Parquet`, `Excel`, `JSON`).
    - `ground_truth.py`: Independent ground-truth benchmark with 12 financial & cross-domain questions, deterministic verification queries, and evaluation runner comparing `Explorer`, `Hercules`, `Dual-Path`, and `Dual-Path + Memory`.
12. **`backend/api/app.py` & `run_demo.py`**
    - FastAPI REST service and runnable CLI demonstration script.
