"""Independent ground truth for the synthetic financial dataset.

Expected answers are computed from the generator's in-memory event model (Python, integer
cents) -- never from the SQL engine -- and then cross-checked against deterministic SQL
verification queries.  The benchmark is split into a ``dev`` set (allowed for debugging) and a
``heldout`` set (only ever scored, never used to tune heuristics or prompts).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from udre.synthetic.generator import AS_OF, SyntheticFinanceGenerator, amt, period_of

# --------------------------------------------------------------------------- invariants
FINANCIAL_INVARIANTS: List[Dict[str, str]] = [
    {
        "name": "invoice_header_equals_sum_of_lines",
        "description": "Invoice subtotal (doc and USD) equals the sum of its line items.",
        "sql": """
            SELECT COUNT(*) AS violations FROM ledger_erp.sales_invoices h
            JOIN (SELECT invoice_id, SUM(line_net_amount_doc) d, SUM(line_net_amount_usd) u
                  FROM ledger_erp.sales_invoice_items GROUP BY invoice_id) l USING (invoice_id)
            WHERE h.subtotal_amount_doc <> l.d OR h.subtotal_amount_usd <> l.u""",
    },
    {
        "name": "gross_equals_subtotal_plus_tax",
        "description": "gross_total = subtotal + tax in both currencies.",
        "sql": """
            SELECT COUNT(*) AS violations FROM ledger_erp.sales_invoices
            WHERE gross_total_doc <> subtotal_amount_doc + tax_amount_doc
               OR gross_total_usd <> subtotal_amount_usd + tax_amount_usd""",
    },
    {
        "name": "open_balance_equals_ar_subledger",
        "description": "Invoice open balance equals the net of its AR subledger events (non-void).",
        "sql": """
            SELECT COUNT(*) AS violations FROM ledger_erp.sales_invoices h
            LEFT JOIN (SELECT applied_invoice_id, SUM(signed_amount_usd) s
                       FROM ledger_erp.receivable_ledger_events GROUP BY applied_invoice_id) e
                   ON e.applied_invoice_id = h.invoice_id
            WHERE h.settlement_status <> 'VOID' AND h.open_balance_usd <> COALESCE(e.s, 0)""",
    },
    {
        "name": "every_voucher_balances",
        "description": "Every journal voucher has equal debits and credits.",
        "sql": """
            SELECT COUNT(*) AS violations FROM (
              SELECT voucher_id FROM ledger_erp.journal_postings GROUP BY voucher_id
              HAVING SUM(debit_amount_usd) <> SUM(credit_amount_usd)) x""",
    },
    {
        "name": "ar_control_account_ties_to_open_invoices",
        "description": "Posted GL balance of the AR control account equals total open invoice balance.",
        "sql": """
            SELECT CASE WHEN ABS(
              (SELECT SUM(p.net_signed_usd) FROM ledger_erp.journal_postings p
                 JOIN ledger_erp.journal_vouchers v USING (voucher_id)
                 JOIN ledger_erp.chart_of_accounts a USING (account_id)
               WHERE v.posting_status = 'POSTED' AND a.account_number = '1100')
              - (SELECT SUM(open_balance_usd) FROM ledger_erp.sales_invoices)) < 0.005 THEN 0 ELSE 1 END AS violations""",
    },
    {
        "name": "snapshot_revenue_ties_to_gl",
        "description": "Monthly snapshot recognized revenue equals posted GL revenue (credits - debits) per period.",
        "sql": """
            SELECT COUNT(*) AS violations FROM (
              SELECT v.fiscal_period, -SUM(p.net_signed_usd) gl
              FROM ledger_erp.journal_postings p
              JOIN ledger_erp.journal_vouchers v USING (voucher_id)
              JOIN ledger_erp.chart_of_accounts a USING (account_id)
              WHERE v.posting_status = 'POSTED' AND a.account_class = 'REVENUE'
              GROUP BY v.fiscal_period) g
            FULL JOIN (SELECT snapshot_period, SUM(recognized_gl_revenue_usd) s
                       FROM exec_reporting.monthly_revenue_posture GROUP BY snapshot_period) r
              ON r.snapshot_period = g.fiscal_period
            WHERE ABS(COALESCE(g.gl, 0) - COALESCE(r.s, 0)) > 0.005""",
    },
    {
        "name": "variance_equals_actual_minus_budget",
        "description": "Snapshot variance = recognized revenue - budget target.",
        "sql": """
            SELECT COUNT(*) AS violations FROM exec_reporting.monthly_revenue_posture
            WHERE variance_to_budget_usd <> recognized_gl_revenue_usd - budget_target_usd""",
    },
    {
        "name": "outlook_combined_equals_parts",
        "description": "Forward outlook combined = locked contracted + weighted pipeline.",
        "sql": """
            SELECT COUNT(*) AS violations FROM exec_reporting.horizon_revenue_outlook
            WHERE combined_outlook_usd <> locked_contracted_rev_usd + weighted_pipeline_addition_usd""",
    },
    {
        "name": "budget_ties_to_snapshot",
        "description": "Snapshot budget targets equal FY approved budget revenue targets per period.",
        "sql": """
            SELECT COUNT(*) AS violations FROM (
              SELECT fiscal_period, SUM(target_amount_usd) b FROM fpna_plan.financial_targets
              WHERE metric_category = 'REVENUE_TARGET' AND plan_scenario = 'FY' || fiscal_year || '_APPROVED_BUDGET'
              GROUP BY fiscal_period) t
            JOIN (SELECT snapshot_period, SUM(budget_target_usd) s FROM exec_reporting.monthly_revenue_posture
                  GROUP BY snapshot_period) r ON r.snapshot_period = t.fiscal_period
            WHERE t.b <> r.s""",
    },
]


# --------------------------------------------------------------------------- benchmark
def build_benchmark(gen: SyntheticFinanceGenerator) -> List[Dict[str, Any]]:
    """Compute expected answers from the event model. Requires ``gen.generate()`` to have run."""
    R = gen.rows
    inv = gen.invoices
    non_void = [i for i in inv if i.status != "VOID"]
    deals = gen.deals
    events = [(i, e) for i in inv for e in i.events]
    q: List[Dict[str, Any]] = []

    def add(qid: str, split: str, question: str, expected: Any, sql: str, category: str,
            answer_type: str = "scalar", tables: List[str] = (), values: List[str] = (), behavior: str = "answer",
            sign_insensitive: bool = False) -> None:
        q.append(dict(id=qid, split=split, question=question, expected=expected, verification_sql=" ".join(sql.split()),
                      category=category, answer_type=answer_type, expected_tables=list(tables),
                      expected_values=list(values), behavior=behavior, sign_insensitive=sign_insensitive))

    # ---------------- dev
    add("D01", "dev", "What is the total open balance in USD across all sales invoices?",
        amt(sum(i.open_usd for i in inv)),
        "SELECT SUM(open_balance_usd) FROM ledger_erp.sales_invoices", "ar", tables=["ledger_erp.sales_invoices"])
    add("D02", "dev", "How many invoices have settlement status OVERDUE?",
        sum(1 for i in inv if i.status == "OVERDUE"),
        "SELECT COUNT(*) FROM ledger_erp.sales_invoices WHERE settlement_status = 'OVERDUE'", "ar",
        tables=["ledger_erp.sales_invoices"], values=["OVERDUE"])
    add("D03", "dev", "What is the total gross invoice amount in USD for invoices billed in EUR currency?",
        amt(sum(i.gross_usd for i in inv if i.currency == "EUR")),
        "SELECT SUM(gross_total_usd) FROM ledger_erp.sales_invoices WHERE doc_currency = 'EUR'", "fx",
        tables=["ledger_erp.sales_invoices"], values=["EUR"])
    add("D04", "dev", "How many deal opportunities are in the Commit pipeline stage?",
        sum(1 for d in deals if d["pipeline_stage"] == "Commit"),
        "SELECT COUNT(*) FROM pipeline_crm.deal_opportunities WHERE pipeline_stage = 'Commit'", "crm",
        tables=["pipeline_crm.deal_opportunities"], values=["Commit"])
    add("D05", "dev", "What is the total weighted ARR in USD of deals in the Negotiation stage?",
        amt(sum(int(round(d["weighted_arr_usd"] * 100)) for d in deals if d["pipeline_stage"] == "Negotiation")),
        "SELECT SUM(weighted_arr_usd) FROM pipeline_crm.deal_opportunities WHERE pipeline_stage = 'Negotiation'", "crm",
        tables=["pipeline_crm.deal_opportunities"], values=["Negotiation"])
    add("D06", "dev", "What is the total invoice subtotal amount in USD for fiscal period 2025-03, excluding VOID invoices?",
        amt(sum(i.subtotal_usd for i in non_void if period_of(i.invoice_date) == "2025-03")),
        "SELECT SUM(subtotal_amount_usd) FROM ledger_erp.sales_invoices WHERE fiscal_period = '2025-03' AND settlement_status <> 'VOID'",
        "revenue", tables=["ledger_erp.sales_invoices"], values=["2025-03", "VOID"])
    add("D07", "dev", "How many agreement files have execution status Fully Signed?",
        sum(1 for a in R["agreements.agreement_files"] if a["execution_status"] == "Fully_Signed"),
        "SELECT COUNT(*) FROM agreements.agreement_files WHERE execution_status = 'Fully_Signed'", "contracts",
        tables=["agreements.agreement_files"], values=["Fully_Signed"])
    add("D08", "dev", "What is the total target amount in USD for plan scenario FY2025_APPROVED_BUDGET and metric category REVENUE_TARGET?",
        amt(sum(int(round(t["target_amount_usd"] * 100)) for t in R["fpna_plan.financial_targets"]
                if t["plan_scenario"] == "FY2025_APPROVED_BUDGET" and t["metric_category"] == "REVENUE_TARGET")),
        "SELECT SUM(target_amount_usd) FROM fpna_plan.financial_targets WHERE plan_scenario = 'FY2025_APPROVED_BUDGET' AND metric_category = 'REVENUE_TARGET'",
        "budget", tables=["fpna_plan.financial_targets"], values=["FY2025_APPROVED_BUDGET", "REVENUE_TARGET"])
    add("D09", "dev", "What is the total cash receipt amount in USD for ledger events in 2025?",
        amt(-sum(e["usd_c"] for i, e in events if e["type"] == "CASH_RECEIPT" and e["date"].year == 2025)),
        "SELECT SUM(signed_amount_usd) FROM ledger_erp.receivable_ledger_events WHERE event_type = 'CASH_RECEIPT' AND fiscal_period LIKE '2025-%'",
        "cash", tables=["ledger_erp.receivable_ledger_events"], values=["CASH_RECEIPT"], sign_insensitive=True)
    by_status: Dict[str, int] = defaultdict(int)
    for i in inv:
        by_status[i.status] += i.open_usd
    add("D10", "dev", "What is the total open balance in USD by settlement status?",
        {k: amt(v) for k, v in sorted(by_status.items())},
        "SELECT settlement_status, SUM(open_balance_usd) FROM ledger_erp.sales_invoices GROUP BY settlement_status",
        "ar", answer_type="grouped", tables=["ledger_erp.sales_invoices"])

    # ---------------- held-out
    cells = gen.revenue_cells()
    add("H01", "heldout", "What is the total recognized GL revenue in USD for snapshot period 2025-06?",
        amt(sum(v for (p, *_r), v in cells.items() if p == "2025-06")),
        "SELECT SUM(recognized_gl_revenue_usd) FROM exec_reporting.monthly_revenue_posture WHERE snapshot_period = '2025-06'",
        "reconciliation", tables=["exec_reporting.monthly_revenue_posture"], values=["2025-06"])
    add("H02", "heldout", "How many receivable debtors have no conglomerate mapping?",
        sum(1 for d in R["ledger_erp.receivable_debtors"] if d["conglomerate_id"] is None),
        "SELECT COUNT(*) FROM ledger_erp.receivable_debtors WHERE conglomerate_id IS NULL", "identity",
        tables=["ledger_erp.receivable_debtors"])
    add("H03", "heldout", "What is the total gross invoice amount in USD for invoices billed in GBP currency in 2025?",
        amt(sum(i.gross_usd for i in inv if i.currency == "GBP" and i.invoice_date.year == 2025)),
        "SELECT SUM(gross_total_usd) FROM ledger_erp.sales_invoices WHERE doc_currency = 'GBP' AND fiscal_period LIKE '2025-%'",
        "fx", tables=["ledger_erp.sales_invoices"], values=["GBP"])
    add("H04", "heldout", "How many deal opportunities are Closed Won with a close fiscal period in 2025?",
        sum(1 for d in deals if d["pipeline_stage"] == "Closed_Won" and d["target_close_date"].year == 2025),
        "SELECT COUNT(*) FROM pipeline_crm.deal_opportunities WHERE pipeline_stage = 'Closed_Won' AND close_fiscal_period LIKE '2025-%'",
        "crm", tables=["pipeline_crm.deal_opportunities"], values=["Closed_Won"])
    add("H05", "heldout", "What is the total credit memo amount in USD across receivable ledger events?",
        amt(-sum(e["usd_c"] for i, e in events if e["type"] == "CREDIT_MEMO")),
        "SELECT SUM(signed_amount_usd) FROM ledger_erp.receivable_ledger_events WHERE event_type = 'CREDIT_MEMO'",
        "credits", tables=["ledger_erp.receivable_ledger_events"], values=["CREDIT_MEMO"], sign_insensitive=True)
    bc = [d["win_probability_pct"] for d in deals if d["pipeline_stage"] == "Best_Case"]
    add("H06", "heldout", "What is the average win probability percentage of deals in the Best Case stage?",
        round(sum(bc) / len(bc), 4) if bc else None,
        "SELECT AVG(win_probability_pct) FROM pipeline_crm.deal_opportunities WHERE pipeline_stage = 'Best_Case'", "crm",
        tables=["pipeline_crm.deal_opportunities"], values=["Best_Case"])
    by_ent: Dict[str, int] = defaultdict(int)
    for i in non_void:
        if i.invoice_date.year == 2025:
            by_ent[i.entity] += i.subtotal_usd
    add("H07", "heldout", "What is the total invoice subtotal in USD by legal entity for 2025, excluding VOID invoices?",
        {k: amt(v) for k, v in sorted(by_ent.items())},
        "SELECT legal_entity_code, SUM(subtotal_amount_usd) FROM ledger_erp.sales_invoices WHERE fiscal_period LIKE '2025-%' AND settlement_status <> 'VOID' GROUP BY legal_entity_code",
        "revenue", answer_type="grouped", tables=["ledger_erp.sales_invoices"], values=["VOID"])
    by_grp: Dict[str, int] = defaultdict(int)
    for i in non_void:
        if i.gid:  # type: ignore[attr-defined]
            by_grp[gen.group_by_id[i.gid]["conglomerate_name"]] += i.subtotal_usd  # type: ignore[attr-defined]
    top5 = sorted(by_grp.items(), key=lambda kv: -kv[1])[:5]
    add("H08", "heldout", "Which are the top 5 conglomerates by total invoice subtotal in USD, excluding VOID invoices?",
        {k: amt(v) for k, v in top5},
        """SELECT c.conglomerate_name, SUM(i.subtotal_amount_usd) s FROM ledger_erp.sales_invoices i
           JOIN ledger_erp.receivable_debtors d ON d.debtor_id = i.debtor_id
           JOIN master_ref.client_conglomerates c ON c.conglomerate_id = d.conglomerate_id
           WHERE i.settlement_status <> 'VOID' GROUP BY c.conglomerate_name ORDER BY s DESC LIMIT 5""",
        "cross_domain", answer_type="grouped",
        tables=["ledger_erp.sales_invoices", "ledger_erp.receivable_debtors", "master_ref.client_conglomerates"], values=["VOID"])
    add("H09", "heldout", "What is the total combined outlook in USD for as of period 2025-12?",
        amt(sum(int(round(r["combined_outlook_usd"] * 100)) for r in R["exec_reporting.horizon_revenue_outlook"] if r["as_of_period"] == "2025-12")),
        "SELECT SUM(combined_outlook_usd) FROM exec_reporting.horizon_revenue_outlook WHERE as_of_period = '2025-12'",
        "reporting", tables=["exec_reporting.horizon_revenue_outlook"], values=["2025-12"])
    add("H10", "heldout", "How many ingestion batches have a FAILED run status?",
        sum(1 for b in R["data_ops.ingestion_batches"] if b["run_status"] == "FAILED"),
        "SELECT COUNT(*) FROM data_ops.ingestion_batches WHERE run_status = 'FAILED'", "ops",
        tables=["data_ops.ingestion_batches"], values=["FAILED"])
    add("H11", "heldout", "What is the total annual quota in USD for sales reps with the Enterprise AE role?",
        amt(sum(int(round(r["annual_quota_usd"] * 100)) for r in R["pipeline_crm.quota_carriers"] if r["role_title"] == "Enterprise_AE")),
        "SELECT SUM(annual_quota_usd) FROM pipeline_crm.quota_carriers WHERE role_title = 'Enterprise_AE'", "crm",
        tables=["pipeline_crm.quota_carriers"], values=["Enterprise_AE"])
    nodes = {n["node_id"]: n["canonical_label"] for n in R["master_ref.taxonomy_nodes"]}
    pw: Dict[str, int] = defaultdict(int)
    for r in R["exec_reporting.monthly_revenue_posture"]:
        if r["snapshot_period"] == "2025-12":
            pw[nodes[r["region_node_id"]]] += int(round(r["open_pipeline_weighted_usd"] * 100))
    add("H12", "heldout", "What is the total open pipeline weighted USD by region for snapshot period 2025-12?",
        {k: amt(v) for k, v in sorted(pw.items())},
        """SELECT n.canonical_label, SUM(m.open_pipeline_weighted_usd) FROM exec_reporting.monthly_revenue_posture m
           JOIN master_ref.taxonomy_nodes n ON n.node_id = m.region_node_id
           WHERE m.snapshot_period = '2025-12' GROUP BY n.canonical_label""",
        "reporting", answer_type="grouped", tables=["exec_reporting.monthly_revenue_posture"], values=["2025-12"])
    add("H13", "heldout", "What was our revenue last year?", None, "-- ambiguous: invoiced vs recognized vs contracted",
        "ambiguity", behavior="flag_ambiguity")
    posted_debits = sum(p["_cents"] for p in R["ledger_erp.journal_postings"]
                        if p["_status"] == "POSTED" and p["_period"] == "2025-06" and p["_cents"] > 0)
    add("H14", "heldout", "What is the total debit amount in USD of journal postings for POSTED vouchers in fiscal period 2025-06?",
        amt(posted_debits),
        """SELECT SUM(p.debit_amount_usd) FROM ledger_erp.journal_postings p JOIN ledger_erp.journal_vouchers v
           ON v.voucher_id = p.voucher_id WHERE v.posting_status = 'POSTED' AND v.fiscal_period = '2025-06'""",
        "reconciliation", tables=["ledger_erp.journal_postings", "ledger_erp.journal_vouchers"], values=["POSTED", "2025-06"])

    # ---------------- held-out v2: written after the first held-out run, scored exactly once
    add("N01", "heldout2", "How many credit memo events were posted in fiscal period 2025-09?",
        sum(1 for i, e in events if e["type"] == "CREDIT_MEMO" and period_of(e["date"]) == "2025-09"),
        "SELECT COUNT(*) FROM ledger_erp.receivable_ledger_events WHERE event_type = 'CREDIT_MEMO' AND fiscal_period = '2025-09'",
        "credits", tables=["ledger_erp.receivable_ledger_events"], values=["CREDIT_MEMO", "2025-09"])
    add("N02", "heldout2", "What is the total tax amount in USD on invoices issued by legal entity AETH-UK in 2025?",
        amt(sum(i.tax_usd for i in inv if i.entity == "AETH-UK" and i.invoice_date.year == 2025)),
        "SELECT SUM(tax_amount_usd) FROM ledger_erp.sales_invoices WHERE legal_entity_code = 'AETH-UK' AND fiscal_period LIKE '2025-%'",
        "tax", tables=["ledger_erp.sales_invoices"], values=["AETH-UK"])
    exp_arr = [d["_arr_c"] for d in deals if d["deal_type"] == "Expansion"]
    add("N03", "heldout2", "What is the average deal ARR in USD for Expansion deals?",
        round(sum(exp_arr) / len(exp_arr) / 100, 4),
        "SELECT AVG(deal_arr_usd) FROM pipeline_crm.deal_opportunities WHERE deal_type = 'Expansion'", "crm",
        tables=["pipeline_crm.deal_opportunities"], values=["Expansion"])
    pl: Dict[str, int] = defaultdict(int)
    for r in R["exec_reporting.monthly_revenue_posture"]:
        if r["snapshot_period"] == "2025-12":
            pl[nodes[r["product_line_node_id"]]] += int(round(r["contracted_arr_usd"] * 100))
    add("N04", "heldout2", "What is the total contracted ARR in USD by product line for snapshot period 2025-12?",
        {k: amt(v) for k, v in sorted(pl.items())},
        """SELECT n.canonical_label, SUM(m.contracted_arr_usd) FROM exec_reporting.monthly_revenue_posture m
           JOIN master_ref.taxonomy_nodes n ON n.node_id = m.product_line_node_id
           WHERE m.snapshot_period = '2025-12' GROUP BY n.canonical_label""",
        "reporting", answer_type="grouped", tables=["exec_reporting.monthly_revenue_posture"], values=["2025-12"])
    add("N05", "heldout2", "How many conglomerates have a credit risk rating of WATCH?",
        sum(1 for g in R["master_ref.client_conglomerates"] if g["credit_risk_rating"] == "WATCH"),
        "SELECT COUNT(*) FROM master_ref.client_conglomerates WHERE credit_risk_rating = 'WATCH'", "identity",
        tables=["master_ref.client_conglomerates"], values=["WATCH"])
    terms = {d["debtor_id"]: d["payment_terms_code"] for d in R["ledger_erp.receivable_debtors"]}
    add("N06", "heldout2", "What is the total open balance in USD of invoices for debtors with payment terms NET60?",
        amt(sum(i.open_usd for i in inv if terms[i.debtor_id] == "NET60")),
        """SELECT SUM(i.open_balance_usd) FROM ledger_erp.sales_invoices i JOIN ledger_erp.receivable_debtors d
           ON d.debtor_id = i.debtor_id WHERE d.payment_terms_code = 'NET60'""",
        "cross_domain", tables=["ledger_erp.sales_invoices", "ledger_erp.receivable_debtors"], values=["NET60"])
    add("N07", "heldout2", "What is the total target amount in USD for scenario FY2026_BASELINE_PLAN?",
        amt(sum(int(round(t["target_amount_usd"] * 100)) for t in R["fpna_plan.financial_targets"]
                if t["plan_scenario"] == "FY2026_BASELINE_PLAN")),
        "SELECT SUM(target_amount_usd) FROM fpna_plan.financial_targets WHERE plan_scenario = 'FY2026_BASELINE_PLAN'",
        "budget", tables=["fpna_plan.financial_targets"], values=["FY2026_BASELINE_PLAN"])
    reps = {r["rep_id"]: r["rep_full_name"] for r in R["pipeline_crm.quota_carriers"]}
    by_rep: Dict[str, int] = defaultdict(int)
    for d in deals:
        by_rep[reps[d["owner_rep_id"]]] += int(round(d["weighted_arr_usd"] * 100))
    add("N08", "heldout2", "Who are the top 3 quota carriers by total weighted ARR in USD of their deals?",
        {k: amt(v) for k, v in sorted(by_rep.items(), key=lambda kv: -kv[1])[:3]},
        """SELECT r.rep_full_name, SUM(o.weighted_arr_usd) s FROM pipeline_crm.deal_opportunities o
           JOIN pipeline_crm.quota_carriers r ON r.rep_id = o.owner_rep_id GROUP BY r.rep_full_name ORDER BY s DESC LIMIT 3""",
        "cross_domain", answer_type="grouped",
        tables=["pipeline_crm.deal_opportunities", "pipeline_crm.quota_carriers"])
    add("N09", "heldout2", "How are we doing against plan this year?", None, "-- ambiguous: which metric, which plan, which period",
        "ambiguity", behavior="flag_ambiguity")
    prod_usd = sum(l["_usd_c"] for i in inv for l in i.lines if l["catalog_item_id"] == "PRD-003")
    add("N10", "heldout2", "What is the total line net amount in USD of invoice items for the offering Managed Cloud Hosting?",
        amt(prod_usd),
        """SELECT SUM(l.line_net_amount_usd) FROM ledger_erp.sales_invoice_items l JOIN ledger_erp.catalog_offerings c
           ON c.catalog_item_id = l.catalog_item_id WHERE c.offering_name = 'Managed Cloud Hosting'""",
        "cross_domain", tables=["ledger_erp.sales_invoice_items", "ledger_erp.catalog_offerings"], values=["Managed Cloud Hosting"])
    return q
