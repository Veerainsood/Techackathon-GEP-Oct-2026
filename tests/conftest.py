import os
import sys
from pathlib import Path

# Unit tests never call external LLMs or sandboxes; live smoke tests opt in explicitly (UDRE_LIVE_SMOKE=1).
os.environ.setdefault("UDRE_REASONING_MODE", "offline")
os.environ.setdefault("UDRE_SANDBOX", "off")

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from udre.synthetic.generator import SyntheticFinanceGenerator  # noqa: E402
from udre.synthetic.loader import export_files, get_postgres_uri, load_to_postgres  # noqa: E402


@pytest.fixture(scope="session")
def generator():
    gen = SyntheticFinanceGenerator()
    gen.frames = gen.generate()
    return gen


@pytest.fixture(scope="session")
def pg_uri(generator):
    uri = get_postgres_uri()
    conn = psycopg2.connect(uri)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('ledger_erp.sales_invoices')")
        exists = cur.fetchone()[0] is not None
    conn.close()
    if not exists:
        load_to_postgres(generator.frames, uri)
    return uri


@pytest.fixture(scope="session")
def uploads_dir(generator):
    path = ROOT / "data" / "uploads"
    if not (path / "invoice_register_export.csv").exists():
        export_files(generator.frames, str(ROOT / "data"))
    return path


@pytest.fixture(scope="session")
def gateway(pg_uri, uploads_dir):
    from udre.gateway import SourceGateway
    from udre.gateway.file_adapter import FileSourceAdapter
    from udre.gateway.postgres_adapter import PostgresSourceAdapter

    gw = SourceGateway()
    gw.register(PostgresSourceAdapter("finance_pg", pg_uri), tenants=["default", "acme"])
    gw.register(FileSourceAdapter("uploads", [str(uploads_dir)]), tenants=["default"])
    return gw
