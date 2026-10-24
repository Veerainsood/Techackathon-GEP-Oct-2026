from backend.orchestration.charts import (ChartPlan, _num, check, leakage_charts, normalize, period_key, plan_charts,
                                          strip_text_charts)

# The forecast answer from the demo: long format, two currencies, actual/forecast, quarter total rows mixed in.
FORECAST = [
    {"period_type": "Actual", "month_label": "July 2025", "currency_code": "INR", "value": 299520},
    {"period_type": "Actual", "month_label": "September 2025", "currency_code": "INR", "value": 349024},
    {"period_type": "Actual", "month_label": "Q3 2025 Total", "currency_code": "INR", "value": 648544},
    {"period_type": "Forecast", "month_label": "January 2026", "currency_code": "INR", "value": 216181.33},
    {"period_type": "Forecast", "month_label": "February 2026", "currency_code": "INR", "value": 216181.33},
    {"period_type": "Forecast", "month_label": "March 2026", "currency_code": "INR", "value": 216181.33},
    {"period_type": "Forecast", "month_label": "Q1 2026 Total", "currency_code": "INR", "value": 648544},
    {"period_type": "Actual", "month_label": "July 2025", "currency_code": "USD", "value": 3493684},
    {"period_type": "Actual", "month_label": "August 2025", "currency_code": "USD", "value": 3660583.31},
    {"period_type": "Actual", "month_label": "September 2025", "currency_code": "USD", "value": 3017146.92},
    {"period_type": "Actual", "month_label": "Q3 2025 Total", "currency_code": "USD", "value": 10171414.23},
    {"period_type": "Forecast", "month_label": "January 2026", "currency_code": "USD", "value": 3390471.41},
    {"period_type": "Forecast", "month_label": "February 2026", "currency_code": "USD", "value": 3390471.41},
    {"period_type": "Forecast", "month_label": "March 2026", "currency_code": "USD", "value": 3390471.41},
    {"period_type": "Forecast", "month_label": "Q1 2026 Total", "currency_code": "USD", "value": 10171414.23},
]


def test_forecast_rows_become_one_actual_vs_forecast_chart_per_currency():
    charts = plan_charts("predict Q1 2026 from Q3 spending, keep USD and INR separate", FORECAST)
    assert [(c["type"], c["unit"]) for c in charts] == [("line", "inr"), ("line", "usd")]
    usd = charts[1]
    assert [s["key"] for s in usd["series"]] == ["Actual", "Forecast"] and usd["series"][1]["dashed"]
    assert [d["label"] for d in usd["data"]] == ["July 2025", "August 2025", "September 2025", "January 2026", "February 2026", "March 2026"]
    assert not any("Total" in d["label"] for c in charts for d in c["data"])  # quarter totals are not plotted
    assert usd["boundary"] == "September 2025" and usd["data"][2]["Forecast"] == 3017146.92  # forecast joins the actuals
    assert usd["data"][0]["Actual"] == 3493684 and "Forecast" not in usd["data"][0]


class _DecliningModel:
    """Says a chart would not help; the rules chart is used anyway."""
    model = "fake"

    async def acomplete(self, system, messages, tools, tool_choice=None, max_tokens=None, usage=None):
        from backend.llm.base import LLMResponse, ToolCall

        return LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="submit_chart", arguments={"charts": [], "reason": "no"})],
                           stop_reason="tool_use")


class _MixingModel:
    """Puts USD and INR on one axis; rejected twice, so the rules plan (one chart per currency) is used."""
    model = "fake"

    async def acomplete(self, system, messages, tools, tool_choice=None, max_tokens=None, usage=None):
        from backend.llm.base import LLMResponse, ToolCall

        plan = {"type": "line", "title": "Spend", "x": "month_label", "series": ["value"], "split_by": "period_type"}
        return LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="submit_chart", arguments={"charts": [plan]})], stop_reason="tool_use")


