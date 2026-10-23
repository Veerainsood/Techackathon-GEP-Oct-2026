"""Export shareable copies of the synthetic database into share/.

Produces PostgreSQL dumps (plain SQL with data, schema-only, custom format), a single-file DuckDB
copy of all 39 tables, and a zip of the sample upload files.

Usage: python scripts/export_share.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from udre import pg_embedded  # noqa: E402
from udre.synthetic.generator import SyntheticFinanceGenerator  # noqa: E402
from udre.synthetic.loader import DATASET_DB, export_files, get_postgres_uri  # noqa: E402


def main() -> None:
    out = ROOT / "share"
    out.mkdir(exist_ok=True)
    get_postgres_uri()  # ensure the embedded server and database exist
    pg_dump = pg_embedded._bin("pg_dump")
    base = ["-h", "127.0.0.1", "-p", str(pg_embedded.DEFAULT_PORT), "-U", "postgres", "--no-owner", "--no-privileges"]
    dumps = {
        "aethelgard_finance_full.sql": [],
        "aethelgard_finance_schema_only.sql": ["--schema-only"],
        "aethelgard_finance.dump": ["-Fc"],
    }
    for name, extra in dumps.items():
        subprocess.run([pg_dump, *base, *extra, "-f", str(out / name), DATASET_DB], check=True)

    mirror = ROOT / "data" / "parquet_mirror"
    if not mirror.exists() or not any(mirror.glob("*.parquet")):
        export_files(SyntheticFinanceGenerator().generate(), str(ROOT / "data"))
    db_path = out / "aethelgard_finance.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    for f in sorted(mirror.glob("*.parquet")):
        schema, table = f.stem.split("__")
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        con.execute(f"CREATE TABLE {schema}.{table} AS SELECT * FROM read_parquet(?)", [str(f)])
    tables = con.execute("SELECT COUNT(*) FROM duckdb_tables()").fetchone()[0]
    con.close()

    shutil.make_archive(str(out / "uploaded_files_sample"), "zip", ROOT / "data" / "uploads")
    print(f"duckdb tables: {tables}")
    for p in sorted(out.iterdir()):
        print(f"{p.name:<40} {os.path.getsize(p) / 1e6:8.2f} MB")


if __name__ == "__main__":
    main()
