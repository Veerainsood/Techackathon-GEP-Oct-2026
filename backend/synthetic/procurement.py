"""Synthetic procurement data (schema ``procurement``) with seeded spend leakage and an answer key.

Tables: suppliers, supplier_contracts (payment terms, early-payment discounts, agreed price escalations),
contract_rate_cards, purchase_orders, supplier_invoices, supplier_invoice_lines, supplier_payments, saas_subscriptions,
saas_seat_assignments. Some suppliers are companies that already exist in the finance data (linked by conglomerate_id).

Seeded leakage (answer key, kept outside the database): duplicate invoices, price above the contract rate, missed
early-payment discounts, overpayments, and unused SaaS seats. Legitimate look-alikes are seeded too and must not be
flagged: monthly retainers with identical amounts, prices that include an agreed escalation, late payments of invoices
that were on hold, and seats assigned during onboarding.

Generated from its own seeded random stream, so the 39 finance tables are unaffected.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

AS_OF = date(2025, 12, 31)
SCHEMA = "procurement"

# (name, category, region); names matching existing customer groups are linked to them (shared companies)
SUPPLIERS = [
    ("Contoso Logistics", "Logistics", "AMER"), ("Northwind Traders", "Hardware", "AMER"), ("Adatum Systems", "SaaS", "EMEA"),
    ("Litware Pvt Ltd", "SaaS", "APAC"), ("Woodgrove Bank", "Financial services", "AMER"), ("Fabrikam Retail", "Facilities", "EMEA"),
    ("Proseware Cloud", "SaaS", "AMER"), ("Tailspin Freight", "Logistics", "EMEA"), ("Blue Yonder Office", "Facilities", "AMER"),
    ("Coho Hardware", "Hardware", "APAC"), ("Lucerne Consulting", "Services", "EMEA"), ("Margie Travel", "Travel", "AMER"),
    ("Trey Research", "Services", "AMER"), ("Wide World Importers", "Hardware", "EMEA"), ("Alpine Ski Supplies", "Facilities", "EMEA"),
    ("Humongous Insurance", "Financial services", "AMER"), ("Fourth Coffee", "Facilities", "AMER"), ("Wingtip Toys", "Hardware", "APAC"),
    ("Relecloud", "SaaS", "AMER"), ("VanArsdel Analytics", "SaaS", "EMEA"), ("Graphic Design Institute", "Services", "APAC"),
    ("School of Fine Art", "Services", "EMEA"), ("Southridge Video", "SaaS", "AMER"), ("City Power & Light", "Utilities", "AMER"),
    ("Nod Publishers", "Services", "APAC"),
]
ITEMS = {
    "Logistics": [("FRT-STD", "Standard freight, per pallet", "pallet", 85.0), ("FRT-EXP", "Express freight, per pallet", "pallet", 140.0),
                  ("WHS-MO", "Warehousing, per month", "month", 2200.0), ("HDL-HR", "Handling, per hour", "hour", 48.0)],
    "Hardware": [("LTP-14", "Laptop 14in", "unit", 1150.0), ("MON-27", "Monitor 27in", "unit", 320.0), ("DCK-USB", "USB-C dock", "unit", 180.0),
                 ("SRV-1U", "Rack server 1U", "unit", 6400.0)],
    "SaaS": [("SEAT-STD", "Standard seat, per month", "seat-month", 35.0), ("SEAT-PRO", "Pro seat, per month", "seat-month", 75.0),
             ("API-10K", "API calls, per 10k", "10k calls", 12.0), ("SUP-PREM", "Premium support, per month", "month", 900.0)],
    "Financial services": [("PAY-TXN", "Payment processing, per 1k transactions", "1k txn", 210.0), ("FX-MO", "FX service, per month", "month", 650.0)],
    "Facilities": [("CLN-MO", "Office cleaning, per month", "month", 3800.0), ("MNT-HR", "Maintenance, per hour", "hour", 65.0),
                   ("SUP-BOX", "Office supplies box", "box", 42.0)],
    "Services": [("CON-DAY", "Consulting, per day", "day", 1400.0), ("WRK-SHP", "Workshop", "session", 4800.0), ("RPT-ANL", "Analysis report", "report", 2600.0)],
    "Travel": [("TRV-AIR", "Air ticket", "ticket", 640.0), ("TRV-HTL", "Hotel night", "night", 210.0)],
    "Utilities": [("ELC-MWH", "Electricity, per MWh", "MWh", 118.0), ("WTR-MO", "Water, per month", "month", 450.0)],
}
REGION_COUNTRY = {"AMER": "US", "EMEA": "GB", "APAC": "SG"}


@dataclass
class ProcurementData:
    rows: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    answer_key: List[Dict[str, Any]] = field(default_factory=list)  # leakage cases
    exceptions: List[Dict[str, Any]] = field(default_factory=list)  # legitimate look-alikes that must not be flagged


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


class ProcurementGenerator:
    def __init__(self, seed: int = 20261024, groups: Optional[Dict[str, str]] = None):
        self.rng = random.Random(seed)
        self.groups = groups or {}  # existing conglomerate name -> conglomerate_id
        self.d = ProcurementData()
        self.seq: Dict[str, int] = {}

    def _id(self, prefix: str, width: int = 5) -> str:
        self.seq[prefix] = self.seq.get(prefix, 0) + 1
        return f"{prefix}-{self.seq[prefix]:0{width}d}"

    def _add(self, table: str, row: Dict[str, Any]) -> Dict[str, Any]:
        self.d.rows.setdefault(table, []).append(row)
        return row

    def generate(self) -> ProcurementData:
        rng = self.rng
        self.suppliers, self.contracts, self.rates = [], {}, {}
        for name, category, region in SUPPLIERS:
            sid = self._id("SUP", 3)
            s = self._add("suppliers", dict(supplier_id=sid, supplier_name=name, conglomerate_id=self.groups.get(name), category=category,
                                            region_code=region, country_iso=REGION_COUNTRY[region],
                                            payment_terms_code=rng.choice(["NET30", "NET45", "NET60"]), status="ACTIVE"))
            self.suppliers.append(s)
            terms = int(s["payment_terms_code"][3:])
            discount = rng.random() < 0.45
            escalation = rng.random() < 0.3
            cid = self._id("SCT", 4)
            c = self._add("supplier_contracts", dict(
                contract_id=cid, supplier_id=sid, start_date=date(2024, 1, 1) + timedelta(days=rng.randint(0, 300)),
                end_date=date(2026, 12, 31), currency="USD", payment_terms_days=terms,
                early_pay_discount_pct=2.0 if discount else None, early_pay_discount_days=10 if discount else None,
                escalation_pct=5.0 if escalation else None, escalation_effective_date=date(2025, 7, 1) if escalation else None,
                status="ACTIVE"))
            self.contracts[sid] = c
            items = ITEMS[category]
            self.rates[sid] = {}
            for code, desc, unit, base in items:
                price = r2(base * rng.uniform(0.85, 1.15))
                self._add("contract_rate_cards", dict(rate_id=self._id("RATE", 5), contract_id=cid, item_code=code, item_description=desc,
                                                      unit=unit, unit_price=price, valid_from=c["start_date"], valid_to=c["end_date"]))
                self.rates[sid][code] = price
        self._purchasing()
        self._retainers()
        self._seed_leakage()
        self._saas()
        return self.d

    # ------------------------------------------------------------------ normal purchasing
    def allowed_price(self, sid: str, code: str, on: date) -> float:
        c, base = self.contracts[sid], self.rates[sid][code]
        if c["escalation_pct"] and on >= c["escalation_effective_date"]:
            return r2(base * (1 + c["escalation_pct"] / 100))
        return base

    def _invoice(self, s: Dict[str, Any], po_id: Optional[str], inv_date: date, lines: List[Tuple[str, float, float]],
                 number: Optional[str] = None, hold_reason: Optional[str] = None) -> Dict[str, Any]:
        iid = self._id("SINV", 5)
        c = self.contracts[s["supplier_id"]]
        total = r2(sum(q * p for _code, q, p in lines))
        inv = self._add("supplier_invoices", dict(
            invoice_id=iid, supplier_id=s["supplier_id"], po_id=po_id, invoice_number=number or f"{s['supplier_name'][:3].upper()}-{self.rng.randint(10000, 99999)}",
            invoice_date=inv_date, due_date=inv_date + timedelta(days=c["payment_terms_days"]), currency="USD",
            amount_total=total, status="PAID", hold_reason=hold_reason))
        for code, q, p in lines:
            self._add("supplier_invoice_lines", dict(line_id=self._id("SIL", 6), invoice_id=iid, item_code=code, quantity=q,
                                                     unit_price=p, line_amount=r2(q * p)))
        return inv

    def _pay(self, inv: Dict[str, Any], on: date, amount: Optional[float] = None, discount: bool = False) -> None:
        self._add("supplier_payments", dict(payment_id=self._id("SPAY", 5), invoice_id=inv["invoice_id"], supplier_id=inv["supplier_id"],
                                            payment_date=on, amount=r2(amount if amount is not None else inv["amount_total"]),
                                            method=self.rng.choice(["ACH", "WIRE", "ACH"]), discount_taken=discount))

    def _pay_normally(self, inv: Dict[str, Any]) -> None:
        c = self.contracts[inv["supplier_id"]]
        if c["early_pay_discount_pct"]:
            on = inv["invoice_date"] + timedelta(days=self.rng.randint(3, c["early_pay_discount_days"]))
            self._pay(inv, on, inv["amount_total"] * (1 - c["early_pay_discount_pct"] / 100), discount=True)
        else:
            on = inv["due_date"] - timedelta(days=self.rng.randint(0, 6))
            if self.rng.random() < 0.12:  # some invoices are paid in two instalments that add up exactly
                half = r2(inv["amount_total"] / 2)
                self._pay(inv, on - timedelta(days=10), half)
                self._pay(inv, on, inv["amount_total"] - half)
            else:
                self._pay(inv, on)

    def _purchasing(self) -> None:
        rng = self.rng
        self.paid_invoices: List[Dict[str, Any]] = []
        for s in self.suppliers:
            n_po = rng.randint(8, 12) if s["category"] == "SaaS" else rng.randint(14, 20)
            for _ in range(n_po):
                po_date = date(2025, 1, 6) + timedelta(days=rng.randint(0, 320))
                codes = rng.sample(list(self.rates[s["supplier_id"]]), k=min(len(self.rates[s["supplier_id"]]), rng.randint(2, 4)))
                lines = [(code, float(rng.randint(1, 40)), self.allowed_price(s["supplier_id"], code, po_date)) for code in codes]
                po = self._add("purchase_orders", dict(po_id=self._id("PO", 5), supplier_id=s["supplier_id"],
                                                       contract_id=self.contracts[s["supplier_id"]]["contract_id"], po_date=po_date,
                                                       currency="USD", amount_total=r2(sum(q * p for _c, q, p in lines)), status="CLOSED"))
                inv_date = po_date + timedelta(days=rng.randint(4, 18))
                lines = [(code, q, self.allowed_price(s["supplier_id"], code, inv_date)) for code, q, _p in lines]
                inv = self._invoice(s, po["po_id"], inv_date, lines)
                if inv["due_date"] <= AS_OF:
                    self._pay_normally(inv)
                    self.paid_invoices.append(inv)
                else:
                    inv["status"] = "APPROVED"  # not yet due: in the next payment run

    def _retainers(self) -> None:
        """Monthly retainers: identical amounts every month (legitimate; not duplicates)."""
        for s in [x for x in self.suppliers if x["category"] in ("Facilities", "Services")][:3]:
            code = next(iter(self.rates[s["supplier_id"]]))
            for m in range(1, 11):
                d = date(2025, m, 1)
                price = self.allowed_price(s["supplier_id"], code, d)
                inv = self._invoice(s, None, d, [(code, 1.0, price)])
                self._pay_normally(inv)
                if m == 4:
                    self.d.exceptions.append({"type": "duplicate_invoice", "ref": inv["invoice_id"],
                                              "why": "monthly retainer: same amount every month, different periods"})

    # ------------------------------------------------------------------ seeded leakage
    def _seed_leakage(self) -> None:
        rng = self.rng
        no_discount = [i for i in self.paid_invoices if not self.contracts[i["supplier_id"]]["early_pay_discount_pct"]]
        with_discount = [i for i in self.paid_invoices if self.contracts[i["supplier_id"]]["early_pay_discount_pct"]]
        rng.shuffle(no_discount)
        rng.shuffle(with_discount)
        used = set()

        def take(pool: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
            out = [i for i in pool if i["invoice_id"] not in used][:n]
            used.update(i["invoice_id"] for i in out)
            return out

        sup = {s["supplier_id"]: s for s in self.suppliers}
        lines_of = {}
        for l in self.d.rows["supplier_invoice_lines"]:
            lines_of.setdefault(l["invoice_id"], []).append(l)

        # 1. duplicate invoices: the same invoice entered twice (number typed differently or same PO) and paid twice
        for k, orig in enumerate(take(no_discount, 8)):
            num = orig["invoice_number"]
            variant = num.replace("-", "") if k % 2 == 0 else f"{num}-A" if k % 4 == 1 else num
            same_po = k % 4 == 1
            lines = [(l["item_code"], l["quantity"], l["unit_price"]) for l in lines_of[orig["invoice_id"]]]
            dup = self._invoice(sup[orig["supplier_id"]], orig["po_id"] if same_po or k % 4 == 3 else None,
                                orig["invoice_date"] + timedelta(days=rng.randint(1, 6)), lines, number=variant)
            unpaid = k == 7  # caught before payment: should be held, not clawed back
            if unpaid:
                dup["status"] = "APPROVED"
            else:
                self._pay(dup, dup["due_date"] - timedelta(days=2))
            self.d.answer_key.append({"type": "duplicate_invoice", "ref": dup["invoice_id"], "related": [orig["invoice_id"]],
                                      "supplier_id": dup["supplier_id"], "recoverable_usd": dup["amount_total"],
                                      "note": "not yet paid: hold the payment" if unpaid else "paid twice"})

        # 2. price above the contract rate (no escalation clause covers it)
        for orig in take([i for i in no_discount if not self.contracts[i["supplier_id"]]["escalation_pct"]
                          and len(lines_of[i["invoice_id"]]) >= 1], 8):
            line = lines_of[orig["invoice_id"]][0]
            bump = rng.uniform(0.06, 0.20)
            new_price = r2(line["unit_price"] * (1 + bump))
            over = r2((new_price - line["unit_price"]) * line["quantity"])
            line["unit_price"], line["line_amount"] = new_price, r2(new_price * line["quantity"])
            orig["amount_total"] = r2(sum(l["line_amount"] for l in lines_of[orig["invoice_id"]]))
            self._fix_payments(orig)
            self.d.answer_key.append({"type": "price_variance", "ref": orig["invoice_id"], "related": [line["line_id"]],
                                      "supplier_id": orig["supplier_id"], "recoverable_usd": over,
                                      "note": f"{line['item_code']} invoiced {bump:.0%} above the contract rate"})
        # legitimate: prices after an agreed escalation are 5% higher and must not be flagged
        esc = [i for i in self.paid_invoices if self.contracts[i["supplier_id"]]["escalation_pct"]
               and i["invoice_date"] >= self.contracts[i["supplier_id"]]["escalation_effective_date"] and i["invoice_id"] not in used]
        for i in esc[:3]:
            used.add(i["invoice_id"])
            self.d.exceptions.append({"type": "price_variance", "ref": i["invoice_id"], "why": "price includes the agreed 5% escalation"})

        # 3. missed early-payment discounts: paid after the discount window although the contract offers 2/10
        for inv in take(with_discount, 8):
            self._unpay(inv)
            c = self.contracts[inv["supplier_id"]]
            self._pay(inv, inv["invoice_date"] + timedelta(days=rng.randint(c["early_pay_discount_days"] + 5, c["payment_terms_days"])))
            self.d.answer_key.append({"type": "missed_discount", "ref": inv["invoice_id"], "related": [],
                                      "supplier_id": inv["supplier_id"],
                                      "recoverable_usd": r2(inv["amount_total"] * c["early_pay_discount_pct"] / 100),
                                      "note": "2% discount available within 10 days; paid later"})
        # legitimate: invoice on hold for a dispute, so paying late was correct
        for inv in take(with_discount, 2):
            self._unpay(inv)
            inv["hold_reason"] = "Quality dispute, resolved after inspection"
            self._pay(inv, inv["invoice_date"] + timedelta(days=self.contracts[inv["supplier_id"]]["payment_terms_days"] - 2))
            self.d.exceptions.append({"type": "missed_discount", "ref": inv["invoice_id"], "why": "invoice was on hold for a dispute"})

        # 4. overpayments: paid more than invoiced (extra transfer or wrong amount)
        for k, inv in enumerate(take(no_discount, 6)):
            if k % 2 == 0:
                extra = r2(inv["amount_total"] * rng.uniform(0.05, 0.25))
                self._pay(inv, max(p["payment_date"] for p in self.d.rows["supplier_payments"] if p["invoice_id"] == inv["invoice_id"])
                          + timedelta(days=3), extra)
            else:
                self._unpay(inv)
                extra = r2(inv["amount_total"] * 0.1)
                self._pay(inv, inv["due_date"], inv["amount_total"] + extra)
            self.d.answer_key.append({"type": "overpayment", "ref": inv["invoice_id"], "related": [], "supplier_id": inv["supplier_id"],
                                      "recoverable_usd": extra, "note": "payments exceed the invoice amount"})

    def _unpay(self, inv: Dict[str, Any]) -> None:
        self.d.rows["supplier_payments"] = [p for p in self.d.rows["supplier_payments"] if p["invoice_id"] != inv["invoice_id"]]

    def _fix_payments(self, inv: Dict[str, Any]) -> None:
        """After a price change, the invoice is paid in full exactly once (so it is not also an overpayment)."""
        self._unpay(inv)
        self._pay(inv, inv["due_date"] - timedelta(days=2))

    # ------------------------------------------------------------------ SaaS
    def _saas(self) -> None:
        rng = self.rng
        saas = [s for s in self.suppliers if s["category"] == "SaaS"]
        products = ["Workspace Suite", "Design Studio", "Analytics Cloud", "Ticketing Pro", "Video Meetings", "Code Hosting",
                    "CRM Seats", "Password Manager", "BI Dashboards", "Survey Tool", "E-signature", "Data Catalog"]
        depts = ["Sales", "Marketing", "Engineering", "Finance", "Operations", "HR"]
        n = 0
        for idx, product in enumerate(products):
            s = saas[idx % len(saas)]
            seats = rng.choice([10, 15, 20, 25, 40, 50])
            price = rng.choice([12.0, 18.0, 25.0, 35.0, 45.0])
            sub = self._add("saas_subscriptions", dict(subscription_id=self._id("SUB", 3), supplier_id=s["supplier_id"], product_name=product,
                                                       seats_purchased=seats, price_per_seat_month=price,
                                                       start_date=date(2025, 1, 1) + timedelta(days=rng.randint(0, 90)),
                                                       renewal_date=date(2026, rng.randint(1, 9), 1), owner_department=rng.choice(depts)))
            unused = rng.randint(3, max(4, seats // 3)) if idx < 8 else 0
            onboarding = 2 if idx in (8, 9) else 0  # legitimate: new joiners have not logged in yet
            for k in range(seats):
                n += 1
                if k < seats - unused - onboarding:
                    assigned, last = date(2025, 2, 1) + timedelta(days=rng.randint(0, 120)), AS_OF - timedelta(days=rng.randint(0, 25))
                elif k < seats - unused:
                    assigned, last = AS_OF - timedelta(days=rng.randint(3, 20)), None
                else:
                    assigned, last = date(2025, 2, 1) + timedelta(days=rng.randint(0, 60)), (
                        AS_OF - timedelta(days=rng.randint(75, 250)) if rng.random() < 0.7 else None)
                self._add("saas_seat_assignments", dict(assignment_id=self._id("SEAT", 5), subscription_id=sub["subscription_id"],
                                                        user_email=f"user{n:04d}@aethelgard.example", assigned_date=assigned,
                                                        last_login_date=last))
            if unused:
                self.d.answer_key.append({"type": "unused_saas", "ref": sub["subscription_id"], "related": [], "supplier_id": s["supplier_id"],
                                          "recoverable_usd": r2(unused * price * 12), "note": f"{unused} seats without a login in 60 days"})
            if onboarding:
                self.d.exceptions.append({"type": "unused_saas", "ref": sub["subscription_id"], "why": "seats assigned in the last 30 days"})


DDL = """
DROP SCHEMA IF EXISTS procurement CASCADE;
CREATE SCHEMA procurement;
CREATE TABLE procurement.suppliers (supplier_id VARCHAR(16) PRIMARY KEY, supplier_name VARCHAR(120) NOT NULL,
  conglomerate_id VARCHAR(32), category VARCHAR(40) NOT NULL, region_code VARCHAR(8) NOT NULL, country_iso VARCHAR(4) NOT NULL,
  payment_terms_code VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL);