def test_a_declining_or_currency_mixing_model_still_gets_the_right_charts():
    assert [c["unit"] for c in plan_charts("forecast", FORECAST, llm=_DecliningModel())] == ["inr", "usd"]
    charts = plan_charts("forecast", FORECAST, llm=_MixingModel())
    assert [c["unit"] for c in charts] == ["inr", "usd"] and charts[0]["chosen_by"] == "rules_fallback"


def test_normalisation_parses_money_periods_and_wide_tables():
    assert [_num(v) for v in ("₹299,520.00", "$3.4M", "(1,700)", "USD 12k", "-5", "12.5%", "INV-001", "2025-07")] == \
        [299520.0, 3400000.0, -1700.0, 12000.0, -5.0, 12.5, None, None]
    keys = [period_key(v) for v in ("July 2025", "Jul-25", "2025-07", "Q3 2025", "2025 Q3", "FY2025", "Sept 2025", 2024, "Acme")]
    assert keys == [(2025, 7, 0), (2025, 7, 0), (2025, 7, 0), (2025, 7, 0), (2025, 7, 0), (2025, 0, 0), (2025, 9, 0), (2024, 0, 0), None]
    wide = [{"region": "APAC", "jan_2025": "1,000", "feb_2025": "1,500", "mar_2025": "2,000"},
            {"region": "EMEA", "jan_2025": "800", "feb_2025": "900", "mar_2025": "950"}]
    chart = plan_charts("monthly spend by region", wide)[0]
    assert chart["type"] == "line" and [s["key"] for s in chart["series"]] == ["APAC", "EMEA"]
    assert [d["label"] for d in chart["data"]] == ["jan 2025", "feb 2025", "mar 2025"] and chart["data"][1]["APAC"] == 1500.0


def test_unsorted_months_are_sorted_and_real_names_with_total_are_kept():
    rows = [{"month": m, "spend_usd": v} for m, v in (("Mar 2025", 3), ("Jan 2025", 1), ("Feb 2025", 2))]
    assert [d["label"] for d in plan_charts("spend by month", rows)[0]["data"]] == ["Jan 2025", "Feb 2025", "Mar 2025"]
    vendors = [{"vendor": "Total Wine & More", "spend_usd": 50.0}, {"vendor": "Acme", "spend_usd": 20.0}, {"vendor": "Beta", "spend_usd": 10.0}]
    assert len(normalize(vendors)) == 3  # 'Total' in a name is not a total row: 50 is not the sum of the others
    with_total = vendors + [{"vendor": "Grand total", "spend_usd": 80.0}]
    assert [r["vendor"] for r in normalize(with_total)] == ["Total Wine & More", "Acme", "Beta"]


class _Schema:
    def __init__(self, cols):
        self.columns = [type("C", (), {"name": n, "data_type": t}) for n, t in cols.items()]


class _Adapter:
    dialect = "postgres"

    def describe_collection(self, name):
        return _Schema({"invoice_date": "date", "currency_code": "text", "amount": "numeric", "gross_total_usd": "double precision",
                        "invoice_id": "text"})


class _Gateway:
    def adapter(self, ctx, sid):
        return _Adapter()


