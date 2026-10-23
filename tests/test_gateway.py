import pytest

from udre.gateway import AccessContext, AccessDenied
from udre.gateway.extensible_adapters import RestApiSourceAdapter
from udre.gateway.sql_guard import UnsafeQueryError, analyze, assert_read_only, bound_rows

CTX = AccessContext()


def test_postgres_discovery_and_catalog(gateway):
    pg = gateway.adapter(CTX, "finance_pg")
    collections = gateway.collections(CTX, "finance_pg")
    assert len(collections) == 39
    s = pg.describe_collection("ledger_erp.sales_invoices")
    assert s.primary_keys == ["invoice_id"]
    assert {"column": "debtor_id", "references": "ledger_erp.receivable_debtors.debtor_id"} in s.foreign_keys
    assert s.description and any(c.description for c in s.columns)


def test_profile_and_value_search(gateway):
    pg = gateway.adapter(CTX, "finance_pg")
    prof = pg.profile_column("ledger_erp.sales_invoices", "settlement_status")
    assert prof.is_categorical and {t["value"] for t in prof.top_values} >= {"PAID", "OPEN", "VOID"}
    num = pg.profile_column("ledger_erp.sales_invoices", "gross_total_usd")
    assert num.mean_value and not num.is_categorical
    assert pg.search_values("ledger_erp.sales_invoices", "settlement_status", "partially paid")[0].matched_value == "PARTIALLY_PAID"
    assert pg.search_values("pipeline_crm.deal_opportunities", "pipeline_stage", "closed won")[0].matched_value == "Closed_Won"


def test_execute_and_bounds(gateway):
    r = gateway.execute_query(CTX, "finance_pg", "SELECT doc_currency, COUNT(*) n FROM ledger_erp.sales_invoices GROUP BY 1")
    assert r.succeeded and {row["doc_currency"] for row in r.rows} == {"USD", "EUR", "GBP"}
    r = gateway.execute_query(CTX, "finance_pg", "SELECT * FROM ledger_erp.journal_postings", max_rows=10)
    assert r.row_count == 10 and r.truncated


@pytest.mark.parametrize("sql", [
    "DELETE FROM ledger_erp.sales_invoices",
    "SELECT 1; DROP TABLE ledger_erp.sales_invoices",
    "SELECT pg_sleep(5)",
    "SELECT * FROM pg_catalog.pg_user",
    "UPDATE ledger_erp.sales_invoices SET open_balance_usd = 0",
    "SELECT * INTO hack FROM ledger_erp.sales_invoices",
])
def test_rejects_unsafe_queries(gateway, sql):
    r = gateway.execute_query(CTX, "finance_pg", sql)
    assert not r.succeeded and r.error_message.startswith("REJECTED")


def test_file_adapter_formats(gateway):
    cols = gateway.collections(CTX, "uploads")
    assert {"invoice_register_export", "gl_postings_extract", "plan_targets_workbook", "crm_opportunities_dump"} <= set(cols)
    r = gateway.execute_query(CTX, "uploads", "SELECT COUNT(*) AS n FROM invoice_register_export WHERE settlement_status = 'VOID'")
    assert r.succeeded and r.rows[0]["n"] > 0
    fs = gateway.adapter(CTX, "uploads")
    assert fs.describe_collection("plan_targets_workbook").row_count > 1000
    assert fs.profile_column("crm_opportunities_dump", "pipeline_stage").is_categorical
    blocked = gateway.execute_query(CTX, "uploads", "SELECT * FROM read_csv_auto('C:/secret.csv')")
    assert not blocked.succeeded


def test_tenant_and_collection_authorization(gateway):
    other = AccessContext(tenant_id="acme")
    assert [s.source_id for s in gateway.sources(other)] == ["finance_pg"]
    with pytest.raises(AccessDenied):
        gateway.adapter(other, "uploads")
    restricted = AccessContext(denied_collections={"finance_pg:portal_app.thread_utterances"})
    assert "portal_app.thread_utterances" not in gateway.collections(restricted, "finance_pg")
    r = gateway.execute_query(restricted, "finance_pg", "SELECT * FROM portal_app.thread_utterances")
    assert not r.succeeded


def test_sql_guard_helpers():
    with pytest.raises(UnsafeQueryError):
        assert_read_only("CREATE TABLE x (a int)", "postgres")
    qa = analyze("SELECT d.x, SUM(i.a) FROM s.i i JOIN s.d d ON d.k = i.k WHERE i.y = 'Z' GROUP BY d.x", "postgres")
    assert qa.tables == {"s.i", "s.d"} and qa.joins == 1 and qa.group_by == ["d.x"]
    assert "LIMIT 11" in bound_rows("SELECT * FROM t", "postgres", 10)
    assert "LIMIT 5" in bound_rows("SELECT * FROM t LIMIT 5", "postgres", 10)


def test_rest_adapter_scoped():
    payload = {"data": {"items": [{"code": "EUR", "rate": 1.08}, {"code": "GBP", "rate": 1.27}]}}
    api = RestApiSourceAdapter("fx_api", [{"name": "fx_rates", "url": "https://fx.example.com/v1/rates", "records_path": "data.items"}],
                               allowed_hosts=["fx.example.com"], fetcher=lambda url: payload)
    r = api.execute("SELECT code FROM fx_rates WHERE rate > 1.1")
    assert r.succeeded and r.rows == [{"code": "GBP"}]
    with pytest.raises(PermissionError):
        RestApiSourceAdapter("bad", [{"name": "x", "url": "https://evil.example.org/"}], allowed_hosts=["fx.example.com"])