CREATE TABLE procurement.supplier_contracts (contract_id VARCHAR(16) PRIMARY KEY, supplier_id VARCHAR(16) NOT NULL REFERENCES procurement.suppliers,
  start_date DATE NOT NULL, end_date DATE NOT NULL, currency VARCHAR(4) NOT NULL, payment_terms_days INT NOT NULL,
  early_pay_discount_pct NUMERIC(5,2), early_pay_discount_days INT, escalation_pct NUMERIC(5,2), escalation_effective_date DATE,
  status VARCHAR(16) NOT NULL);
CREATE TABLE procurement.contract_rate_cards (rate_id VARCHAR(16) PRIMARY KEY, contract_id VARCHAR(16) NOT NULL REFERENCES procurement.supplier_contracts,
  item_code VARCHAR(16) NOT NULL, item_description VARCHAR(120) NOT NULL, unit VARCHAR(16) NOT NULL, unit_price NUMERIC(12,2) NOT NULL,
  valid_from DATE NOT NULL, valid_to DATE NOT NULL);
CREATE TABLE procurement.purchase_orders (po_id VARCHAR(16) PRIMARY KEY, supplier_id VARCHAR(16) NOT NULL REFERENCES procurement.suppliers,
  contract_id VARCHAR(16) REFERENCES procurement.supplier_contracts, po_date DATE NOT NULL, currency VARCHAR(4) NOT NULL,
  amount_total NUMERIC(14,2) NOT NULL, status VARCHAR(16) NOT NULL);