def test_column_meaning_comes_from_the_sql_not_the_alias():
    from backend.orchestration.semantics import result_semantics

    sql = """WITH m AS (SELECT DATE_TRUNC('month', i.invoice_date) AS mon, i.currency_code AS cur, SUM(i.gross_total_usd) AS amt,
                     COUNT(*) AS n, SUM(i.amount) AS raw FROM ledger_erp.sales_invoices i GROUP BY 1, 2)
             SELECT 'Actual' AS kind, TO_CHAR(mon, 'Mon YYYY') AS lbl, cur AS c1, amt AS v1, n AS v2, raw AS v3 FROM m"""
    sem = result_semantics(_Gateway(), None, "s", sql, ["kind", "lbl", "c1", "v1", "v2", "v3"])
    assert sem["v1"]["unit"] == "usd" and sem["v2"]["unit"] == "count" and sem["v3"]["unit"] is None  # no currency known
    assert sem["lbl"]["kind"] == "time" and sem["c1"]["role"] == "currency" and "kind" not in sem
    pinned = result_semantics(_Gateway(), None, "s", "SELECT SUM(amount) AS x FROM ledger_erp.sales_invoices WHERE currency_code = 'INR'", ["x"])
    assert pinned["x"]["unit"] == "inr"
    # the hint wins over the alias: 'v2' looks like nothing, but it is a count
    rows = [{"lbl": "Jan 2025", "v2": 3}, {"lbl": "Feb 2025", "v2": 5}]
    assert plan_charts("trend", rows, hints={"lbl": {"kind": "time"}, "v2": {"kind": "measure", "unit": "count"}})[0]["unit"] == "count"


def test_design_is_computed_from_the_data_and_the_model_cannot_write_numbers():
    rows = [{"month": m, "spend_usd": v} for m, v in (("Jan 2025", 100.0), ("Feb 2025", 300.0), ("Mar 2025", 200.0))]
    chart = plan_charts("monthly spend", rows)[0]
    assert {r["kind"]: r["value"] for r in chart["references"]} == {"average": 200.0, "peak": 300.0}
    assert chart["takeaway"] == "Peak $300 in Feb 2025, against an average of $200"
    ok = ChartPlan(type="line", title="t", x="month", series=["spend_usd"], takeaway="Peak {peak} in {peak_label}")
    assert check(ok, rows) is None
    assert "digits" in check(ok.model_copy(update={"takeaway": "Spend hit 300 in February"}), rows)
    assert "placeholders" in check(ok.model_copy(update={"takeaway": "{record}"}), rows)
    target = ok.model_copy(update={"references": ["target"], "target": 250.0})
    assert "stated in the question" in check(target, rows, question="monthly spend")
    assert check(target, rows, question="monthly spend against a target of 250") is None
    bars = plan_charts("spend by region", [{"region": r, "spend_usd": v} for r, v in (("APAC", 30.0), ("EMEA", 70.0))])[0]
    assert bars["highlight"] == "EMEA" and bars["takeaway"] == "EMEA leads with $70.00, 70.0% of the total"
    forecast = plan_charts("forecast", FORECAST)[1]
    assert forecast["takeaway"] == "Forecast $10.2M against $10.2M in the actual periods"


def test_text_charts_are_removed_from_answers():
    text = ("Forecast for Q1 2026 is $10.2M.\n\nVisualization (Text-Based: Actual Q3 vs Forecast Q1)\n\n"
            "USD Monthly Spending (in millions) July |████████████████ $3.49M August |█████████████████ $3.66M\n\n"
            "Data quality: one duplicate removed.")
    assert strip_text_charts(text) == "Forecast for Q1 2026 is $10.2M.\n\nData quality: one duplicate removed."
    assert strip_text_charts("No bars here.") == "No bars here."


def kinds(question, rows):
    return [(c["type"], c.get("x"), [s["key"] for s in c["series"]]) for c in plan_charts(question, rows)]


