"""Rename an existing local database from the old "udre" names to the "backend" names. Safe to run more than once.

    python scripts/migrate_backend_rename.py

Stop the backend and the ingestion service first (a database cannot be renamed while it has connections).
  database  udre_knowledge                    -> backend_knowledge
  schemas   udre_kg / udre_memory / udre_runtime -> backend_kg / backend_memory / backend_runtime
  table     <data db>.public.udre_outbox      -> backend_outbox (knowledge-ingestion's event queue)
Old test databases (udre_sync_lab, udre_crm_sync_lab) are dropped; the tests recreate them under the new names.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import Settings, pg_uri  # noqa: E402
from backend.pg_embedded import base_uri  # noqa: E402


def exists(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    return cur.fetchone() is not None


def main() -> None:
    admin = psycopg2.connect(base_uri())
    admin.autocommit = True
    with admin.cursor() as cur:
        if exists(cur, "udre_knowledge") and not exists(cur, "backend_knowledge"):
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'udre_knowledge'")
            cur.execute("ALTER DATABASE udre_knowledge RENAME TO backend_knowledge")
            print("renamed database udre_knowledge -> backend_knowledge")
        for old in ("udre_sync_lab", "udre_crm_sync_lab"):
            if exists(cur, old):
                cur.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{old}'")
                cur.execute(f"DROP DATABASE {old}")
                print(f"dropped test database {old}")
        has_knowledge = exists(cur, "backend_knowledge")
    admin.close()

    settings = Settings()
    if has_knowledge:
        with psycopg2.connect(pg_uri(settings.knowledge_db)) as conn, conn.cursor() as cur:
            for part in ("kg", "memory", "runtime"):
                cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (f"udre_{part}",))
                if cur.fetchone():
                    cur.execute(f"ALTER SCHEMA udre_{part} RENAME TO backend_{part}")
                    print(f"renamed schema udre_{part} -> backend_{part}")
    with psycopg2.connect(pg_uri(settings.data_db)) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.udre_outbox') IS NOT NULL AND to_regclass('public.backend_outbox') IS NULL")
        if cur.fetchone()[0]:
            cur.execute("ALTER TABLE public.udre_outbox RENAME TO backend_outbox")
            print("renamed table public.udre_outbox -> backend_outbox")
    print("done")


if __name__ == "__main__":
    main()
