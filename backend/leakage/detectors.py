"""Deterministic spend-leakage detectors over a canonical procurement ledger (schema ``procurement``).

The same SQL runs on PostgreSQL (the warehouse) and DuckDB (uploaded files mapped to the same tables), always read-only.
Filtering happens in SQL so only candidate rows come back; explicit rules then separate leakage from legitimate
exceptions. Every case carries its root cause, a confidence score, the recoverable amount (USD) and the evidence rows.

  duplicate_invoice  same vendor (tax id or normalised name, so duplicate vendor records count as one) and same amount,
                     with the same invoice number after normalisation (case, punctuation, leading zeros) within 120 days,
                     or the same purchase order within 10 days when the PO is invoiced beyond its value. Credited or
                     cancelled copies, credit notes (amount <= 0) and copies whose payment was refunded are not leakage.
                     A triplicate gives one case per extra copy, each against the first invoice.
  price_variance     invoiced unit price (converted to USD) above the rate card of the newest contract and price card
                     covering the invoice date, after any agreed escalation; differences below 0.5% are rounding
  missed_discount    first payment after the early-payment window without the discount (newest covering contract),
                     unless the invoice was on hold
  overpayment        net payments (refunds deducted) exceed the invoice amount
  unused_saas        seats without a login in 60 days, excluding seats assigned in the last 30 days (onboarding);
                     unassigned seats count as unused
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, List

Run = Callable[[str], List[Dict[str, Any]]]
TOLERANCE = 0.005  # price differences below 0.5% are rounding, not leakage
DUPLICATE_WINDOW_DAYS = 120  # same invoice number: a resubmitted invoice can arrive months later
PO_WINDOW_DAYS = 10
CLOSED = "('CREDITED', 'CANCELLED', 'VOID', 'REVERSED')"
INACTIVE_CONTRACT = "('SUPERSEDED', 'TERMINATED', 'EXPIRED', 'CANCELLED')"
NORM_NUMBER = r"regexp_replace(regexp_replace(upper({col}), '[^A-Z0-9]', '', 'g'), '(^|[A-Z])0+([0-9])', '\1\2', 'g')"
LEGAL = "(LTD|LIMITED|LLC|LLP|INC|PVT|PRIVATE|CORP|CORPORATION|CO|COMPANY|GMBH|PLC|AG|BV|SA)"


def _f(v: Any) -> float:
    return float(v or 0)


def _d(v: Any) -> date:
    """The gateway returns dates as ISO strings (JSON-safe)."""
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def normalize_vendor(name: Any) -> str:
    import re

    s = " " + re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper()) + " "
    for _ in range(2):
        s = re.sub(rf" {LEGAL} ", " ", s)
    return s.replace(" ", "")


def vendor_groups(run: Run) -> Dict[str, str]:
    """supplier_id -> group id; records sharing a tax id or a normalised name are one vendor."""
    rows = run("SELECT supplier_id, supplier_name, tax_id FROM procurement.suppliers")
    parent = {r["supplier_id"]: r["supplier_id"] for r in rows}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen: Dict[str, str] = {}
    for r in rows:
        for key in (f"tax:{str(r['tax_id']).strip().upper()}" if r.get("tax_id") and str(r["tax_id"]).strip() else None,
                    f"name:{normalize_vendor(r['supplier_name'])}"):
            if not key:
                continue
            if key in seen:
                parent[find(r["supplier_id"])] = find(seen[key])
            else:
                seen[key] = r["supplier_id"]
    return {sid: find(sid) for sid in parent}


def duplicate_invoices(run: Run) -> List[Dict[str, Any]]:
    groups = vendor_groups(run)
    names = {r["supplier_id"]: r["supplier_name"] for r in run("SELECT supplier_id, supplier_name FROM procurement.suppliers")}
    rows = run(f"""
        WITH inv AS (
            SELECT i.invoice_id, i.supplier_id, i.po_id, i.invoice_number, i.invoice_date, i.amount_total, upper(i.status) AS status,
                   COALESCE(i.fx_rate, 1) AS fx_rate, {NORM_NUMBER.format(col='i.invoice_number')} AS nnum,
                   (SELECT COALESCE(SUM(p.amount), 0) FROM procurement.supplier_payments p WHERE p.invoice_id = i.invoice_id) AS paid
            FROM procurement.supplier_invoices i
            WHERE i.amount_total > 0 AND upper(i.status) NOT IN {CLOSED}),
        po AS (SELECT po_id, SUM(amount_total) AS invoiced FROM inv WHERE po_id IS NOT NULL GROUP BY po_id)
        SELECT a.invoice_id AS original_id, b.invoice_id AS duplicate_id, a.supplier_id AS original_supplier, b.supplier_id,
               a.invoice_number AS original_number, b.invoice_number AS duplicate_number, a.po_id AS original_po, b.po_id AS duplicate_po,
               a.invoice_date AS original_date, b.invoice_date AS duplicate_date, b.amount_total, b.status AS duplicate_status, b.fx_rate,
               b.paid AS duplicate_paid, a.paid AS original_paid, a.nnum = b.nnum AS same_number,
               pp.invoiced AS po_invoiced, o.amount_total AS po_amount
        FROM inv a
        JOIN inv b ON b.amount_total = a.amount_total
             AND (b.invoice_date > a.invoice_date OR (b.invoice_date = a.invoice_date AND b.invoice_id > a.invoice_id))
        LEFT JOIN po pp ON pp.po_id = b.po_id
        LEFT JOIN procurement.purchase_orders o ON o.po_id = b.po_id
        WHERE (a.nnum = b.nnum AND b.invoice_date - a.invoice_date <= {DUPLICATE_WINDOW_DAYS})
           OR (a.po_id = b.po_id AND b.invoice_date - a.invoice_date <= {PO_WINDOW_DAYS} AND pp.invoiced > o.amount_total * 1.02)""")
    best: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if groups.get(r["original_supplier"]) != groups.get(r["supplier_id"]):
            continue  # the same invoice number at two different vendors is a coincidence, not a duplicate
        cur = best.get(r["duplicate_id"])
        if cur is None or (str(r["original_date"]), r["original_id"]) < (str(cur["original_date"]), cur["original_id"]):
            best[r["duplicate_id"]] = r  # a triplicate: every extra copy is compared with the first invoice
    cases = []
    for r in best.values():
        paid, amount, fx = _f(r["duplicate_paid"]), _f(r["amount_total"]), _f(r["fx_rate"]) or 1.0
        unpaid = r["duplicate_status"] not in ("PAID", "CLOSED", "CLEARED")
        if paid <= 0.01 and not unpaid:
            continue  # paid and refunded (or settled at zero): nothing left to recover
        same_number = bool(r["same_number"])
        days = abs((_d(r["duplicate_date"]) - _d(r["original_date"])).days)
        other_vendor = r["original_supplier"] != r["supplier_id"]
        reason = ("same number" if same_number else f"same purchase order {r['duplicate_po']}, invoiced beyond its value")
        cases.append({
            "type": "duplicate_invoice", "ref": r["duplicate_id"], "related": [r["original_id"]], "supplier_id": r["supplier_id"],
            "supplier_name": names.get(r["supplier_id"]),
            "recoverable_usd": round(min(paid, amount) * fx if paid > 0.01 else amount * fx, 2),
            "confidence": (0.95 if days <= 30 else 0.88) if same_number else 0.82,
            "root_cause": (f"Invoice {r['duplicate_number']} ({r['duplicate_id']}) repeats {r['original_number']} ({r['original_id']}): "
                           f"{reason}, same amount {amount:,.2f}, {days} days apart"
                           + (f", booked under a second vendor record ({names.get(r['original_supplier'])} / {names.get(r['supplier_id'])})"
                              if other_vendor else "")
                           + (" and both were paid." if paid > 0.01 else "; the copy is not paid yet.")),
            "action": "claw_back" if paid > 0.01 else "payment_hold",
            "evidence": [{"label": "Invoices", "rows": [
                {"invoice": r["original_id"], "number": r["original_number"], "vendor": names.get(r["original_supplier"]),
                 "date": str(r["original_date"]), "amount": amount, "paid": _f(r["original_paid"])},
                {"invoice": r["duplicate_id"], "number": r["duplicate_number"], "vendor": names.get(r["supplier_id"]),
                 "date": str(r["duplicate_date"]), "amount": amount, "paid": paid}]}],
        })
    return cases


def _contract_rank(alias: str = "c") -> str:
    return f"CASE WHEN upper({alias}.status) IN {INACTIVE_CONTRACT} THEN 1 ELSE 0 END, {alias}.start_date DESC"


def price_variance(run: Run) -> List[Dict[str, Any]]:
    rows = run(f"""
        SELECT * FROM (
            SELECT l.line_id, l.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.invoice_date, i.currency,
                   COALESCE(i.fx_rate, 1) AS fx_rate, l.item_code, l.quantity, l.unit_price AS invoiced_price,
                   r.unit_price AS contract_price, c.contract_id, c.escalation_pct, c.escalation_effective_date,
                   ROW_NUMBER() OVER (PARTITION BY l.line_id ORDER BY {_contract_rank()}, r.valid_from DESC) AS rk
            FROM procurement.supplier_invoice_lines l
            JOIN procurement.supplier_invoices i ON i.invoice_id = l.invoice_id
            JOIN procurement.suppliers s ON s.supplier_id = i.supplier_id
            JOIN procurement.supplier_contracts c ON c.supplier_id = i.supplier_id AND i.invoice_date BETWEEN c.start_date AND c.end_date
            JOIN procurement.contract_rate_cards r ON r.contract_id = c.contract_id AND upper(r.item_code) = upper(l.item_code)
                 AND i.invoice_date BETWEEN r.valid_from AND r.valid_to
            WHERE upper(i.status) NOT IN {CLOSED} AND i.amount_total > 0) x
        WHERE rk = 1 AND invoiced_price * fx_rate > contract_price * (1 + {TOLERANCE})
              * (1 + CASE WHEN escalation_pct IS NOT NULL AND invoice_date >= escalation_effective_date THEN escalation_pct / 100.0 ELSE 0 END)""")
    by_invoice: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        allowed = _f(r["contract_price"])
        escalated = r["escalation_pct"] is not None and _d(r["invoice_date"]) >= _d(r["escalation_effective_date"])
        if escalated:
            allowed = round(allowed * (1 + _f(r["escalation_pct"]) / 100), 2)
        fx = _f(r["fx_rate"]) or 1.0
        invoiced = round(_f(r["invoiced_price"]) * fx, 4)
        if invoiced <= allowed * (1 + TOLERANCE):
            continue
        over = round((invoiced - allowed) * _f(r["quantity"]), 2)
        case = by_invoice.setdefault(r["invoice_id"], {
            "type": "price_variance", "ref": r["invoice_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "recoverable_usd": 0.0, "confidence": 0.0, "action": "credit_note", "lines": [],
            "evidence": [{"label": "Invoiced vs contract price (USD)", "rows": []}]})
        case["related"].append(r["line_id"])
        case["recoverable_usd"] = round(case["recoverable_usd"] + over, 2)
        pct = invoiced / allowed - 1 if allowed else 0
        case["confidence"] = max(case["confidence"], 0.92 if pct > 0.05 else 0.75)
        case["lines"].append(f"{r['item_code']} at ${invoiced:,.2f} vs ${allowed:,.2f} ({pct:.1%} over, {_f(r['quantity']):g} units"
                             + (f", contract {r['contract_id']}" if r.get("contract_id") else "") + ")")
        case["evidence"][0]["rows"].append({"line": r["line_id"], "item": r["item_code"], "quantity": _f(r["quantity"]), "invoiced_price": invoiced,
                                            "contract": r["contract_id"], "contract_price": _f(r["contract_price"]), "allowed_price": allowed,
                                            "escalation_applied": escalated, "overcharge": over})
    for c in by_invoice.values():
        c["root_cause"] = f"Invoice {c['ref']} charges above the contract rate card: " + "; ".join(c.pop("lines")) + "."
    return list(by_invoice.values())


def missed_discounts(run: Run) -> List[Dict[str, Any]]:
    rows = run(f"""
        SELECT x.*, p.first_payment, p.discount_taken, p.paid FROM (
            SELECT i.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.invoice_date, i.amount_total, COALESCE(i.fx_rate, 1) AS fx_rate,
                   c.contract_id, c.early_pay_discount_pct, c.early_pay_discount_days,
                   ROW_NUMBER() OVER (PARTITION BY i.invoice_id ORDER BY {_contract_rank()}) AS rk
            FROM procurement.supplier_invoices i
            JOIN procurement.suppliers s ON s.supplier_id = i.supplier_id
            JOIN procurement.supplier_contracts c ON c.supplier_id = i.supplier_id AND i.invoice_date BETWEEN c.start_date AND c.end_date
            WHERE i.amount_total > 0 AND upper(i.status) NOT IN {CLOSED} AND COALESCE(TRIM(i.hold_reason), '') = '') x
        JOIN (SELECT invoice_id, MIN(payment_date) AS first_payment, BOOL_OR(discount_taken) AS discount_taken, SUM(amount) AS paid
              FROM procurement.supplier_payments WHERE amount > 0 GROUP BY invoice_id) p ON p.invoice_id = x.invoice_id
        WHERE x.rk = 1 AND x.early_pay_discount_pct IS NOT NULL AND NOT p.discount_taken
              AND p.first_payment - x.invoice_date > x.early_pay_discount_days""")
    cases = []
    for r in rows:
        deadline = _d(r["invoice_date"]) + timedelta(days=int(r["early_pay_discount_days"]))
        paid_on = _d(r["first_payment"])
        fx = _f(r["fx_rate"]) or 1.0
        lost = round(_f(r["amount_total"]) * fx * _f(r["early_pay_discount_pct"]) / 100, 2)
        cases.append({
            "type": "missed_discount", "ref": r["invoice_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "recoverable_usd": lost, "confidence": 0.9, "action": "discount_recovery",
            "root_cause": (f"Contract {r['contract_id']} offers {_f(r['early_pay_discount_pct']):g}% within {r['early_pay_discount_days']} days; "
                           f"invoice {r['invoice_number']} ({_f(r['amount_total']):,.2f}) was first paid on {paid_on}, "
                           f"{(paid_on - deadline).days} days after the window closed on {deadline}."),
            "evidence": [{"label": "Terms and payment", "rows": [
                {"invoice": r["invoice_id"], "invoice_date": str(r["invoice_date"]), "discount_deadline": str(deadline),
                 "paid_on": str(paid_on), "paid": _f(r["paid"]), "discount_lost_usd": lost}]}],
        })
    return cases


def overpayments(run: Run) -> List[Dict[str, Any]]:
    rows = run(f"""
        SELECT i.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.amount_total, COALESCE(i.fx_rate, 1) AS fx_rate,
               SUM(p.amount) AS paid, COUNT(*) AS payments, SUM(CASE WHEN p.amount < 0 THEN 1 ELSE 0 END) AS refunds
        FROM procurement.supplier_invoices i
        JOIN procurement.suppliers s ON s.supplier_id = i.supplier_id
        JOIN procurement.supplier_payments p ON p.invoice_id = i.invoice_id
        WHERE i.amount_total > 0 AND upper(i.status) NOT IN {CLOSED}
        GROUP BY i.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.amount_total, i.fx_rate
        HAVING SUM(p.amount) > i.amount_total + 0.01""")
    out = []
    for r in rows:
        fx = _f(r["fx_rate"]) or 1.0
        n = int(r["payments"])
        out.append({
            "type": "overpayment", "ref": r["invoice_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "recoverable_usd": round((_f(r["paid"]) - _f(r["amount_total"])) * fx, 2), "confidence": 0.97, "action": "refund_request",
            "root_cause": (f"Invoice {r['invoice_number']} ({r['invoice_id']}) is for {_f(r['amount_total']):,.2f} but {_f(r['paid']):,.2f} "
                           f"was paid net in {n} payment{'s' if n != 1 else ''}"
                           + (f" (after {int(r['refunds'])} refund{'s' if int(r['refunds']) != 1 else ''})" if int(r["refunds"] or 0) else "") + "."),
            "evidence": [{"label": "Invoice and payments", "rows": [{"invoice": r["invoice_id"], "invoiced": _f(r["amount_total"]),
                                                                     "paid_net": _f(r["paid"]), "payments": n}]}],
        })
    return out


def unused_saas(run: Run) -> List[Dict[str, Any]]:
    latest = run("SELECT MAX(last_login_date) AS d FROM procurement.saas_seat_assignments")
    if not latest or latest[0]["d"] is None:
        subs = run("SELECT COUNT(*) AS n FROM procurement.saas_subscriptions")
        if not subs or not subs[0]["n"]:
            return []
    as_of = _d(latest[0]["d"]) if latest and latest[0]["d"] else date.today()
    active_since, onboarding_since = as_of - timedelta(days=60), as_of - timedelta(days=30)
    rows = run(f"""
        SELECT s.subscription_id, s.supplier_id, v.supplier_name, s.product_name, s.seats_purchased, s.price_per_seat_month, s.renewal_date,
               COUNT(a.subscription_id) AS assigned,
               COUNT(*) FILTER (WHERE a.last_login_date >= DATE '{active_since}') AS active_seats,
               COUNT(*) FILTER (WHERE a.assigned_date >= DATE '{onboarding_since}' AND a.last_login_date IS NULL) AS onboarding_seats
        FROM procurement.saas_subscriptions s
        LEFT JOIN procurement.suppliers v ON v.supplier_id = s.supplier_id
        LEFT JOIN procurement.saas_seat_assignments a ON a.subscription_id = s.subscription_id
        GROUP BY s.subscription_id, s.supplier_id, v.supplier_name, s.product_name, s.seats_purchased, s.price_per_seat_month, s.renewal_date""")
    cases = []
    for r in rows:
        unused = int(r["seats_purchased"]) - int(r["active_seats"]) - int(r["onboarding_seats"])
        if unused <= 0:
            continue
        unassigned = max(0, int(r["seats_purchased"]) - int(r["assigned"]))
        annual = round(unused * _f(r["price_per_seat_month"]) * 12, 2)
        cases.append({
            "type": "unused_saas", "ref": r["subscription_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "recoverable_usd": annual, "confidence": 0.85 if unused >= 3 else 0.7, "action": "seat_reduction",
            "root_cause": (f"{r['product_name']}: {unused} of {r['seats_purchased']} seats have no login since {active_since}"
                           + (f" ({unassigned} never assigned)" if unassigned else "")
                           + f" (${_f(r['price_per_seat_month']):,.2f} per seat per month; renews {r['renewal_date']})."
                           + (f" {r['onboarding_seats']} newly assigned seats were not counted." if r["onboarding_seats"] else "")),
            "evidence": [{"label": "Seat usage", "rows": [{"subscription": r["subscription_id"], "product": r["product_name"],
                                                          "seats": int(r["seats_purchased"]), "assigned": int(r["assigned"]),
                                                          "active_60d": int(r["active_seats"]), "onboarding": int(r["onboarding_seats"]),
                                                          "unused": unused}]}],
        })
    return cases


DETECTORS = {"duplicate_invoice": duplicate_invoices, "price_variance": price_variance, "missed_discount": missed_discounts,
             "overpayment": overpayments, "unused_saas": unused_saas}
