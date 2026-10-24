"""Deterministic spend-leakage detectors over the procurement data.

Each detector is a read-only query through the gateway plus explicit rules that separate leakage from legitimate
exceptions. Every case carries its root cause, a confidence score, the recoverable amount and the evidence rows.

  duplicate_invoice  same supplier and amount within 10 days, and the same invoice number (ignoring punctuation) or PO
  price_variance     invoiced unit price above the contract rate, after applying any agreed escalation
  missed_discount    paid after the early-payment window without the discount, unless the invoice was on hold
  overpayment        payments exceed the invoice amount
  unused_saas        seats without a login in 60 days, excluding seats assigned in the last 30 days (onboarding)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, List

Run = Callable[[str], List[Dict[str, Any]]]
TOLERANCE = 0.005  # price differences below 0.5% are rounding, not leakage


def _f(v: Any) -> float:
    return float(v or 0)


def _d(v: Any) -> date:
    """The gateway returns dates as ISO strings (JSON-safe)."""
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def duplicate_invoices(run: Run) -> List[Dict[str, Any]]:
    rows = run("""
        SELECT a.invoice_id AS original_id, b.invoice_id AS duplicate_id, b.supplier_id, s.supplier_name,
               a.invoice_number AS original_number, b.invoice_number AS duplicate_number, a.po_id AS original_po, b.po_id AS duplicate_po,
               a.invoice_date AS original_date, b.invoice_date AS duplicate_date, b.amount_total, b.status AS duplicate_status,
               (SELECT COALESCE(SUM(p.amount), 0) FROM procurement.supplier_payments p WHERE p.invoice_id = b.invoice_id) AS duplicate_paid,
               (SELECT COALESCE(SUM(p.amount), 0) FROM procurement.supplier_payments p WHERE p.invoice_id = a.invoice_id) AS original_paid
        FROM procurement.supplier_invoices a
        JOIN procurement.supplier_invoices b ON b.supplier_id = a.supplier_id AND b.invoice_id > a.invoice_id
             AND b.amount_total = a.amount_total AND ABS(b.invoice_date - a.invoice_date) <= 10
        JOIN procurement.suppliers s ON s.supplier_id = b.supplier_id
        WHERE regexp_replace(upper(a.invoice_number), '[^A-Z0-9]', '', 'g') = regexp_replace(upper(b.invoice_number), '[^A-Z0-9]', '', 'g')
           OR (a.po_id IS NOT NULL AND a.po_id = b.po_id)""")
    cases = []
    for r in rows:
        same_number = r["original_number"].replace("-", "").upper() == r["duplicate_number"].replace("-", "").upper()
        paid = _f(r["duplicate_paid"])
        cases.append({
            "type": "duplicate_invoice", "ref": r["duplicate_id"], "related": [r["original_id"]], "supplier_id": r["supplier_id"],
            "supplier_name": r["supplier_name"], "recoverable_usd": round(paid if paid else _f(r["amount_total"]), 2),
            "confidence": 0.95 if same_number else 0.82,
            "root_cause": (f"Invoice {r['duplicate_number']} repeats {r['original_number']} "
                           f"({'same number' if same_number else 'same purchase order ' + str(r['duplicate_po'])}, same amount "
                           f"${_f(r['amount_total']):,.2f}, {abs((_d(r['duplicate_date']) - _d(r['original_date'])).days)} days apart)"
                           + (" and both were paid." if paid else "; the copy is not paid yet.")),
            "action": "claw_back" if paid else "payment_hold",
            "evidence": [{"label": "Invoices", "rows": [
                {"invoice": r["original_id"], "number": r["original_number"], "date": str(r["original_date"]), "amount": _f(r["amount_total"]), "paid": _f(r["original_paid"])},
                {"invoice": r["duplicate_id"], "number": r["duplicate_number"], "date": str(r["duplicate_date"]), "amount": _f(r["amount_total"]), "paid": paid}]}],
        })
    return cases


def price_variance(run: Run) -> List[Dict[str, Any]]:
    rows = run("""
        SELECT l.line_id, l.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.invoice_date, l.item_code, l.quantity,
               l.unit_price AS invoiced_price, r.unit_price AS contract_price, c.contract_id, c.escalation_pct, c.escalation_effective_date
        FROM procurement.supplier_invoice_lines l
        JOIN procurement.supplier_invoices i ON i.invoice_id = l.invoice_id
        JOIN procurement.suppliers s ON s.supplier_id = i.supplier_id
        JOIN procurement.supplier_contracts c ON c.supplier_id = i.supplier_id AND i.invoice_date BETWEEN c.start_date AND c.end_date
        JOIN procurement.contract_rate_cards r ON r.contract_id = c.contract_id AND r.item_code = l.item_code
             AND i.invoice_date BETWEEN r.valid_from AND r.valid_to
        WHERE i.status <> 'CREDITED'""")
    by_invoice: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        allowed = _f(r["contract_price"])
        escalated = r["escalation_pct"] is not None and _d(r["invoice_date"]) >= _d(r["escalation_effective_date"])
        if escalated:
            allowed = round(allowed * (1 + _f(r["escalation_pct"]) / 100), 2)
        invoiced = _f(r["invoiced_price"])
        if invoiced <= allowed * (1 + TOLERANCE):
            continue
        over = round((invoiced - allowed) * _f(r["quantity"]), 2)
        case = by_invoice.setdefault(r["invoice_id"], {
            "type": "price_variance", "ref": r["invoice_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "recoverable_usd": 0.0, "confidence": 0.0, "action": "credit_note", "lines": [],
            "evidence": [{"label": "Invoiced vs contract price", "rows": []}]})
        case["related"].append(r["line_id"])
        case["recoverable_usd"] = round(case["recoverable_usd"] + over, 2)
        pct = invoiced / allowed - 1 if allowed else 0
        case["confidence"] = max(case["confidence"], 0.92 if pct > 0.05 else 0.75)
        case["lines"].append(f"{r['item_code']} at ${invoiced:,.2f} vs ${allowed:,.2f} ({pct:.0%} over, {_f(r['quantity']):g} units)")
        case["evidence"][0]["rows"].append({"line": r["line_id"], "item": r["item_code"], "quantity": _f(r["quantity"]), "invoiced_price": invoiced,
                                            "contract_price": _f(r["contract_price"]), "allowed_price": allowed,
                                            "escalation_applied": escalated, "overcharge": over})
    for c in by_invoice.values():
        c["root_cause"] = f"Invoice {c['ref']} charges above the contract rate card: " + "; ".join(c.pop("lines")) + "."
    return list(by_invoice.values())


def missed_discounts(run: Run) -> List[Dict[str, Any]]:
    rows = run("""
        SELECT i.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.invoice_date, i.amount_total, i.hold_reason,
               c.early_pay_discount_pct, c.early_pay_discount_days, MIN(p.payment_date) AS first_payment,
               BOOL_OR(p.discount_taken) AS discount_taken, SUM(p.amount) AS paid
        FROM procurement.supplier_invoices i
        JOIN procurement.suppliers s ON s.supplier_id = i.supplier_id
        JOIN procurement.supplier_contracts c ON c.supplier_id = i.supplier_id AND i.invoice_date BETWEEN c.start_date AND c.end_date
        JOIN procurement.supplier_payments p ON p.invoice_id = i.invoice_id
        WHERE c.early_pay_discount_pct IS NOT NULL
        GROUP BY i.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.invoice_date, i.amount_total, i.hold_reason,
                 c.early_pay_discount_pct, c.early_pay_discount_days""")
    cases = []
    for r in rows:
        deadline = _d(r["invoice_date"]) + timedelta(days=int(r["early_pay_discount_days"]))
        paid_on = _d(r["first_payment"])
        if r["discount_taken"] or paid_on <= deadline or r["hold_reason"]:
            continue  # discount used, paid in time, or the invoice was legitimately on hold
        lost = round(_f(r["amount_total"]) * _f(r["early_pay_discount_pct"]) / 100, 2)
        cases.append({
            "type": "missed_discount", "ref": r["invoice_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "recoverable_usd": lost, "confidence": 0.9, "action": "discount_recovery",
            "root_cause": (f"Contract offers {_f(r['early_pay_discount_pct']):g}% within {r['early_pay_discount_days']} days; invoice "
                           f"{r['invoice_number']} (${_f(r['amount_total']):,.2f}) was paid on {paid_on}, "
                           f"{(paid_on - deadline).days} days after the window closed on {deadline}."),
            "evidence": [{"label": "Terms and payment", "rows": [
                {"invoice": r["invoice_id"], "invoice_date": str(r["invoice_date"]), "discount_deadline": str(deadline),
                 "paid_on": str(paid_on), "paid": _f(r["paid"]), "discount_lost": lost}]}],
        })
    return cases


def overpayments(run: Run) -> List[Dict[str, Any]]:
    rows = run("""
        SELECT i.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.amount_total, SUM(p.amount) AS paid, COUNT(*) AS payments
        FROM procurement.supplier_invoices i
        JOIN procurement.suppliers s ON s.supplier_id = i.supplier_id
        JOIN procurement.supplier_payments p ON p.invoice_id = i.invoice_id
        GROUP BY i.invoice_id, i.supplier_id, s.supplier_name, i.invoice_number, i.amount_total
        HAVING SUM(p.amount) > i.amount_total + 0.01""")
    return [{
        "type": "overpayment", "ref": r["invoice_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
        "recoverable_usd": round(_f(r["paid"]) - _f(r["amount_total"]), 2), "confidence": 0.97, "action": "refund_request",
        "root_cause": (f"Invoice {r['invoice_number']} is for ${_f(r['amount_total']):,.2f} but ${_f(r['paid']):,.2f} was paid "
                       f"in {r['payments']} payment{'s' if r['payments'] != 1 else ''}."),
        "evidence": [{"label": "Invoice and payments", "rows": [{"invoice": r["invoice_id"], "invoiced": _f(r["amount_total"]),
                                                                 "paid": _f(r["paid"]), "payments": int(r["payments"])}]}],
    } for r in rows]


def unused_saas(run: Run) -> List[Dict[str, Any]]:
    latest = run("SELECT MAX(last_login_date) AS d FROM procurement.saas_seat_assignments")[0]["d"]
    as_of = _d(latest) if latest else date.today()
    active_since, onboarding_since = as_of - timedelta(days=60), as_of - timedelta(days=30)
    rows = run(f"""
        SELECT s.subscription_id, s.supplier_id, v.supplier_name, s.product_name, s.seats_purchased, s.price_per_seat_month, s.renewal_date,
               COUNT(*) FILTER (WHERE a.last_login_date >= DATE '{active_since}') AS active_seats,
               COUNT(*) FILTER (WHERE a.assigned_date >= DATE '{onboarding_since}' AND a.last_login_date IS NULL) AS onboarding_seats
        FROM procurement.saas_subscriptions s
        JOIN procurement.suppliers v ON v.supplier_id = s.supplier_id
        LEFT JOIN procurement.saas_seat_assignments a ON a.subscription_id = s.subscription_id
        GROUP BY s.subscription_id, s.supplier_id, v.supplier_name, s.product_name, s.seats_purchased, s.price_per_seat_month, s.renewal_date""")
    cases = []
    for r in rows:
        unused = int(r["seats_purchased"]) - int(r["active_seats"]) - int(r["onboarding_seats"])
        if unused <= 0:
            continue
        annual = round(unused * _f(r["price_per_seat_month"]) * 12, 2)
        cases.append({
            "type": "unused_saas", "ref": r["subscription_id"], "related": [], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "recoverable_usd": annual, "confidence": 0.85 if unused >= 3 else 0.7, "action": "seat_reduction",
            "root_cause": (f"{r['product_name']}: {unused} of {r['seats_purchased']} seats have no login since {active_since} "
                           f"(${_f(r['price_per_seat_month']):,.2f} per seat per month; renews {r['renewal_date']})."
                           + (f" {r['onboarding_seats']} newly assigned seats were not counted." if r["onboarding_seats"] else "")),
            "evidence": [{"label": "Seat usage", "rows": [{"subscription": r["subscription_id"], "product": r["product_name"],
                                                          "seats": int(r["seats_purchased"]), "active_60d": int(r["active_seats"]),
                                                          "onboarding": int(r["onboarding_seats"]), "unused": unused}]}],
        })
    return cases


DETECTORS = {"duplicate_invoice": duplicate_invoices, "price_variance": price_variance, "missed_discount": missed_discounts,
             "overpayment": overpayments, "unused_saas": unused_saas}