CREATE TABLE procurement.supplier_invoices (invoice_id VARCHAR(16) PRIMARY KEY, supplier_id VARCHAR(16) NOT NULL REFERENCES procurement.suppliers,
  po_id VARCHAR(16) REFERENCES procurement.purchase_orders, invoice_number VARCHAR(40) NOT NULL, invoice_date DATE NOT NULL, due_date DATE NOT NULL,
  currency VARCHAR(4) NOT NULL, amount_total NUMERIC(14,2) NOT NULL, status VARCHAR(16) NOT NULL, hold_reason VARCHAR(160));
CREATE TABLE procurement.supplier_invoice_lines (line_id VARCHAR(16) PRIMARY KEY, invoice_id VARCHAR(16) NOT NULL REFERENCES procurement.supplier_invoices,
  item_code VARCHAR(16) NOT NULL, quantity NUMERIC(12,2) NOT NULL, unit_price NUMERIC(12,2) NOT NULL, line_amount NUMERIC(14,2) NOT NULL);
CREATE TABLE procurement.supplier_payments (payment_id VARCHAR(16) PRIMARY KEY, invoice_id VARCHAR(16) NOT NULL REFERENCES procurement.supplier_invoices,
  supplier_id VARCHAR(16) NOT NULL REFERENCES procurement.suppliers, payment_date DATE NOT NULL, amount NUMERIC(14,2) NOT NULL,
  method VARCHAR(8) NOT NULL, discount_taken BOOLEAN NOT NULL);
