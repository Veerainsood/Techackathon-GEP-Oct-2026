from backend.orchestration.charts import ChartPlan, check, leakage_charts, plan_charts


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
                        arguments={"chart": {"type": "bar", "title": "Made up", "x": "region", "series": ["profit"]}})
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
