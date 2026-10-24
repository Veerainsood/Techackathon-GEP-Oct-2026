"""Upload test files: a manual-approval file and a hard 14-file accounts-payable audit pack for leakage hunting.

The audit pack is the procurement data (own seed) plus injected hard cases, written the way different systems export it:
two ERP invoice exports with different layouts, scanned (OCR) invoices, a bank statement with free-text references and
refunds, credit notes, a price list with an overlapping renewal, a superseded contract, an INR supplier with FX rates,
licence and user-activity exports, and an unrelated journal extract. Some cells are blank on purpose (currency, line
totals, discount flags, statuses, account statuses) so the data-prep agent has gaps to fill; the answer key does not
depend on them. The answer key (seeded leakage and legitimate look-alikes, with the ids as they appear in the files) is
written next to the pack folder, never inside it.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from backend.synthetic.procurement import ProcurementGenerator, r2

SEED = 7
INR_PER_USD = 83.2  # fx file: USD per INR = 1 / 83.2


def build_pack() -> Dict[str, Any]:
    g = ProcurementGenerator(seed=SEED)
    d = g.generate()
    rng = random.Random(SEED + 1)
    key: List[Dict[str, Any]] = list(d.answer_key)
    exc: List[Dict[str, Any]] = list(d.exceptions)
    inv_rows = d.rows["supplier_invoices"]
    lines_of: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for l in d.rows["supplier_invoice_lines"]:
        lines_of[l["invoice_id"]].append(l)
    touched = {k["ref"] for k in key} | {r for k in key for r in k.get("related", [])} | {e["ref"] for e in exc}
    sup = {s["supplier_id"]: s for s in g.suppliers}
    ocr: set = set()

    def plain(s: Dict[str, Any]) -> bool:
        c = g.contracts[s["supplier_id"]]
        return not c["early_pay_discount_pct"] and not c["escalation_pct"]

    def paid_invoices(sid: Optional[str] = None, after: Optional[date] = None) -> List[Dict[str, Any]]:
        paid = {p["invoice_id"] for p in d.rows["supplier_payments"]}
        return [i for i in inv_rows if i["status"] == "PAID" and i["invoice_id"] in paid and i["invoice_id"] not in touched
                and i["po_id"] and (sid is None or i["supplier_id"] == sid) and (after is None or i["invoice_date"] >= after)]

    plain_sups = [s for s in g.suppliers if plain(s) and s["category"] != "SaaS"]
    # suppliers reserved for the price scenarios, so no other case interferes with them
    p2 = next(s for s in plain_sups if len(paid_invoices(s["supplier_id"], date(2025, 10, 1))) >= 2
              and not any(i["invoice_id"] in touched for i in inv_rows if i["supplier_id"] == s["supplier_id"] and i["invoice_date"] >= date(2025, 10, 1)))
    p1 = next(s for s in plain_sups if s is not p2)
    dup_sups = [s for s in plain_sups if s not in (p1, p2)]

    def copy_of(orig: Dict[str, Any], days: int, number: Optional[str] = None, supplier: Optional[Dict[str, Any]] = None,
                pay: bool = True, po: Optional[str] = None) -> Dict[str, Any]:
        s = supplier or sup[orig["supplier_id"]]
        lines = [(l["item_code"], l["quantity"], l["unit_price"]) for l in lines_of[orig["invoice_id"]]]
        c = g._invoice(s, po, orig["invoice_date"] + timedelta(days=days), lines, number=number or orig["invoice_number"])
        c["amount_total"] = orig["amount_total"]
        for l in d.rows["supplier_invoice_lines"]:
            if l["invoice_id"] == c["invoice_id"]:
                lines_of[c["invoice_id"]].append(l)
        if pay:
            g._pay(c, c["invoice_date"] + timedelta(days=rng.randint(10, 25)))
        else:
            c["status"] = "APPROVED"
        touched.update({orig["invoice_id"], c["invoice_id"]})
        return c

    def seed_dup(orig: Dict[str, Any], c: Dict[str, Any], note: str) -> None:
        paid = sum(p["amount"] for p in d.rows["supplier_payments"] if p["invoice_id"] == c["invoice_id"])
        key.append({"type": "duplicate_invoice", "ref": c["invoice_id"], "related": [orig["invoice_id"]], "supplier_id": c["supplier_id"],
                    "recoverable_usd": r2(paid if paid else c["amount_total"]), "note": note})

    pool = [i for s in dup_sups for i in paid_invoices(s["supplier_id"])]
    rng.shuffle(pool)
    take = iter(pool)

    # H1 the same vendor entered twice: a second vendor record with the same tax id, invoice booked under it again
    contoso = next(s for s in g.suppliers if s["supplier_name"] == "Contoso Logistics")
    twin = g._add("suppliers", dict(supplier_id=g._id("SUP", 3), supplier_name="CONTOSO LOGISTICS LTD.", tax_id=contoso["tax_id"],
                                    conglomerate_id=None, category="Logistics", region_code="AMER", country_iso="US",
                                    payment_terms_code=contoso["payment_terms_code"], status="ACTIVE"))
    g.suppliers.append(twin)
    sup[twin["supplier_id"]] = twin
    g.contracts[twin["supplier_id"]] = g.contracts[contoso["supplier_id"]]  # due dates only; the twin has no contract row
    orig = paid_invoices(contoso["supplier_id"])[0]
    c = copy_of(orig, 20, supplier=twin)
    seed_dup(orig, c, "same invoice booked under a duplicate vendor record (same tax id)")

    # H2 scanned copies of paid invoices: OCR'd number (spaces, leading zero), vendor name only, paid again
    for _ in range(3):
        orig = next(take)
        num = orig["invoice_number"].replace("-", " 0", 1)
        c = copy_of(orig, rng.randint(3, 8), number=num)
        ocr.add(c["invoice_id"])
        seed_dup(orig, c, "scanned copy of an invoice already in the ERP, paid twice")

    # H3 a triplicate: entered in the ERP again and scanned as well
    orig = next(take)
    for k, days in enumerate((2, 5)):
        c = copy_of(orig, days)
        if k == 1:
            ocr.add(c["invoice_id"])
        seed_dup(orig, c, "third copy of the same invoice" if k else "second copy of the same invoice")

    # H4 a resubmitted invoice six weeks later
    orig = next(take)
    seed_dup(orig, copy_of(orig, 45), "supplier resent the invoice 45 days later; it was paid again")

    # H5 a copy caught before payment (sits in the payment run): hold it
    orig = next(take)
    seed_dup(orig, copy_of(orig, 4, pay=False), "copy not paid yet: hold the payment")

    # L1 copy reversed by a full credit note (not leakage)
    credit_notes: List[Dict[str, Any]] = []
    orig = next(take)
    c = copy_of(orig, 3, pay=False)
    credit_notes.append({"invoice_id": c["invoice_id"], "supplier_id": c["supplier_id"], "amount": c["amount_total"],
                         "date": c["invoice_date"] + timedelta(days=6)})
    exc.append({"type": "duplicate_invoice", "ref": c["invoice_id"], "why": "duplicate reversed by a credit note"})

    # L2 copy paid and refunded by the supplier (net zero: not leakage)
    orig = next(take)
    c = copy_of(orig, 3)
    g._pay(c, c["invoice_date"] + timedelta(days=40), -c["amount_total"])
    exc.append({"type": "duplicate_invoice", "ref": c["invoice_id"], "why": "duplicate paid, then refunded by the supplier"})

    # L3 weekly service under a blanket purchase order: same amount, same PO, within the PO value
    s = dup_sups[0]
    code = next(iter(g.rates[s["supplier_id"]]))
    price = g.allowed_price(s["supplier_id"], code, date(2025, 8, 4))
    weekly = [(code, 1.0, price)]
    po = g._add("purchase_orders", dict(po_id=g._id("PO", 5), supplier_id=s["supplier_id"], contract_id=g.contracts[s["supplier_id"]]["contract_id"],
                                        po_date=date(2025, 8, 1), currency="USD", amount_total=r2(4 * price), status="OPEN"))
    for k in range(4):
        inv = g._invoice(s, po["po_id"], date(2025, 8, 4) + timedelta(days=7 * k), weekly)
        g._pay(inv, inv["invoice_date"] + timedelta(days=20))
        touched.add(inv["invoice_id"])
        if k:
            exc.append({"type": "duplicate_invoice", "ref": inv["invoice_id"], "why": "weekly service on a blanket PO, within its value"})

    # L4 two credit memos for the same amount on one PO (refunds to us, not duplicate invoices)
    s = dup_sups[1]
    po_id = next(i["po_id"] for i in inv_rows if i["supplier_id"] == s["supplier_id"] and i["po_id"])
    for k in range(2):
        cm = g._invoice(s, po_id, date(2025, 9, 10) + timedelta(days=3 * k), [], number=f"CM-{9100 + k}")
        cm.update(amount_total=-850.0, status="CREDIT MEMO")
        if k:
            exc.append({"type": "duplicate_invoice", "ref": cm["invoice_id"], "why": "second credit memo on the PO, not an invoice"})

    # L5 two different vendors happen to use the same invoice number and amount
    for k, s in enumerate(dup_sups[2:4]):
        inv = g._invoice(s, None, date(2025, 5, 12) + timedelta(days=2 * k), [], number="INV-1001")  # header-only invoices
        inv["amount_total"] = 1200.0
        g._pay(inv, inv["due_date"] - timedelta(days=1))
        touched.add(inv["invoice_id"])
        if k:
            exc.append({"type": "duplicate_invoice", "ref": inv["invoice_id"], "why": "same number and amount at a different vendor"})

    # P1 price renewal on 1 Sep with a one-month overlap: September invoices at the new price are fine
    sid1 = p1["supplier_id"]
    code1 = next(iter(g.rates[sid1]))
    old_card = next(r for r in d.rows["contract_rate_cards"] if r["contract_id"] == g.contracts[sid1]["contract_id"] and r["item_code"] == code1)
    new_price = r2(old_card["unit_price"] * 1.08)
    old_card["valid_to"] = date(2025, 9, 30)
    g._add("contract_rate_cards", dict(rate_id=g._id("RATE", 5), contract_id=old_card["contract_id"], item_code=code1,
                                       item_description=old_card["item_description"], unit=old_card["unit"], unit_price=new_price,
                                       valid_from=date(2025, 9, 1), valid_to=g.contracts[sid1]["end_date"]))
    for k in range(2):
        inv = g._invoice(p1, None, date(2025, 9, 8 + 7 * k), [(code1, 12.0, new_price)])
        g._pay(inv, inv["due_date"] - timedelta(days=2))
        exc.append({"type": "price_variance", "ref": inv["invoice_id"], "why": "new price card (overlapping the old one) applies"})
    over = r2(new_price * 1.12)
    inv = g._invoice(p1, None, date(2025, 10, 14), [(code1, 15.0, over)])
    g._pay(inv, inv["due_date"] - timedelta(days=2))
    key.append({"type": "price_variance", "ref": inv["invoice_id"], "related": [], "supplier_id": sid1,
                "recoverable_usd": r2((over - new_price) * 15.0), "note": "12% above the renewed price card"})

    # P2 renegotiated contract from 1 Oct (8% lower); the old one is marked superseded but its end date was never updated
    sid2 = p2["supplier_id"]
    old = g.contracts[sid2]
    old["status"] = "SUPERSEDED"
    new_c = g._add("supplier_contracts", dict(contract_id=g._id("SCT", 4), supplier_id=sid2, start_date=date(2025, 10, 1),
                                              end_date=old["end_date"], currency="USD", payment_terms_days=old["payment_terms_days"],
                                              early_pay_discount_pct=None, early_pay_discount_days=None, escalation_pct=None,
                                              escalation_effective_date=None, status="ACTIVE"))
    new_rates = {}
    for r in [r for r in d.rows["contract_rate_cards"] if r["contract_id"] == old["contract_id"]]:
        new_rates[r["item_code"]] = r2(r["unit_price"] * 0.92)
        g._add("contract_rate_cards", dict(rate_id=g._id("RATE", 5), contract_id=new_c["contract_id"], item_code=r["item_code"],
                                           item_description=r["item_description"], unit=r["unit"], unit_price=new_rates[r["item_code"]],
                                           valid_from=new_c["start_date"], valid_to=new_c["end_date"]))
    for inv in [i for i in inv_rows if i["supplier_id"] == sid2 and i["invoice_date"] >= date(2025, 10, 1)]:
        rec = sum(round((l["unit_price"] - new_rates[l["item_code"]]) * l["quantity"], 2) for l in lines_of[inv["invoice_id"]])
        key.append({"type": "price_variance", "ref": inv["invoice_id"], "related": [], "supplier_id": sid2, "recoverable_usd": round(rec, 2),
                    "note": "still billed at the old contract's prices after the renegotiated contract started"})
        touched.add(inv["invoice_id"])

    # P3 escalation charged one day early, and charged correctly on its first day
    s3 = next(s for s in g.suppliers if g.contracts[s["supplier_id"]]["escalation_pct"] and s["category"] != "SaaS")
    c3 = g.contracts[s3["supplier_id"]]
    codes = list(g.rates[s3["supplier_id"]])[:2]
    for on, leak in ((date(2025, 6, 30), True), (date(2025, 7, 1), False)):
        ls = [(cd, 10.0, r2(g.rates[s3["supplier_id"]][cd] * 1.05)) for cd in codes]
        inv = g._invoice(s3, None, on, ls)
        if c3["early_pay_discount_pct"]:
            g._pay(inv, on + timedelta(days=5), inv["amount_total"] * (1 - c3["early_pay_discount_pct"] / 100), discount=True)
        else:
            g._pay(inv, inv["due_date"] - timedelta(days=2))
        if leak:
            key.append({"type": "price_variance", "ref": inv["invoice_id"], "related": [], "supplier_id": s3["supplier_id"],
                        "recoverable_usd": round(sum(round((p - g.rates[s3["supplier_id"]][cd]) * q, 2) for cd, q, p in ls), 2),
                        "note": "escalated price charged the day before the escalation took effect"})
        else:
            exc.append({"type": "price_variance", "ref": inv["invoice_id"], "why": "escalation applies from this date"})

    # P4 a rounding difference (0.3%) is not leakage
    code4 = list(g.rates[sid1])[1]
    inv = g._invoice(p1, None, date(2025, 5, 20), [(code4, 7.0, r2(g.rates[sid1][code4] * 1.003))])
    g._pay(inv, inv["due_date"] - timedelta(days=2))
    exc.append({"type": "price_variance", "ref": inv["invoice_id"], "why": "0.3% rounding difference"})

    # P5 an Indian supplier invoicing in INR against a USD rate card: compare after conversion
    inr = g._add("suppliers", dict(supplier_id=g._id("SUP", 3), supplier_name="Vayu Freight India Pvt Ltd", tax_id="IN-GST27AABCV1234F1Z5",
                                   conglomerate_id=None, category="Logistics", region_code="APAC", country_iso="IN",
                                   payment_terms_code="NET30", status="ACTIVE"))
    g.suppliers.append(inr)
    ci = g._add("supplier_contracts", dict(contract_id=g._id("SCT", 4), supplier_id=inr["supplier_id"], start_date=date(2025, 1, 1),
                                           end_date=date(2026, 12, 31), currency="USD", payment_terms_days=30, early_pay_discount_pct=None,
                                           early_pay_discount_days=None, escalation_pct=None, escalation_effective_date=None, status="ACTIVE"))
    g.contracts[inr["supplier_id"]] = ci
    g.rates[inr["supplier_id"]] = {}
    for code, desc, unit, base in [("FRT-STD", "Standard freight, per pallet", "pallet", 70.0), ("WHS-MO", "Warehousing, per month", "month", 1500.0)]:
        g._add("contract_rate_cards", dict(rate_id=g._id("RATE", 5), contract_id=ci["contract_id"], item_code=code, item_description=desc,
                                           unit=unit, unit_price=base, valid_from=ci["start_date"], valid_to=ci["end_date"]))
        g.rates[inr["supplier_id"]][code] = base
    rate = 1 / INR_PER_USD
    for k in range(4):
        mult = 1.10 if k == 3 else 1.0
        ls = [("FRT-STD", float(20 + 5 * k), r2(70.0 * mult * INR_PER_USD)), ("WHS-MO", 1.0, r2(1500.0 * INR_PER_USD))]
        inv = g._invoice(inr, None, date(2025, 3 + 2 * k, 15), ls)
        inv.update(currency="INR", fx_rate=rate)
        g._pay(inv, inv["due_date"] - timedelta(days=1))
        if k == 3:
            q, p = ls[0][1], ls[0][2]
            key.append({"type": "price_variance", "ref": inv["invoice_id"], "related": [], "supplier_id": inr["supplier_id"],
                        "recoverable_usd": round((round(p * rate, 4) - 70.0) * q, 2), "note": "INR price 10% above the USD rate card after conversion"})
        else:
            exc.append({"type": "price_variance", "ref": inv["invoice_id"], "why": "INR invoice at the contracted USD price after conversion"})

    # D1 paid with the discount on the last day of the window
    sd = next(s for s in g.suppliers if g.contracts[s["supplier_id"]]["early_pay_discount_pct"] and s["category"] != "SaaS"
              and not g.contracts[s["supplier_id"]]["escalation_pct"])
    cd_ = g.contracts[sd["supplier_id"]]
    code = next(iter(g.rates[sd["supplier_id"]]))
    inv = g._invoice(sd, None, date(2025, 9, 1), [(code, 9.0, g.allowed_price(sd["supplier_id"], code, date(2025, 9, 1)))])
    g._pay(inv, date(2025, 9, 1) + timedelta(days=cd_["early_pay_discount_days"]), inv["amount_total"] * (1 - cd_["early_pay_discount_pct"] / 100), discount=True)
    exc.append({"type": "missed_discount", "ref": inv["invoice_id"], "why": "discount taken on the last day of the window"})

    # O1 an extra payment the supplier refunded; O2 a one-cent difference
    for kind in ("refunded", "cent"):
        orig = next(take)
        if kind == "refunded":
            extra = r2(orig["amount_total"] * 0.2)
            g._pay(orig, orig["due_date"] + timedelta(days=3), extra)
            g._pay(orig, orig["due_date"] + timedelta(days=30), -extra)
            exc.append({"type": "overpayment", "ref": orig["invoice_id"], "why": "extra payment refunded by the supplier"})
        else:
            g._pay(orig, orig["due_date"] + timedelta(days=3), 0.01)
            exc.append({"type": "overpayment", "ref": orig["invoice_id"], "why": "one-cent rounding difference"})

    # S1 the same product bought twice (second subscription unused); S2 annual billing with unassigned licences
    saas = [s for s in g.suppliers if s["category"] == "SaaS"]
    vm = next(x for x in d.rows["saas_subscriptions"] if x["product_name"] == "Video Meetings")
    other = next(s for s in saas if s["supplier_id"] != vm["supplier_id"])
    dup_sub = g._add("saas_subscriptions", dict(subscription_id=g._id("SUB", 3), supplier_id=other["supplier_id"], product_name="Video Meetings",
                                                seats_purchased=25, price_per_seat_month=15.0, start_date=date(2025, 4, 1),
                                                renewal_date=date(2026, 4, 1), owner_department="Marketing"))
    key.append({"type": "unused_saas", "ref": dup_sub["subscription_id"], "related": [vm["subscription_id"]], "supplier_id": other["supplier_id"],
                "recoverable_usd": r2(25 * 15.0 * 12), "note": "second Video Meetings subscription nobody uses (bought twice)"})
    ann = g._add("saas_subscriptions", dict(subscription_id=g._id("SUB", 3), supplier_id=saas[0]["supplier_id"], product_name="Expense Cloud",
                                            seats_purchased=40, price_per_seat_month=20.0, start_date=date(2025, 2, 1),
                                            renewal_date=date(2026, 2, 1), owner_department="Finance"))
    as_of = max(a["last_login_date"] for a in d.rows["saas_seat_assignments"] if a["last_login_date"])
    for k in range(30):
        g._add("saas_seat_assignments", dict(assignment_id=g._id("SEAT", 5), subscription_id=ann["subscription_id"],
                                             user_email=f"exp{k:03d}@meridian.example", assigned_date=date(2025, 2, 3),
                                             last_login_date=as_of - timedelta(days=rng.randint(0, 20))))
    key.append({"type": "unused_saas", "ref": ann["subscription_id"], "related": [], "supplier_id": saas[0]["supplier_id"],
                "recoverable_usd": r2(10 * 20.0 * 12), "note": "10 of 40 annual licences never assigned"})
    annual_subs = {ann["subscription_id"]}

    return {"g": g, "d": d, "key": key, "exceptions": exc, "ocr": ocr, "credit_notes": credit_notes, "annual": annual_subs}


# ------------------------------------------------------------------ rendering
def _fmt_amount(x: float) -> str:
    return f"{x:,.2f}"


def write_pack(out_dir: Path) -> Dict[str, Any]:
    p = build_pack()
    g, d = p["g"], p["d"]
    rng = random.Random(SEED + 2)
    pack = out_dir / "leakage_audit_pack"
    pack.mkdir(parents=True, exist_ok=True)
    for old in pack.iterdir():
        old.unlink()
    sid = {s["supplier_id"]: f"V-{1000 + int(s['supplier_id'].split('-')[1])}" for s in d.rows["suppliers"]}
    sname = {s["supplier_id"]: s["supplier_name"] for s in d.rows["suppliers"]}
    cid = {c["contract_id"]: f"CT-{c['start_date']:%y}-{int(c['contract_id'].split('-')[1]):03d}" for c in d.rows["supplier_contracts"]}
    po_map = {po["po_id"]: f"PO-45{int(po['po_id'].split('-')[1]):06d}" for po in d.rows["purchase_orders"]}
    doc: Dict[str, str] = {}
    n_h1 = n_h2 = n_ocr = 0
    for inv in d.rows["supplier_invoices"]:
        if inv["invoice_id"] in p["ocr"]:
            n_ocr += 1
            doc[inv["invoice_id"]] = f"SCN-{n_ocr:04d}"
        elif inv["invoice_date"] < date(2025, 7, 1):
            n_h1 += 1
            doc[inv["invoice_id"]] = f"51{n_h1:08d}"
        else:
            n_h2 += 1
            doc[inv["invoice_id"]] = f"AP25-{n_h2:05d}"
    lic = {s["subscription_id"]: f"LIC-{int(s['subscription_id'].split('-')[1]):03d}" for s in d.rows["saas_subscriptions"]}
    terms_text = {}
    for s in d.rows["suppliers"]:
        c = g.contracts.get(s["supplier_id"])
        days = c["payment_terms_days"] if c else 30
        terms_text[s["supplier_id"]] = (f"{c['early_pay_discount_pct']:g}/{c['early_pay_discount_days']} Net {days}"
                                        if c and c["early_pay_discount_pct"] else f"Net {days}")

    # 1 vendor master (Excel); some currencies left blank (all USD vendors)
    blank_cur = set(rng.sample([s["supplier_id"] for s in d.rows["suppliers"] if s["country_iso"] != "IN"], 4))
    pd.DataFrame([{"Vendor ID": sid[s["supplier_id"]], "Vendor Name": s["supplier_name"], "Tax ID": s["tax_id"],
                   "Payment Terms": terms_text[s["supplier_id"]], "Country": s["country_iso"],
                   "Currency": "" if s["supplier_id"] in blank_cur else ("INR" if s["country_iso"] == "IN" else "USD")}
                  for s in d.rows["suppliers"]]).to_excel(pack / "vendor_master.xlsx", index=False, sheet_name="Vendors")

    # 2 contracts
    with open(pack / "supplier_contracts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Contract No", "Vendor ID", "Start Date", "End Date", "Status", "Early Payment Terms", "Price Escalation %", "Escalation Effective"])
        for c in d.rows["supplier_contracts"]:
            w.writerow([cid[c["contract_id"]], sid[c["supplier_id"]], c["start_date"].isoformat(), c["end_date"].isoformat(), c["status"].title(),
                        f"{c['early_pay_discount_pct']:g}% {c['early_pay_discount_days']} days" if c["early_pay_discount_pct"] else "",
                        f"{c['escalation_pct']:g}%" if c["escalation_pct"] else "",
                        c["escalation_effective_date"].isoformat() if c["escalation_effective_date"] else ""])

    # 3 price list
    with open(pack / "contract_price_list.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Contract No", "Item Code", "Description", "Unit", "Agreed Unit Price", "Valid From", "Valid To"])
        for r in d.rows["contract_rate_cards"]:
            w.writerow([cid[r["contract_id"]], r["item_code"], r["item_description"], r["unit"], f"{r['unit_price']:.2f}",
                        r["valid_from"].isoformat(), r["valid_to"].isoformat()])

    # 4 purchase orders
    with open(pack / "purchase_orders.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["PO Number", "Vendor ID", "PO Date", "PO Amount", "Status"])
        for po in d.rows["purchase_orders"]:
            w.writerow([po_map[po["po_id"]], sid[po["supplier_id"]], po["po_date"].isoformat(), f"{po['amount_total']:.2f}", po["status"].title()])

    pays_of: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pm in d.rows["supplier_payments"]:
        pays_of[pm["invoice_id"]].append(pm)
    holds = {i["invoice_id"] for i in d.rows["supplier_invoices"] if i.get("hold_reason")}

    def status_text(inv: Dict[str, Any], style: str) -> str:
        st = inv["status"]
        if st == "CREDIT MEMO":
            return "Credit Memo"
        if st == "PAID":
            return "Paid" if style == "h1" else "PAID"
        return "Approved - awaiting payment" if style == "h1" else "VALIDATED"

    # 5 ERP export H1 (Excel): dd-Mon-yyyy dates; some currencies blank
    rows_h1, rows_h2, rows_ocr = [], [], []
    for inv in d.rows["supplier_invoices"]:
        dd = doc[inv["invoice_id"]]
        cur = inv.get("currency", "USD")
        if dd.startswith("51"):
            rows_h1.append({"Document No": dd, "Vendor ID": sid[inv["supplier_id"]], "Supplier Invoice No": inv["invoice_number"],
                            "Invoice Date": inv["invoice_date"].strftime("%d-%b-%Y"), "Gross Amount": inv["amount_total"],
                            "Currency": "" if cur == "USD" and rng.random() < 0.06 else cur,
                            "PO Number": po_map.get(inv["po_id"], ""), "Status": status_text(inv, "h1"), "Hold Reason": inv.get("hold_reason") or ""})
        elif dd.startswith("AP25"):
            st = status_text(inv, "h2")
            rows_h2.append({"INVOICE_ID": dd, "SUPPLIER_NUM": sid[inv["supplier_id"]], "INVOICE_NUM": inv["invoice_number"],
                            "INVOICE_DATE": inv["invoice_date"].isoformat(), "INVOICE_AMOUNT": _fmt_amount(inv["amount_total"]),
                            "CURRENCY_CODE": cur, "PO_NUMBER": po_map.get(inv["po_id"], ""),
                            "STATUS": "" if st == "PAID" and rng.random() < 0.05 else st, "HOLD_REASON": inv.get("hold_reason") or ""})
        else:
            name = sname[inv["supplier_id"]]
            rows_ocr.append({"Scan Ref": dd, "Vendor Name": rng.choice([name, name.upper(), f"{name} Ltd" if "Ltd" not in name else name]),
                             "Invoice No": inv["invoice_number"], "Invoice Date": inv["invoice_date"].strftime("%d %b %Y"),
                             "Total": _fmt_amount(inv["amount_total"]), "Currency": cur})
    pd.DataFrame(rows_h1).to_excel(pack / "ap_invoices_erp_jan_jun.xlsx", index=False, sheet_name="AP Invoices")
    pd.DataFrame(rows_h2).to_csv(pack / "ap_invoices_erp_jul_dec.csv", index=False)
    pd.DataFrame(rows_ocr).to_csv(pack / "scanned_invoices_ocr.csv", index=False)

    # 6 invoice lines; some line totals blank (imputable: quantity x unit price)
    with open(pack / "invoice_lines.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Document No", "Line No", "Item Code", "Quantity", "Unit Price", "Line Total"])
        n = defaultdict(int)
        for l in d.rows["supplier_invoice_lines"]:
            if l["invoice_id"] in p["ocr"]:
                continue  # scanned invoices are header-only
            n[l["invoice_id"]] += 1
            w.writerow([doc[l["invoice_id"]], n[l["invoice_id"]], l["item_code"], f"{l['quantity']:g}", f"{l['unit_price']:.2f}",
                        "" if rng.random() < 0.06 else f"{l['line_amount']:.2f}"])

    # 7 bank statement: references in free text, refunds negative; some discount flags blank (all of them "N")
    bank = []
    for inv_id, pms in pays_of.items():
        inv = next(i for i in d.rows["supplier_invoices"] if i["invoice_id"] == inv_id)
        for pm in pms:
            ref = rng.choice([f"PAY {doc[inv_id]} {inv['invoice_number']}", f"{sname[inv['supplier_id']][:16]} REMIT {doc[inv_id]}",
                              f"INV {inv['invoice_number']} / {doc[inv_id]}"]) if pm["amount"] > 0 else f"REFUND RECEIVED {doc[inv_id]}"
            bank.append({"Value Date": pm["payment_date"].isoformat(), "Beneficiary": sname[inv["supplier_id"]], "Payment Reference": ref,
                         "Amount": _fmt_amount(pm["amount"]), "Currency": inv.get("currency", "USD"),
                         "Early Discount Taken": "Y" if pm["discount_taken"] else ("" if rng.random() < 0.10 else "N")})
    bank.sort(key=lambda r: r["Value Date"])
    pd.DataFrame(bank).to_csv(pack / "bank_payments_2025.csv", index=False)

    # 8 credit notes
    pd.DataFrame([{"Credit Note No": f"CN-25-{k + 1:03d}", "Vendor ID": sid[c["supplier_id"]], "Original Document No": doc[c["invoice_id"]],
                   "Credit Date": c["date"].isoformat(), "Credit Amount": f"{c['amount']:.2f}"}
                  for k, c in enumerate(p["credit_notes"])]).to_csv(pack / "credit_notes.csv", index=False)

    # 9 licences (Excel) and 10 user activity; some account statuses blank (all active)
    pd.DataFrame([{"Licence Ref": lic[s["subscription_id"]], "Product": s["product_name"], "Vendor ID": sid[s["supplier_id"]],
                   "Licences Purchased": s["seats_purchased"],
                   "Price per Licence": r2(s["price_per_seat_month"] * 12) if s["subscription_id"] in p["annual"] else s["price_per_seat_month"],
                   "Billing": "Annual" if s["subscription_id"] in p["annual"] else "Monthly", "Renewal Date": s["renewal_date"].isoformat()}
                  for s in d.rows["saas_subscriptions"]]).to_excel(pack / "saas_licences.xlsx", index=False, sheet_name="Licences")
    act = []
    for a in d.rows["saas_seat_assignments"]:
        old = a["last_login_date"] is None or (a["last_login_date"] < date(2025, 10, 1))
        status = "Deprovisioned" if old and rng.random() < 0.3 else "Active"
        act.append({"User Email": a["user_email"], "Licence Ref": lic[a["subscription_id"]], "Assigned On": a["assigned_date"].isoformat(),
                    "Last Login": a["last_login_date"].isoformat() if a["last_login_date"] else "",
                    "Account Status": "" if status == "Active" and rng.random() < 0.08 else status})
    pd.DataFrame(act).to_csv(pack / "saas_user_activity.csv", index=False)

    # 11 FX rates (USD per unit)
    pd.DataFrame([{"Currency": cur, "Month": f"2025-{m:02d}", "USD per Unit": rate} for m in range(1, 13)
                  for cur, rate in (("INR", round(1 / INR_PER_USD, 6)), ("EUR", 1.08), ("GBP", 1.27))]).to_csv(pack / "fx_rates_2025.csv", index=False)

    # 12 an unrelated journal extract (must be ignored)
    accts = ["6100 Office Supplies", "6200 Travel", "6300 Software", "2100 Accrued Liabilities", "6400 Freight"]
    pd.DataFrame([{"Journal ID": f"JE-{k + 1:05d}", "Posting Date": (date(2025, 1, 1) + timedelta(days=rng.randint(0, 360))).isoformat(),
                   "GL Account": rng.choice(accts), "Description": rng.choice(["Monthly accrual", "Reclass", "Year-end adjustment", "Allocation"]),
                   "Debit": _fmt_amount(rng.uniform(100, 20000)) if k % 2 == 0 else "", "Credit": "" if k % 2 == 0 else _fmt_amount(rng.uniform(100, 20000)),
                   "Cost Centre": rng.choice(["CC-100", "CC-200", "CC-300"])} for k in range(160)]).to_csv(pack / "gl_journal_extract.csv", index=False)

    def ref(k: Dict[str, Any]) -> str:
        return lic[k["ref"]] if k["type"] == "unused_saas" else doc[k["ref"]]

    answer = {"pack": "leakage_audit_pack", "seeded_total_usd": round(sum(k["recoverable_usd"] for k in p["key"]), 2),
              "cases": [{**k, "ref": ref(k), "related": [doc.get(r, lic.get(r, r)) for r in k.get("related", [])],
                         "supplier": sname.get(k["supplier_id"]), "supplier_id": sid.get(k["supplier_id"])} for k in p["key"]],
              "legitimate_exceptions": [{**e, "ref": ref(e)} for e in p["exceptions"]]}
    (out_dir / "leakage_audit_pack_answer_key.json").write_text(json.dumps(answer, indent=2, default=str), encoding="utf-8")
    return answer


def write_hubspot_file(out_dir: Path) -> Dict[str, Any]:
    """One flat invoice ledger to upload to HubSpot (Files): each row is an invoice with its payment, terms, item and the
    contracted price. Seeded leakage: duplicates (number typed differently, one not paid yet), overpayments, missed
    early-payment discounts and prices above contract; look-alikes: retainers, the same number at another vendor, discounts
    taken in time, partial payments and rounding. Gaps to fill: some currencies are blank (all USD)."""
    rng = random.Random(41)
    vendors = [("Contoso Logistics", "Net 30", None), ("Northwind Traders", "2/10 Net 30", 2.0), ("Woodgrove Bank", "Net 45", None),
               ("Litware Pvt Ltd", "2/10 Net 30", 2.0), ("Fabrikam Retail", "Net 30", None), ("Adatum Systems", "Net 60", None),
               ("Trey Research", "Net 30", None), ("Lucerne Consulting", "1/15 Net 45", 1.0)]
    items = {"FRT-STD": 85.0, "WHS-MO": 2200.0, "LTP-14": 1150.0, "CON-DAY": 1400.0, "SUP-BOX": 42.0, "SEAT-PRO": 75.0}
    contract = {(v, it): r2(p * rng.uniform(0.9, 1.1)) for v, _, _ in vendors for it, p in items.items()}
    rows: List[Dict[str, Any]] = []
    key: List[Dict[str, Any]] = []
    exc: List[Dict[str, Any]] = []
    n = 0

    def add(vendor: str, terms: str, disc: Optional[float], on: date, item: str, qty: float, price: float, number: Optional[str] = None,
            paid: Optional[float] = None, paid_on: Optional[date] = None, taken: str = "N", po: str = "") -> Dict[str, Any]:
        nonlocal n
        n += 1
        amount = r2(qty * price)
        if paid is None and paid_on is None:  # paid normally: with the discount inside the window when the terms offer one
            if disc:
                paid_on, paid, taken = on + timedelta(days=rng.randint(4, 10)), r2(amount * (1 - disc / 100)), "Y"
            else:
                paid_on, paid = on + timedelta(days=int(terms.split()[-1]) - rng.randint(0, 5)), amount
        row = {"Doc No": f"HS-{n:04d}", "Invoice No": number or f"{vendor[:3].upper()}-{rng.randint(10000, 99999)}", "Vendor": vendor,
               "Invoice Date": on.isoformat(), "Item": item, "Qty": qty, "Unit Price": price, "Contract Price": contract[(vendor, item)],
               "Amount": amount, "Currency": "" if rng.random() < 0.06 else "USD", "PO Number": po, "Payment Terms": terms,
               "Paid Amount": "" if paid is None else paid, "Payment Date": paid_on.isoformat() if paid_on else "", "Discount Taken": taken}
        rows.append(row)
        return row

    start = date(2025, 7, 1)
    for k in range(140):  # normal invoices at the contracted price
        v, terms, disc = vendors[k % len(vendors)]
        it = rng.choice(list(items))
        add(v, terms, disc, start + timedelta(days=rng.randint(0, 150)), it, float(rng.randint(1, 30)), contract[(v, it)])
    plain = [v for v in vendors if not v[2]]

    for k in range(4):  # duplicates: the same invoice entered again with the number typed differently, and paid again
        v, terms, _ = plain[k]
        orig = add(v, terms, None, start + timedelta(days=10 + 7 * k), "CON-DAY", float(3 + k), contract[(v, "CON-DAY")])
        num = orig["Invoice No"]
        variant = [num.replace("-", ""), num.lower() + " ", num.replace("-", "-0"), num.replace("-", " ")][k]
        on = date.fromisoformat(orig["Invoice Date"]) + timedelta(days=3 + 4 * k)
        dup = add(v, terms, None, on, "CON-DAY", orig["Qty"], orig["Unit Price"], number=variant)
        key.append({"type": "duplicate_invoice", "ref": dup["Doc No"], "related": [orig["Doc No"]], "recoverable_usd": dup["Paid Amount"],
                    "note": "same invoice entered twice and paid twice"})
    v, terms, _ = plain[0]  # a copy caught before payment
    orig = add(v, terms, None, start + timedelta(days=90), "WHS-MO", 1.0, contract[(v, "WHS-MO")])
    dup = add(v, terms, None, start + timedelta(days=94), "WHS-MO", 1.0, orig["Unit Price"], number=orig["Invoice No"], paid=None, paid_on=start)
    dup["Paid Amount"], dup["Payment Date"] = "", ""
    key.append({"type": "duplicate_invoice", "ref": dup["Doc No"], "related": [orig["Doc No"]], "recoverable_usd": dup["Amount"],
                "note": "copy not paid yet: hold the payment"})

    for k in range(4):  # overpayments
        v, terms, _ = plain[(k + 1) % len(plain)]
        r = add(v, terms, None, start + timedelta(days=30 + 9 * k), "LTP-14", float(2 + k), contract[(v, "LTP-14")])
        extra = r2(r["Amount"] * (0.05 + 0.04 * k))
        r["Paid Amount"] = r2(r["Amount"] + extra)
        key.append({"type": "overpayment", "ref": r["Doc No"], "related": [], "recoverable_usd": extra, "note": "paid more than invoiced"})

    disc_vendors = [v for v in vendors if v[2]]
    for k in range(4):  # missed early-payment discounts
        v, terms, disc = disc_vendors[k % len(disc_vendors)]
        on = start + timedelta(days=40 + 8 * k)
        r = add(v, terms, disc, on, "FRT-STD", float(40 + k), contract[(v, "FRT-STD")], paid=0.0, paid_on=on)
        r["Paid Amount"], r["Payment Date"], r["Discount Taken"] = r["Amount"], (on + timedelta(days=25)).isoformat(), "N"
        key.append({"type": "missed_discount", "ref": r["Doc No"], "related": [], "recoverable_usd": r2(r["Amount"] * disc / 100),
                    "note": f"{terms}: paid on day 25 without the discount"})

    for k in range(4):  # prices above the contract
        v, terms, _ = plain[(k + 2) % len(plain)]
        cp = contract[(v, "SUP-BOX")]
        price = r2(cp * (1.07 + 0.03 * k))
        r = add(v, terms, None, start + timedelta(days=60 + 6 * k), "SUP-BOX", float(20 + 5 * k), price)
        key.append({"type": "price_variance", "ref": r["Doc No"], "related": [], "recoverable_usd": r2((price - cp) * r["Qty"]),
                    "note": f"unit price {price} vs contract {cp}"})

    # look-alikes that must not be flagged
    v, terms, _ = plain[1]
    for m in range(3):  # a monthly retainer: same amount, different numbers
        r = add(v, terms, None, date(2025, 8 + m, 1), "WHS-MO", 1.0, contract[(v, "WHS-MO")])
        if m:
            exc.append({"type": "duplicate_invoice", "ref": r["Doc No"], "why": "monthly retainer"})
    a = add(plain[2][0], plain[2][1], None, start + timedelta(days=70), "SUP-BOX", 10.0, contract[(plain[2][0], "SUP-BOX")], number="INV-2001")
    b = add(plain[3][0], plain[3][1], None, start + timedelta(days=72), "SUP-BOX", 10.0, contract[(plain[3][0], "SUP-BOX")], number="INV-2001")
    for r in (a, b):  # header-only rows with the same amount: only the vendor tells them apart
        r.update({"Item": "", "Qty": "", "Unit Price": "", "Contract Price": "", "Amount": 1250.0, "Paid Amount": 1250.0})
    exc.append({"type": "duplicate_invoice", "ref": b["Doc No"], "why": "same number at a different vendor"})
    v, terms, disc = disc_vendors[0]
    r = add(v, terms, disc, start + timedelta(days=100), "FRT-STD", 12.0, contract[(v, "FRT-STD")])
    exc.append({"type": "missed_discount", "ref": r["Doc No"], "why": "discount taken inside the window"})
    v, terms, _ = plain[4 % len(plain)]
    r = add(v, terms, None, start + timedelta(days=110), "LTP-14", 2.0, contract[(v, "LTP-14")])
    r["Paid Amount"] = r2(r["Amount"] / 2)
    exc.append({"type": "overpayment", "ref": r["Doc No"], "why": "partial payment, not an overpayment"})
    r = add(v, terms, None, start + timedelta(days=115), "SUP-BOX", 30.0, r2(contract[(v, "SUP-BOX")] * 1.003))
    exc.append({"type": "price_variance", "ref": r["Doc No"], "why": "0.3% rounding"})

    rows.sort(key=lambda x: x["Invoice Date"])
    folder = out_dir / "hubspot_upload"
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(folder / "vendor_invoices_h2_2025.csv", index=False)
    answer = {"file": "hubspot_upload/vendor_invoices_h2_2025.csv", "seeded_total_usd": round(sum(k["recoverable_usd"] for k in key), 2),
              "cases": key, "legitimate_exceptions": exc}
    (out_dir / "hubspot_upload_answer_key.json").write_text(json.dumps(answer, indent=2, default=str), encoding="utf-8")
    return answer


def write_approval_file(out_dir: Path) -> List[Path]:
    """One file whose problems need a person: ambiguous dates, amounts typed as text, rows without an id, exact duplicates;
    plus gaps the agent fills on its own (cost centre) and stray spaces."""
    folder = out_dir / "approval_test"
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        ["PMT-1001", " Contoso Logistics ", "CON-48211", "03/04/2025", "12,450.00", "Priya M.", "CC-100"],
        ["PMT-1002", "Woodgrove Bank", "WGB-10233", "15/04/2025", "8,200.50", "Rahul S.", "CC-200"],
        ["PMT-1003", "Northwind Traders ", "NOR-77310", "04/05/2025", "TBC", "Priya M.", ""],
        ["PMT-1004", "Litware Pvt Ltd", "LIT-55102", "2025-05-20", "3,999.00", "Anita K.", "CC-300"],
        ["PMT-1005", "Fabrikam Retail", "FAB-90021", "06/07/2025", "15,000.00", "Rahul S.", "CC-200"],
        ["PMT-1005", "Fabrikam Retail", "FAB-90021", "06/07/2025", "15,000.00", "Rahul S.", "CC-200"],
        ["", "", "", "07/08/2025", "450.00", "", "CC-100"],
        ["PMT-1007", "Adatum Systems", "ADT-31007", "2025-08-14", "pending", "Anita K.", ""],
        ["PMT-1008", "Contoso Logistics", "CON-48390", "09/09/2025", "21,300.00", "Priya M.", "CC-100"],
        ["PMT-1009", "Woodgrove Bank", "WGB-10301", "2025-09-30", "6,750.25", "Rahul S.", "CC-200"],
        ["PMT-1010", "Northwind Traders", "NOR-77402", "10/11/2025", "9,120.00", "", "CC-100"],
        ["", "", "", "11/12/2025", "", "", ""],
    ]
    cols = ["Payment ID", "Vendor", "Invoice No", "Payment Date", "Amount USD", "Approver", "Cost Centre"]
    csv_path, xlsx_path = folder / "vendor_payments_review.csv", folder / "vendor_payments_review.xlsx"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    pd.DataFrame(rows, columns=cols).to_excel(xlsx_path, index=False, sheet_name="Payments")
    return [csv_path, xlsx_path]