CREATE TABLE procurement.saas_subscriptions (subscription_id VARCHAR(16) PRIMARY KEY, supplier_id VARCHAR(16) NOT NULL REFERENCES procurement.suppliers,
  product_name VARCHAR(80) NOT NULL, seats_purchased INT NOT NULL, price_per_seat_month NUMERIC(10,2) NOT NULL, start_date DATE NOT NULL,
  renewal_date DATE NOT NULL, owner_department VARCHAR(40) NOT NULL);
CREATE TABLE procurement.saas_seat_assignments (assignment_id VARCHAR(16) PRIMARY KEY,
  subscription_id VARCHAR(16) NOT NULL REFERENCES procurement.saas_subscriptions, user_email VARCHAR(120) NOT NULL,
  assigned_date DATE NOT NULL, last_login_date DATE);
COMMENT ON TABLE procurement.suppliers IS 'One row per supplier we buy from, with category, region and payment terms; conglomerate_id links suppliers that are also customers.';
COMMENT ON TABLE procurement.supplier_contracts IS 'Supplier contracts with payment terms, early-payment discount (percent within days) and any agreed annual price escalation.';
COMMENT ON TABLE procurement.contract_rate_cards IS 'Agreed unit price per item for each supplier contract.';
COMMENT ON TABLE procurement.purchase_orders IS 'Purchase orders raised with suppliers.';
COMMENT ON TABLE procurement.supplier_invoices IS 'Invoices received from suppliers (accounts payable); hold_reason is set when an invoice was put on hold.';
COMMENT ON TABLE procurement.supplier_invoice_lines IS 'Line items of supplier invoices with quantity and invoiced unit price.';
COMMENT ON TABLE procurement.supplier_payments IS 'Payments made to suppliers against invoices; discount_taken marks early-payment discounts.';
COMMENT ON TABLE procurement.saas_subscriptions IS 'SaaS subscriptions with seats purchased and price per seat per month.';
COMMENT ON TABLE procurement.saas_seat_assignments IS 'SaaS seats assigned to users with the date of their last login.';
"""
ORDER = ["suppliers", "supplier_contracts", "contract_rate_cards", "purchase_orders", "supplier_invoices", "supplier_invoice_lines",
         "supplier_payments", "saas_subscriptions", "saas_seat_assignments"]


def load(data: ProcurementData, uri: str) -> Dict[str, int]:
    import psycopg2
    from psycopg2.extras import execute_values

    counts = {}
    with psycopg2.connect(uri) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        for table in ORDER:
            rows = data.rows.get(table, [])
            if not rows:
                continue
            cols = list(rows[0])
            execute_values(cur, f"INSERT INTO {SCHEMA}.{table} ({', '.join(cols)}) VALUES %s", [tuple(r[c] for c in cols) for r in rows])
            counts[table] = len(rows)
    return counts
