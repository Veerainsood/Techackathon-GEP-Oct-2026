"""Load the synthetic dataset into PostgreSQL and export file-based variants."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from backend.synthetic.schema_spec import SYNTHETIC_SCHEMAS, generate_postgresql_ddl

DATASET_DB = "aethelgard_finance"


def get_postgres_uri(data_dir: Optional[str] = None, database: str = DATASET_DB) -> str:
    """Return a PostgreSQL URI.

    Uses ``BACKEND_PG_URI`` when set; otherwise starts (or reuses) the embedded PostgreSQL 16
    server with pgvector (see ``backend.pg_embedded``).
    """
    explicit = os.environ.get("BACKEND_PG_URI")
    if explicit:
        return explicit
    from backend import pg_embedded

    if data_dir:
        os.environ["BACKEND_PGDATA"] = data_dir
    return pg_embedded.ensure_database(database)


def _records(df: pd.DataFrame):
    obj = df.astype(object)
    obj = obj.where(pd.notnull(obj), None)
    return [tuple(r) for r in obj.itertuples(index=False, name=None)]


def load_to_postgres(frames: Dict[str, pd.DataFrame], uri: str) -> Dict[str, int]:
    """(Re)create the synthetic schemas and bulk-load every table. Only touches the synthetic schemas."""
    conn = psycopg2.connect(uri)
    conn.autocommit = False
    counts: Dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for schema in SYNTHETIC_SCHEMAS.values():
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            ddl = generate_postgresql_ddl(include_pgvector=False, include_kg=False)
            # create tables first, then load, then add FKs/indexes for speed
            create_part, _, rest = ddl.partition("-- Foreign Key Constraints")
            cur.execute(create_part.rsplit("-- =====", 1)[0])
            for full_name, df in frames.items():
                cols = ", ".join(df.columns)
                execute_values(cur, f"INSERT INTO {full_name} ({cols}) VALUES %s", _records(df), page_size=2000)
                counts[full_name] = len(df)
            fk_and_idx = "\n".join(l for l in rest.splitlines() if l.startswith(("ALTER TABLE", "CREATE INDEX")))
            cur.execute(fk_and_idx)
            cur.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()
    return counts


def export_files(frames: Dict[str, pd.DataFrame], out_dir: str) -> Dict[str, str]:
    """Export a realistic 'uploaded files' bundle in several formats plus full parquet mirrors."""
    out = Path(out_dir)
    (out / "uploads").mkdir(parents=True, exist_ok=True)
    (out / "parquet_mirror").mkdir(parents=True, exist_ok=True)
    written: Dict[str, str] = {}
    uploads = {
        "invoice_register_export.csv": ("ledger_erp.sales_invoices", "csv"),
        "customer_master.csv": ("ledger_erp.receivable_debtors", "csv"),
        "gl_postings_extract.parquet": ("ledger_erp.journal_postings", "parquet"),
        "gl_vouchers_extract.parquet": ("ledger_erp.journal_vouchers", "parquet"),
        "plan_targets_workbook.xlsx": ("fpna_plan.financial_targets", "excel"),
        "crm_opportunities_dump.json": ("pipeline_crm.deal_opportunities", "json"),
    }
    for fname, (table, fmt) in uploads.items():
        df = frames[table]
        path = out / "uploads" / fname
        if fmt == "csv":
            df.to_csv(path, index=False)
        elif fmt == "parquet":
            df.to_parquet(path, index=False)
        elif fmt == "excel":
            df.to_excel(path, index=False, sheet_name="targets")
        elif fmt == "json":
            df.to_json(path, orient="records", date_format="iso", indent=1)
        written[fname] = str(path)
    for table, df in frames.items():
        p = out / "parquet_mirror" / f"{table.replace('.', '__')}.parquet"
        df.to_parquet(p, index=False)
    return written