def test_rules_pick_the_chart_from_the_result_shape():
    years = [{"year": 2023, "revenue_usd": 1.2e6}, {"year": 2024, "revenue_usd": 1.5e6}]
    assert kinds("revenue by year", years) == [("line", "year", ["revenue_usd"])]  # numeric years are periods, not values
    regions = [{"region": r, "open_balance_usd": v, "invoices": n} for r, v, n in [("APAC", 5e5, 12), ("EMEA", 3e5, 9)]]
    assert kinds("open balance by region", regions) == [("bar", "region", ["open_balance_usd"])]  # one unit per chart
    mix = [{"category": c, "spend": v} for c, v in [("IT", 40), ("Travel", 25), ("Office", 35)]]
    assert kinds("share of spend by category", mix)[0][0] == "donut"
    long = [{"region": r, "status": s, "amount_usd": 10} for r in ("APAC", "EMEA") for s in ("PAID", "OVERDUE")]
    stacked = plan_charts("amount by region and status", long)[0]
    assert stacked["type"] == "stacked_bar" and stacked["data"] == [{"label": "APAC", "PAID": 10.0, "OVERDUE": 10.0},
                                                                     {"label": "EMEA", "PAID": 10.0, "OVERDUE": 10.0}]
    assert kinds("total overdue", [{"total_overdue_usd": 123.4}]) == [("kpi", None, ["total_overdue_usd"])]
    records = [{"invoice_id": f"INV-{i}", "customer": f"C{i}", "amount_usd": i} for i in range(30)]
    assert plan_charts("list overdue invoices", records) == []  # individual records are a table, not a chart


def test_a_model_plan_must_fit_the_verified_rows():
    rows = [{"region": "APAC", "amount_usd": 1.0}, {"region": "EMEA", "amount_usd": 2.0}]
    assert check(ChartPlan(type="bar", title="t", x="region", series=["amount_usd"]), rows) is None
    assert "unknown column" in check(ChartPlan(type="bar", title="t", x="region", series=["revenue"]), rows)
    assert "not a numeric" in check(ChartPlan(type="bar", title="t", x="amount_usd", series=["region"]), rows)
    assert check(ChartPlan(type="kpi", title="t", series=["amount_usd"]), rows) == "kpi needs a single row"


class _BadModel:
    """Always proposes a column that is not in the result; the rules plan must be used instead."""
    model = "fake"

    async def acomplete(self, system, messages, tools, tool_choice=None, max_tokens=None, usage=None):
        from backend.llm.base import LLMResponse, ToolCall

        call = ToolCall(id="c1", name="submit_chart",
                        arguments={"charts": [{"type": "bar", "title": "Made up", "x": "region", "series": ["profit"]}]})
        return LLMResponse(text="", tool_calls=[call], stop_reason="tool_use")


def test_an_invalid_model_choice_falls_back_to_rules():
    rows = [{"region": "APAC", "amount_usd": 1.0}, {"region": "EMEA", "amount_usd": 2.0}]
    chart = plan_charts("amount by region", rows, llm=_BadModel())[0]
    assert chart["chosen_by"] == "rules_fallback" and [s["key"] for s in chart["series"]] == ["amount_usd"]


def test_leakage_charts_sum_the_cases():
    cases = [{"type": "duplicate_invoice", "supplier_name": "Acme", "recoverable_usd": 100.0},
             {"type": "duplicate_invoice", "supplier_name": "Beta", "recoverable_usd": 40.0},
             {"type": "price_variance", "supplier_name": "Acme", "recoverable_usd": 10.0}]
    kpi, donut, vendors = leakage_charts(cases, {"duplicate_invoice": "Duplicate invoices", "price_variance": "Price above contract"})
    assert kpi["data"] == [{"recoverable_usd": 150.0, "cases": 3}]
    assert donut["data"][0] == {"label": "Duplicate invoices", "recoverable_usd": 140.0, "cases": 2}
    assert vendors["data"] == [{"label": "Acme", "recoverable_usd": 110.0}, {"label": "Beta", "recoverable_usd": 40.0}]


def test_answers_carry_chart_plans(pg_uri, uploads_dir):
    from backend.api.app import result_payload
    from backend.engine import build_engine

    e = build_engine(llm_providers=(None, None, None, None))
    q = "What is the total open balance in USD by currency?"
    payload = result_payload(e.ask(q, use_memory=False), q)
    assert isinstance(payload["charts"], list)
    if len(payload["data"]) > 1:
        assert payload["charts"] and payload["charts"][0]["data"]
