import hashlib

import psycopg2
import pytest

from backend.synthetic.generator import SyntheticFinanceGenerator
from backend.synthetic.ground_truth import FINANCIAL_INVARIANTS, build_benchmark
from backend.synthetic.schema_spec import SYNTHETIC_SCHEMAS, generate_postgresql_ddl, get_all_39_table_definitions


def _digest(frames):
    h = hashlib.sha256()
    for name in sorted(frames):
        h.update(name.encode())
        h.update(frames[name].to_csv(index=False).encode())
    return h.hexdigest()


def test_schema_covers_all_39_dba_tables_in_8_domains():
    tables = get_all_39_table_definitions()
    assert len(tables) == 39
    assert {t.original_domain for t in tables} == set(SYNTHETIC_SCHEMAS)
    assert len({t.full_name for t in tables}) == 39
    ddl = generate_postgresql_ddl()
    assert ddl.count("CREATE TABLE IF NOT EXISTS") == 41  # 39 + 2 knowledge-graph tables
    assert "FOREIGN KEY" in ddl and "COMMENT ON TABLE" in ddl


def test_generation_is_deterministic(generator):
    assert _digest(generator.frames) == _digest(SyntheticFinanceGenerator().generate())
    assert _digest(generator.frames) != _digest(SyntheticFinanceGenerator(seed=7).generate())


@pytest.mark.parametrize("inv", FINANCIAL_INVARIANTS, ids=lambda i: i["name"])
def test_financial_invariants_hold(pg_uri, inv):
    with psycopg2.connect(pg_uri) as conn, conn.cursor() as cur:
        cur.execute(inv["sql"])
        assert cur.fetchone()[0] == 0


def test_edge_cases_present(generator):
    f = generator.frames
    inv = f["ledger_erp.sales_invoices"]
    assert {"VOID", "PARTIALLY_PAID", "CREDITED", "OVERDUE", "CLOSED", "WRITTEN_OFF"} <= set(inv.settlement_status)
    assert set(inv.doc_currency) == {"USD", "EUR", "GBP"}
    assert inv.agreement_id.isna().any()
    assert f["ledger_erp.receivable_debtors"].conglomerate_id.isna().any()
    assert f["agreements.agreement_files"].matched_group_id.isna().any()
    assert {"REVERSED", "PENDING_APPROVAL"} <= set(f["ledger_erp.journal_vouchers"].posting_status)
    assert f["pipeline_crm.deal_history_snapshots"].stage_at_snapshot.isin(["CLOSED-WON", "Qual", "closed lost"]).any()


def test_ground_truth_matches_verification_sql(generator, pg_uri):
    # The live warehouse keeps changing (HubSpot sync writes CRM rows and batch logs), so the expected answers are
    # checked against a fresh copy of the generated data.
    from backend import pg_embedded
    from backend.synthetic.loader import load_to_postgres

    pristine = pg_embedded.ensure_database("backend_dataset_check")
    load_to_postgres(generator.frames, pristine)
    bench = build_benchmark(generator)
    with psycopg2.connect(pristine) as conn, conn.cursor() as cur:
        for q in bench:
            if q["behavior"] != "answer":
                continue
            cur.execute(q["verification_sql"])
            rows = cur.fetchall()
            if q["answer_type"] == "scalar":
                got, exp = float(rows[0][0]), float(q["expected"])
                if q["sign_insensitive"]:
                    got, exp = abs(got), abs(exp)
                assert abs(got - exp) < 0.006, q["id"]
            else:
                assert {str(r[0]): round(float(r[1]), 2) for r in rows} == q["expected"], q["id"]
