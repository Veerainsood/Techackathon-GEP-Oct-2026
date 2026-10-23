# Knowledge Ingestion: Implementation Status

This document describes what is built so far in `knowledge-ingestion/`, how HubSpot data gets into the database, and what is still open.

## 1. What the service does today

The service copies contacts and companies from HubSpot into a local database. It only reads from HubSpot and never writes back to it.

1. On startup, and then every `POLL_INTERVAL_SECONDS` (default 300), the API asks HubSpot for records changed since the last run.
2. Each poll is recorded as a run and given a collection name, such as `hubspot_20261023T174634Z`, with start and end times.
3. New or changed record versions are saved. Unchanged versions are skipped.
4. `POST /hubspot/sync` runs the same process on demand and returns what was imported.

## 2. How HubSpot data gets into the database

```
HubSpot CRM
   |  search API (read only): records modified since the cursor, minus a 2-minute overlap
   v
API process (this PC for now)
   |  1. start a run row in ingestion_runs
   |  2. for each object type (contacts, companies):
   |       - read the cursor from sync_state
   |       - search HubSpot for records changed since cursor minus 2 minutes
   |       - skip records whose lastmodifieddate matches the stored version
   |       - move the cursor to the newest modified time seen
   |  3. save all new snapshots, cursor updates, and run status in one transaction
   |  4. on any error: mark the run failed with the error text, cursor unchanged
   v
Database (SQLite file knowledge.db for now)
   ingestion_runs | raw_records | sync_state
```

Key design points:
- **Append-only history.** Every changed version is a new row in `raw_records`. Older versions are never overwritten.
- **Overlap window.** Each poll re-reads 2 minutes before the cursor, so records HubSpot indexes late are not missed.
- **One poll at a time.** A lock stops the background poll and a manual sync from running together.
- **Failures are recorded.** A failed run keeps its error message and does not move the cursor, so the next poll retries the same records.
- **HubSpot search limit.** HubSpot returns at most 10,000 changed records per search. Larger changes are reported as a failed run.

## 3. Files implemented

| File | Purpose |
|---|---|
| `knowledge_ingestion/config.py` | Reads `.env`, validates `DATABASE_URL` (supported drivers: `sqlite`, `postgresql+psycopg`, `mssql+pyodbc`), reads polling settings. |
| `knowledge_ingestion/connectors/hubspot/client.py` | Read-only HubSpot client: list (GET) and search (POST, read only). Raises `HubSpotError` on failure. |
| `knowledge_ingestion/connectors/hubspot/properties.py` | Lists the HubSpot properties to read for each object type, and which property marks a change. |
| `knowledge_ingestion/connectors/hubspot/check_connection.py` | Command-line smoke test that confirms the HubSpot token works. |
| `knowledge_ingestion/pipeline/hubspot_sync.py` | The sync logic: cursor, overlap, dedupe, saving, and run status. Also the single-poll lock. |
| `knowledge_ingestion/storage/base.py` | The storage interface the pipeline depends on. Keeps the pipeline independent of the database. |
| `knowledge_ingestion/storage/sql_store.py` | SQLAlchemy implementation of the storage interface, plus the three table definitions. Works with SQLite, PostgreSQL, and SQL Server. |
| `knowledge_ingestion/storage/factory.py` | Creates one shared store instance and makes sure the tables exist. |
| `knowledge_ingestion/api/app.py` | FastAPI application: background poller, and the HTTP endpoints. |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Template for `.env`, listing every setting without real values. |
| `docker-compose.yml` | Local SQL Server container. Not used now, kept for later. |
| `README.md` | Setup and usage guide. Still describes SQL Server and Azure; needs updating for SQLite. |
| `data/seed/hubspot-sample-contacts.csv` | Sample contacts for manual import testing in HubSpot. |
| `data/test/new-contacts-for-import.csv` | Two test contacts for checking the import path. |
| `knowledge.db` | The local SQLite database created at runtime. Not source code. |

## 4. Database tables

**`ingestion_runs`**: one row per sync or poll.
- `id`, `source_system`, `started_at`, `finished_at`, `status` (`running`, `succeeded`, `failed`), `record_counts` (JSON text), `error`

**`raw_records`**: one row per stored version of a record. Only grows.
- `id`, `run_id` (links to `ingestion_runs`), `source_system`, `object_type` (`contacts` or `companies`), `source_id` (HubSpot record ID), `payload` (properties as JSON text), `fetched_at` (UTC)
- Index `raw_records_lookup` on `source_system`, `object_type`, `source_id`, `fetched_at`

**`sync_state`**: one row per object type, holding where the next poll starts.
- `source_system`, `object_type` (primary key), `cursor_ms`, `updated_at`

Timestamps are stored in UTC.

## 5. API endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness check. |
| POST | `/hubspot/sync` | Runs a poll now. Returns run ID, collection name, status, start and end times, counts, and imported records. Returns 502 if HubSpot fails. |
| GET | `/collections?limit=` | Lists recent runs with their collection names, status, times, counts, and errors. |
| GET | `/knowledge/records?object_type=&source=&limit=` | Returns the latest stored version of each record. |

Interactive docs: `http://localhost:8000/docs`

## 6. Configuration (`.env`)

| Variable | Status | Purpose |
|---|---|---|
| `HUBSPOT_PORTAL_ID` | Set | HubSpot test account ID. |
| `HUBSPOT_ACCESS_TOKEN` | Set (secret) | HubSpot private app token. |
| `DATABASE_URL` | Set to `sqlite:///./knowledge.db` | Database location. Changing this is the only change needed to switch databases. |
| `POLL_ENABLED` | Optional, default `true` | Turns the background poll on or off. |
| `POLL_INTERVAL_SECONDS` | Optional, default `300` | Seconds between polls. Minimum 30. |

## 7. How to run

From the `knowledge-ingestion` folder:

```powershell
.\.venv\Scripts\python.exe -m uvicorn knowledge_ingestion.api.app:app --port 8000
```

