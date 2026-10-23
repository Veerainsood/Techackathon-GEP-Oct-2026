"""Deterministic, event-driven synthetic financial data generator.

All 39 tables are derived from one coherent underlying event model:

    CRM deal -> (won) agreement -> monthly invoices -> AR events (cash / credit / write-off)
             -> GL vouchers (double entry) -> reporting snapshots / budgets

All money is tracked internally in integer cents so that the accounting invariants
(invoice header == sum of lines, open balance == AR subledger, debits == credits,
snapshot revenue == GL revenue) hold exactly.  Every value is fictional and fully
reproducible from the seed.
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from udre.synthetic.schema_spec import get_all_39_table_definitions

SEED = 20261023
AS_OF = date(2025, 12, 31)
WINDOW_START = date(2024, 1, 1)


# --------------------------------------------------------------------------- helpers
def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_end(d: date) -> date:
    return add_months(first_of_month(d), 1) - timedelta(days=1)


def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_range(start: date, end: date) -> List[date]:
    out, cur = [], first_of_month(start)
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def amt(cents: int) -> float:
    return round(cents / 100.0, 2)


def to_cents(x: float) -> int:
    return int(round(x * 100))


# --------------------------------------------------------------------------- reference vocab
ENTITIES = [
    # code, name, region, functional ccy, billing ccy, tax regime, tax rate, erp geo code
    ("AETH-US", "Aethelgard Systems Inc.", "AMER", "USD", "USD", "US_SALES_TAX", 0.065, "GEO-NA"),
    ("AETH-UK", "Aethelgard Technologies Ltd.", "EMEA", "GBP", "GBP", "UK_VAT", 0.20, "GEO-EU"),
    ("AETH-DE", "Aethelgard Digital GmbH", "EMEA", "EUR", "EUR", "DE_MWST", 0.19, "GEO-EU"),
    ("AETH-SG", "Aethelgard Asia Pte. Ltd.", "APAC", "SGD", "USD", "SG_GST", 0.09, "GEO-AP"),
]
REGIONS = [("AMER", "Americas"), ("EMEA", "Europe, Middle East & Africa"), ("APAC", "Asia Pacific")]
PRODUCT_LINES = [
    ("PL_CLOUD", "Cloud Platform", "CLD", "Cloud"),
    ("PL_ANALYTICS", "Analytics Suite", "ANL", "Analytics"),
    ("PL_SECURITY", "Security Services", "SEC", "Security"),
    ("PL_ADVISORY", "Advisory Services", "ADV", "Professional Services"),
]
INDUSTRIES = [
    ("IND_FIN", "Financial Services", ["Banking", "Insurance"]),
    ("IND_HEALTH", "Healthcare", ["Hospitals", "Life Sciences"]),
    ("IND_RETAIL", "Retail & Consumer", ["Retail", "Consumer Goods"]),
    ("IND_MFG", "Manufacturing", ["Industrial", "Automotive"]),
    ("IND_PUBSEC", "Public Sector", ["Government", "Education"]),
]
UNMAPPED_CRM_INDUSTRY = "Media & Telecom"  # deliberately missing from crosswalks
CRM_REGION_VARIANTS = {
    "AMER": ["North America", "Americas", "NA"],
    "EMEA": ["Europe", "EMEA", "UK & Ireland"],
    "APAC": ["Asia Pacific", "APAC", "ANZ"],
}
ERP_GEO = {"AMER": "GEO-NA", "EMEA": "GEO-EU", "APAC": "GEO-AP"}

CATALOG = [
    # id, sku, name, erp product line, revenue stream, revenue account number, list price
    ("PRD-001", "AE-CLD-CORE", "Aether Cloud Platform - Core", "CLD", "SaaS_Subscription", "4010", 1200.0),
    ("PRD-002", "AE-CLD-PSUP", "Aether Cloud Premium Support", "CLD", "SaaS_Subscription", "4010", 450.0),
    ("PRD-003", "AE-CLD-MHST", "Managed Cloud Hosting", "CLD", "Managed_Cloud", "4020", 2600.0),
    ("PRD-004", "AE-ANL-SUIT", "Insight Analytics Suite", "ANL", "SaaS_Subscription", "4010", 900.0),
    ("PRD-005", "AE-ANL-CONN", "Insight Data Connectors", "ANL", "SaaS_Subscription", "4010", 300.0),
    ("PRD-006", "AE-ANL-OVRG", "Analytics Usage Overage", "ANL", "Usage_Overage", "4040", 0.15),
    ("PRD-007", "AE-SEC-MON", "Sentinel Threat Monitoring", "SEC", "Managed_Cloud", "4020", 1800.0),
    ("PRD-008", "AE-SEC-IDV", "Sentinel Identity Vault", "SEC", "SaaS_Subscription", "4010", 650.0),
    ("PRD-009", "AE-ADV-IMPL", "Implementation Services", "ADV", "Advisory_Services", "4030", 210.0),
    ("PRD-010", "AE-ADV-RETN", "Strategic Advisory Retainer", "ADV", "Advisory_Services", "4030", 15000.0),
    ("PRD-011", "AE-ADV-TRN", "Training Workshops", "ADV", "Advisory_Services", "4030", 3500.0),
]
SUBSCRIBABLE = [c for c in CATALOG if c[0] != "PRD-006"]

STATEMENT_GROUPS = [
    ("SG-01", "ASSET_CASH", "Cash and Equivalents", "BALANCE_SHEET", "ASSETS"),
    ("SG-02", "ASSET_AR", "Trade Receivables", "BALANCE_SHEET", "ASSETS"),
    ("SG-03", "LIAB_DEF", "Deferred Revenue", "BALANCE_SHEET", "LIABILITIES"),
    ("SG-04", "LIAB_TAX", "Indirect Tax Payable", "BALANCE_SHEET", "LIABILITIES"),
    ("SG-05", "LIAB_ACC", "Accrued Liabilities", "BALANCE_SHEET", "LIABILITIES"),
    ("SG-06", "REV_REC", "Recurring Revenue", "INCOME_STATEMENT", "REVENUE"),
    ("SG-07", "REV_SERV", "Services Revenue", "INCOME_STATEMENT", "REVENUE"),
    ("SG-08", "REV_CONTRA", "Revenue Credits and Allowances", "INCOME_STATEMENT", "REVENUE"),
    ("SG-09", "COGS", "Cost of Revenue", "INCOME_STATEMENT", "COST_OF_SALES"),
    ("SG-10", "OPEX_RD", "Research and Development", "INCOME_STATEMENT", "OPERATING_EXPENSE"),
    ("SG-11", "OPEX_SM", "Sales and Marketing", "INCOME_STATEMENT", "OPERATING_EXPENSE"),
    ("SG-12", "OPEX_GA", "General and Administrative", "INCOME_STATEMENT", "OPERATING_EXPENSE"),
    ("SG-13", "OTHER_FX", "Foreign Exchange Gain/Loss", "INCOME_STATEMENT", "OTHER_INCOME_EXPENSE"),
]
ACCOUNTS = [
    # number, title, group, class, normal side
    ("1000", "Operating Cash", "SG-01", "ASSET", "DEBIT"),
    ("1100", "Accounts Receivable - Trade", "SG-02", "ASSET", "DEBIT"),
    ("2100", "Deferred Revenue", "SG-03", "LIABILITY", "CREDIT"),
    ("2200", "Sales Tax / VAT Payable", "SG-04", "LIABILITY", "CREDIT"),
    ("2300", "Accrued Liabilities", "SG-05", "LIABILITY", "CREDIT"),
    ("4010", "Subscription Revenue", "SG-06", "REVENUE", "CREDIT"),
    ("4020", "Managed Services Revenue", "SG-06", "REVENUE", "CREDIT"),
    ("4030", "Professional Services Revenue", "SG-07", "REVENUE", "CREDIT"),
    ("4040", "Usage Revenue", "SG-06", "REVENUE", "CREDIT"),
    ("4900", "Sales Credits and Allowances", "SG-08", "REVENUE", "DEBIT"),
    ("5000", "Hosting and Infrastructure Cost", "SG-09", "EXPENSE", "DEBIT"),
    ("6100", "R&D Salaries", "SG-10", "EXPENSE", "DEBIT"),
    ("6200", "Sales Salaries and Commissions", "SG-11", "EXPENSE", "DEBIT"),
    ("6300", "G&A Salaries", "SG-12", "EXPENSE", "DEBIT"),
    ("6400", "Marketing Programs", "SG-11", "EXPENSE", "DEBIT"),
    ("6500", "Travel and Entertainment", "SG-12", "EXPENSE", "DEBIT"),
    ("6900", "Bad Debt Expense", "SG-12", "EXPENSE", "DEBIT"),
    ("7100", "Realized FX Gain/Loss", "SG-13", "EXPENSE", "DEBIT"),
]
REVENUE_ACCOUNTS = {"4010", "4020", "4030", "4040", "4900"}

STAGE_BUCKET = {
    "Qualification": "PIPELINE", "Best_Case": "BEST_CASE", "Negotiation": "BEST_CASE",
    "Commit": "COMMIT", "Closed_Won": "CLOSED", "Closed_Lost": "OMIT",
}
BUCKETS = [
    ("CLOSED", "Closed", 100.0, False),
    ("COMMIT", "Commit", 90.0, True),
    ("BEST_CASE", "Best Case", 60.0, True),
    ("PIPELINE", "Pipeline", 20.0, True),
    ("OMIT", "Omitted", 0.0, False),
]
BUCKET_PROB = {b[0]: b[2] for b in BUCKETS}
LEGACY_STAGE_LABELS = {"Closed_Won": "CLOSED-WON", "Closed_Lost": "closed lost", "Qualification": "Qual", "Commit": "COMMIT"}

NAME_A = ["Norvane", "Quillon", "Stratavex", "Halcyra", "Brightmere", "Ostrava", "Velmora", "Corvant", "Lumenfield",
          "Tessaract", "Ardent", "Kestrel", "Marbrook", "Solvane", "Oberlin", "Pyralis", "Zephyrine", "Calder",
          "Wrenford", "Isolde", "Thornbury", "Avalon", "Meridia", "Caspian", "Elmstead", "Fenwick", "Gallatin",
          "Hollis", "Ironvale", "Juniper", "Kingsway", "Larkspur", "Montclair", "Northam", "Orchard", "Penrose",
          "Ravenna", "Silverline", "Tamsin", "Umbra", "Vantage", "Westerly"]
NAME_B = ["Holdings", "Group", "Partners", "Industries", "Health", "Financial", "Retail Co.", "Manufacturing",
          "Systems", "Logistics", "Mutual", "Labs"]
# Companies in the HubSpot test portal. With the default seed, these customer groups (by position) and this
# CRM-only prospect carry their names, so CRM and finance data describe the same customers. The groups were chosen
# so finance history matches each company's HubSpot lifecycle stage: customer-stage companies are groups with signed
# contracts and invoices; Fabrikam (opportunity) is a prospect with open deals only. Names are swapped after the
# random draws, so every amount, date and ID is identical to an unrenamed build.
HUBSPOT_GROUP_NAMES = {4: "Northwind Traders", 16: "Litware Pvt Ltd", 25: "Woodgrove Bank", 27: "Contoso Logistics",
                       31: "Adatum Systems"}
HUBSPOT_PROSPECT_NAMES = {3: "Fabrikam Retail"}  # index among the unlinked prospects

FIRST = ["Avery", "Jordan", "Riley", "Morgan", "Casey", "Quinn", "Harper", "Rowan", "Elliot", "Skyler", "Dakota",
         "Reese", "Emerson", "Finley", "Hayden", "Kendall", "Logan", "Parker", "Sage", "Tatum"]
LAST = ["Abernathy", "Blackwood", "Castellano", "Delacroix", "Everhart", "Fairbanks", "Galloway", "Hargrove",
        "Iverson", "Jansen", "Kowalczyk", "Lindqvist", "Montague", "Nakamura", "Okafor", "Petrov", "Quintero",
        "Rasmussen", "Sorensen", "Thackeray"]


@dataclass
class InvoiceFact:
    invoice_id: str
    debtor_id: str
    entity: str
    agreement_id: Optional[str]
    invoice_date: date
    due_date: date
    currency: str
    fx: float
    lines: List[Dict[str, Any]]
    subtotal_doc: int
    tax_doc: int
    subtotal_usd: int
    tax_usd: int
    gross_doc: int
    gross_usd: int
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "OPEN"
    open_usd: int = 0


class SyntheticFinanceGenerator:
    """Builds all 39 tables from a single seeded event model."""

    def __init__(self, seed: int = SEED, n_groups: int = 40):
        self.seed = seed
        self.n_groups = n_groups
        self.rng = random.Random(seed)
        self.rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.fx: Dict[Tuple[str, str], float] = {}
        self.invoices: List[InvoiceFact] = []
        self.bundles: Dict[str, str] = {}
        self.account_ids: Dict[str, str] = {}
        self._voucher_seq = 0
        self._posting_seq = 0

    # ------------------------------------------------------------------ public
    def generate(self) -> Dict[str, pd.DataFrame]:
        self._fx_rates()
        self._reference_data()
        self._chart_of_accounts_and_catalog()
        self._expense_taxonomy()
        self._sales_org()
        self._customers()
        self._deals_and_snapshots()
        self._agreements()
        self._invoices_and_settlements()
        self._operating_expenses()
        self._dimension_tables()
        self._budget()
        self._reporting_snapshots()
        self._app_and_ops()
        return self._to_frames()

    def _to_frames(self) -> Dict[str, pd.DataFrame]:
        frames: Dict[str, pd.DataFrame] = {}
        for t in get_all_39_table_definitions():
            cols = [c.name.lower() for c in t.columns]
            rows = self.rows.get(t.full_name, [])
            for r in rows[:1]:
                public = {k for k in r if not k.startswith("_")}
                missing = set(cols) - public
                extra = public - set(cols)
                if missing or extra:
                    raise ValueError(f"{t.full_name}: missing={missing} extra={extra}")
            frames[t.full_name] = pd.DataFrame(rows, columns=cols)
        return frames

    # ------------------------------------------------------------------ fx
    def _fx_rates(self) -> None:
        for i, m in enumerate(month_range(date(2023, 1, 1), date(2027, 12, 1))):
            p = period_of(m)
            self.fx[("USD", p)] = 1.0
            self.fx[("EUR", p)] = round(1.085 + 0.035 * math.sin(i / 4.0) + self.rng.uniform(-0.008, 0.008), 6)
            self.fx[("GBP", p)] = round(1.265 + 0.030 * math.cos(i / 5.0) + self.rng.uniform(-0.008, 0.008), 6)

    def rate(self, ccy: str, d: date) -> float:
        return self.fx[(ccy, period_of(d))]

    # ------------------------------------------------------------------ ref
    def _reference_data(self) -> None:
        R = self.rows
        for code, name, region, fccy, _bccy, regime, _rate, _geo in ENTITIES:
            R["master_ref.entity_jurisdictions"].append(dict(
                legal_entity_code=code, legal_entity_name=name, region_code=region,
                functional_currency=fccy, tax_regime=regime))
        facets = [("REGION", "Sales Region", "Revenue Operations", True),
                  ("PRODUCT_LINE", "Product Line", "Product Finance", True),
                  ("INDUSTRY", "Industry Vertical", "Revenue Operations", True),
                  ("SEGMENT", "Market Segment", "Sales Strategy", False)]
        for f in facets:
            R["master_ref.taxonomy_facets"].append(dict(
                facet_code=f[0], facet_title=f[1], governance_owner=f[2], is_mandatory_reporting=f[3]))
        self.node_id: Dict[Tuple[str, str], str] = {}
        seq = 0

        def node(facet: str, code: str, label: str, active: bool = True) -> None:
            nonlocal seq
            seq += 1
            nid = f"TXN-{seq:03d}"
            self.node_id[(facet, code)] = nid
            R["master_ref.taxonomy_nodes"].append(dict(
                node_id=nid, facet_code=facet, canonical_code=code, canonical_label=label,
                sort_ordinal=seq, is_active=active))

        for code, label in REGIONS:
            node("REGION", code, label)
        for code, label, _erp, _crm in PRODUCT_LINES:
            node("PRODUCT_LINE", code, label)
        node("PRODUCT_LINE", "PL_ALL", "Unallocated / Corporate")
        for code, label, _raw in INDUSTRIES:
            node("INDUSTRY", code, label)
        node("INDUSTRY", "IND_ALL", "Unallocated / Unmapped")
        for code, label in [("SEG_STRAT", "Strategic"), ("SEG_ENT", "Enterprise"), ("SEG_COMM", "Commercial")]:
            node("SEGMENT", code, label)
        node("SEGMENT", "SEG_SMB", "Small Business (retired)", active=False)

        cw = 0

        def crosswalk(system: str, facet: str, raw: str, target_code: str, conf: float = 1.0) -> None:
            nonlocal cw
            cw += 1
            R["master_ref.taxonomy_crosswalks"].append(dict(
                crosswalk_id=f"XW-{cw:04d}", source_system_code=system, facet_code=facet,
                raw_source_value=raw, target_node_id=self.node_id[(facet, target_code)],
                mapping_confidence=conf))

        for region, geo in ERP_GEO.items():
            crosswalk("ERP", "REGION", geo, region)
        for region, variants in CRM_REGION_VARIANTS.items():
            for v in variants:
                crosswalk("CRM", "REGION", v, region, 1.0 if v != "NA" else 0.85)
        for code, _label, erp, crm in PRODUCT_LINES:
            crosswalk("ERP", "PRODUCT_LINE", erp, code)
            crosswalk("CRM", "PRODUCT_LINE", crm, code)
        crosswalk("ERP", "PRODUCT_LINE", "CORP", "PL_ALL")
        for code, _label, raws in INDUSTRIES:
            for raw in raws:
                crosswalk("CRM", "INDUSTRY", raw, code)
        for raw, code in [("Strategic", "SEG_STRAT"), ("Enterprise", "SEG_ENT"), ("Commercial", "SEG_COMM")]:
            crosswalk("CRM", "SEGMENT", raw, code)

    # ------------------------------------------------------------------ coa / catalog
    def _chart_of_accounts_and_catalog(self) -> None:
        R = self.rows
        for g in STATEMENT_GROUPS:
            R["ledger_erp.ledger_statement_groups"].append(dict(
                stmt_group_id=g[0], group_code=g[1], group_title=g[2], financial_statement=g[3], rollup_section=g[4]))
        for i, (num, title, grp, cls, side) in enumerate(ACCOUNTS, start=1):
            aid = f"GLA-{i:03d}"
            self.account_ids[num] = aid
            R["ledger_erp.chart_of_accounts"].append(dict(
                account_id=aid, account_number=num, account_title=title, stmt_group_id=grp,
                account_class=cls, normal_balance_side=side, is_posting_allowed=True))
        legacy = [
            ("LegacyLedger_EU", "8400", "Umsatzerloese Software", "4010"),
            ("LegacyLedger_EU", "8410", "Umsatzerloese Dienstleistung", "4030"),
            ("LegacyLedger_EU", "1400", "Forderungen LuL", "1100"),
            ("LegacyBooks_US", "40000", "Software Sales", "4010"),
            ("LegacyBooks_US", "40500", "Hosting Sales", "4020"),
            ("LegacyBooks_US", "12000", "Trade AR", "1100"),
            ("LegacyBooks_US", "65000", "T&E and Marketing", "6500"),
            ("LegacyBooks_US", "65000", "T&E and Marketing", "6400"),  # one legacy code split into two
        ]
        for i, (sysn, code, desc, modern) in enumerate(legacy, start=1):
            R["ledger_erp.legacy_coa_crosswalk"].append(dict(
                legacy_map_id=f"LGM-{i:03d}", legacy_system_name=sysn, legacy_acct_code=code,
                legacy_acct_desc=desc, modern_account_id=self.account_ids[modern],
                effective_cutover_date=date(2023, 7, 1) if sysn == "LegacyBooks_US" else date(2023, 10, 1)))
        for cid, sku, name, pl, stream, acct, price in CATALOG:
            R["ledger_erp.catalog_offerings"].append(dict(
                catalog_item_id=cid, sku_code=sku, offering_name=name, product_line_raw=pl,
                revenue_stream_type=stream, default_rev_account_id=self.account_ids[acct],
                list_unit_price_usd=price))

    def _expense_taxonomy(self) -> None:
        R = self.rows
        nodes = [
            ("CN-01", None, 1, "F-RND", "Research & Development"),
            ("CN-02", None, 1, "F-SM", "Sales & Marketing"),
            ("CN-03", None, 1, "F-GA", "General & Administrative"),
            ("CN-04", None, 1, "F-COGS", "Cost of Revenue"),
            ("CN-05", "CN-01", 2, "G-RND-PEOPLE", "R&D People Costs"),
            ("CN-06", "CN-02", 2, "G-SM-PEOPLE", "Sales People Costs"),
            ("CN-07", "CN-02", 2, "G-SM-PROG", "Marketing Programs & Travel"),
            ("CN-08", "CN-03", 2, "G-GA-PEOPLE", "G&A People Costs"),
            ("CN-09", "CN-03", 2, "G-GA-OTHER", "G&A Other Costs"),
            ("CN-10", "CN-04", 2, "G-COGS-HOST", "Hosting Costs"),
            ("CN-11", "CN-05", 3, "L-RND-SAL", "Engineering Salaries"),
            ("CN-12", "CN-06", 3, "L-SM-SAL", "Sales Compensation"),
            ("CN-13", "CN-07", 3, "L-SM-MKT", "Demand Generation"),
            ("CN-14", "CN-07", 3, "L-SM-TRV", "Sales Travel"),
            ("CN-15", "CN-08", 3, "L-GA-SAL", "Corporate Salaries"),
            ("CN-16", "CN-09", 3, "L-GA-TRV", "Corporate Travel"),
            ("CN-17", "CN-09", 3, "L-GA-BAD", "Credit Losses"),
            ("CN-18", "CN-10", 3, "L-COGS-HOST", "Cloud Infrastructure"),
        ]
        for n in nodes:
            R["ledger_erp.cost_taxonomy_nodes"].append(dict(
                cost_node_id=n[0], parent_cost_node_id=n[1], hierarchy_level=n[2],
                cost_category_code=n[3], cost_category_title=n[4]))
        links = [("CN-11", "6100", 1.0), ("CN-12", "6200", 1.0), ("CN-13", "6400", 1.0), ("CN-14", "6500", 0.6),
                 ("CN-15", "6300", 1.0), ("CN-16", "6500", 0.4), ("CN-17", "6900", 1.0), ("CN-18", "5000", 1.0)]
        for i, (cn, acct, w) in enumerate(links, start=1):
            R["ledger_erp.cost_account_links"].append(dict(
                link_id=f"CAL-{i:03d}", cost_node_id=cn, account_id=self.account_ids[acct], allocation_weight=w))
        self.cost_leaf_for_account = {"6100": "CN-11", "6200": "CN-12", "6400": "CN-13", "6300": "CN-15",
                                      "6900": "CN-17", "5000": "CN-18"}

    # ------------------------------------------------------------------ sales org
    def _sales_org(self) -> None:
        R, rng = self.rows, self.rng
        squads = [("SQ-01", None, "Global Revenue", "GLOBAL", 1)]
        for i, (region, _l) in enumerate(REGIONS, start=2):
            squads.append((f"SQ-{i:02d}", "SQ-01", f"{region} Theater", region, 2))
        pod = 5
        for region, _l in REGIONS:
            parent = next(s[0] for s in squads if s[3] == region)
            for kind in ("Enterprise Pod", "Commercial Pod"):
                squads.append((f"SQ-{pod:02d}", parent, f"{region} {kind}", region, 3))
                pod += 1
        for s in squads:
            R["pipeline_crm.territory_squads"].append(dict(
                squad_id=s[0], parent_squad_id=s[1], squad_name=s[2], theater_region=s[3], hierarchy_depth=s[4]))
        used = set()

        def person() -> str:
            while True:
                n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                if n not in used:
                    used.add(n)
                    return n

        reps = [("REP-001", person(), "SQ-01", None, "VP_Sales", 0.0)]
        self.reps_by_region: Dict[str, List[str]] = defaultdict(list)
        self.rep_region: Dict[str, str] = {}
        idx = 2
        for region, _l in REGIONS:
            theater = next(s[0] for s in squads if s[3] == region and s[4] == 2)
            director = f"REP-{idx:03d}"
            reps.append((director, person(), theater, "REP-001", "Regional_Director", 0.0))
            idx += 1
            for s in [s for s in squads if s[3] == region and s[4] == 3]:
                for _ in range(2):
                    rid = f"REP-{idx:03d}"
                    quota = float(rng.choice([1_200_000, 1_500_000, 1_800_000, 2_100_000]))
                    reps.append((rid, person(), s[0], director, "Enterprise_AE", quota))
                    self.reps_by_region[region].append(rid)
                    self.rep_region[rid] = region
                    idx += 1
        for rid, name, sq, mgr, role, quota in reps:
            R["pipeline_crm.quota_carriers"].append(dict(
                rep_id=rid, rep_full_name=name, squad_id=sq, manager_rep_id=mgr, role_title=role,
                annual_quota_usd=quota, active_status=not (rid == "REP-012")))
        for b in BUCKETS:
            R["pipeline_crm.forecast_buckets"].append(dict(
                forecast_cat_code=b[0], bucket_label=b[1], default_probability_pct=b[2], included_in_weighted_pipe=b[3]))

    # ------------------------------------------------------------------ customers
    def _customers(self) -> None:
        R, rng = self.rows, self.rng
        names, used = [], set()
        while len(names) < self.n_groups:
            n = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)}"
            if n.split()[0] not in used:
                used.add(n.split()[0])
                names.append(n)
        hubspot_names = self.seed == SEED and self.n_groups == 40
        if hubspot_names:
            for position, real_name in HUBSPOT_GROUP_NAMES.items():
                names[position - 1] = real_name
        self.groups: List[Dict[str, Any]] = []
        self.debtors_by_group: Dict[str, List[str]] = defaultdict(list)
        self.debtor_info: Dict[str, Dict[str, Any]] = {}
        alias_seq, debtor_seq, crm_seq = 0, 0, 0
        entity_by_region = {"AMER": ["AETH-US"], "EMEA": ["AETH-UK", "AETH-DE"], "APAC": ["AETH-SG"]}
        cities = {"AETH-US": [("Chicago", "US"), ("Austin", "US"), ("Toronto", "CA")],
                  "AETH-UK": [("Manchester", "GB"), ("Dublin", "IE")],
                  "AETH-DE": [("Munich", "DE"), ("Rotterdam", "NL"), ("Lyon", "FR")],
                  "AETH-SG": [("Singapore", "SG"), ("Sydney", "AU"), ("Osaka", "JP")]}
        entity_meta = {e[0]: e for e in ENTITIES}

        def alias(gid: str, name: str, src: str, verified: bool) -> None:
            nonlocal alias_seq
            alias_seq += 1
            R["agreements.counterparty_aliases"].append(dict(
                alias_id=f"ALS-{alias_seq:04d}", conglomerate_id=gid, alias_name=name,
                alias_source=src, is_verified_match=verified))

        def add_debtor(gid: Optional[str], ent: str, legal: str, status: str = "ACTIVE") -> str:
            nonlocal debtor_seq
            debtor_seq += 1
            did = f"DBT-{debtor_seq:05d}"
            city, ctry = rng.choice(cities[ent])
            ccy = entity_meta[ent][4]
            R["ledger_erp.receivable_debtors"].append(dict(
                debtor_id=did, conglomerate_id=gid, legal_entity_code=ent, billing_legal_name=legal,
                payment_terms_code=rng.choice(["NET30", "NET30", "NET45", "NET60", "IMMED"]),
                billing_currency=ccy, billing_city=city, billing_country_iso=ctry,
                credit_limit_usd=float(rng.choice([250_000, 500_000, 1_000_000, 2_500_000])),
                account_status=status))
            self.debtor_info[did] = R["ledger_erp.receivable_debtors"][-1]
            return did

        for i, name in enumerate(names, start=1):
            gid = f"CG-{i:04d}"
            region = rng.choices(["AMER", "EMEA", "APAC"], weights=[0.5, 0.33, 0.17])[0]
            ind_code, _ind_label, ind_raws = rng.choice(INDUSTRIES)
            tier = rng.choices(["Strategic_G100", "Enterprise", "Mid_Market"], weights=[0.2, 0.5, 0.3])[0]
            rating = rng.choices(["AAA", "AA", "A", "BBB", "WATCH"], weights=[0.1, 0.25, 0.35, 0.2, 0.1])[0]
            g = dict(conglomerate_id=gid, conglomerate_name=name,
                     hq_country_iso={"AMER": "US", "EMEA": "GB", "APAC": "SG"}[region],
                     global_tier=tier, primary_industry_code=ind_code, primary_region_code=region,
                     credit_risk_rating=rating)
            R["master_ref.client_conglomerates"].append(g)
            self.groups.append(dict(g, ind_raws=ind_raws))
            base_word = name.split()[0]
            legal_suffix = {"AMER": "Inc.", "EMEA": rng.choice(["Ltd.", "GmbH", "B.V."]), "APAC": "Pte. Ltd."}[region]
            primary_ent = rng.choice(entity_by_region[region])
            d1 = add_debtor(gid, primary_ent, f"{base_word} {legal_suffix}")
            self.debtors_by_group[gid].append(d1)
            alias(gid, f"{base_word} {legal_suffix}", "Legal_Filing", True)
            alias(gid, name.upper(), "OCR_Variant", rng.random() < 0.7)
            if rng.random() < 0.35:
                other = rng.choice([e for e in ("AETH-US", "AETH-UK", "AETH-DE", "AETH-SG") if e != primary_ent])
                legacy_name = f"{base_word} {rng.choice(['Intl', 'Global Services', 'Operations'])} {entity_meta[other][0][-2:]}"
                # Edge case: some secondary billing identities are not mapped to their group.
                unmapped = i % 9 == 0
                d2 = add_debtor(None if unmapped else gid, other, legacy_name)
                self.debtors_by_group[gid].append(d2)
                alias(gid, legacy_name, "M&A_Legacy", not unmapped)
            # CRM identities (sometimes duplicated)
            n_crm = 2 if i % 7 == 0 else 1
            for k in range(n_crm):
                crm_seq += 1
                crm_region_raw = rng.choice(CRM_REGION_VARIANTS[region])
                crm_ind_raw = UNMAPPED_CRM_INDUSTRY if i % 11 == 0 else rng.choice(ind_raws)
                acct_name = name if k == 0 else f"{name} ({rng.choice(['EMEA', 'Subsidiary', 'Old'])})"
                R["pipeline_crm.prospect_accounts"].append(dict(
                    crm_account_id=f"CRM-{crm_seq:05d}", conglomerate_id=gid, crm_account_name=acct_name,
                    crm_region_raw=crm_region_raw, crm_industry_raw=crm_ind_raw,
                    market_segment={"Strategic_G100": "Strategic", "Enterprise": "Enterprise", "Mid_Market": "Commercial"}[tier],
                    account_owner_rep_id=rng.choice(self.reps_by_region[region]), is_existing_customer=False))
                if k == 1:
                    alias(gid, acct_name, "CRM_Nickname", False)
        # Unlinked prospects (no ERP footprint).
        for j in range(8):
            crm_seq += 1
            region = rng.choice(["AMER", "EMEA", "APAC"])
            ind_code, _l, raws = rng.choice(INDUSTRIES)
            prospect_name = f"{rng.choice(NAME_A)} {rng.choice(['Ventures', 'Collective', 'Dynamics'])}"
            if hubspot_names and j in HUBSPOT_PROSPECT_NAMES:
                prospect_name = HUBSPOT_PROSPECT_NAMES[j]
            R["pipeline_crm.prospect_accounts"].append(dict(
                crm_account_id=f"CRM-{crm_seq:05d}", conglomerate_id=None,
                crm_account_name=prospect_name,
                crm_region_raw=rng.choice(CRM_REGION_VARIANTS[region]), crm_industry_raw=rng.choice(raws),
                market_segment="Commercial", account_owner_rep_id=rng.choice(self.reps_by_region[region]),
                is_existing_customer=False))
        # Inactive / on-hold billing accounts without activity.
        add_debtor(None, "AETH-US", "Dormant Test Account LLC", "INACTIVE")
        add_debtor(self.groups[3]["conglomerate_id"], "AETH-UK", f"{self.groups[3]['conglomerate_name'].split()[0]} Holdings UK", "ON_HOLD")
        self.group_by_id = {g["conglomerate_id"]: g for g in self.groups}

    # ------------------------------------------------------------------ deals
    def _deals_and_snapshots(self) -> None:
        R, rng = self.rows, self.rng
        self.deals: List[Dict[str, Any]] = []
        self.deal_splits: Dict[str, List[Dict[str, Any]]] = {}
        deal_seq, split_seq, snap_seq = 0, 0, 0
        tier_arr = {"Strategic_G100": (250_000, 900_000), "Enterprise": (90_000, 400_000), "Mid_Market": (30_000, 140_000)}
        for acct in R["pipeline_crm.prospect_accounts"]:
            gid = acct["conglomerate_id"]
            grp = self.group_by_id.get(gid) if gid else None
            n_deals = rng.randint(2, 5) if grp else rng.randint(1, 2)
            for _ in range(n_deals):
                deal_seq += 1
                did = f"DEAL-{deal_seq:05d}"
                created = date(2023, 6, 1) + timedelta(days=rng.randint(0, 900))
                close = created + timedelta(days=rng.randint(30, 200))
                lo, hi = tier_arr[grp["global_tier"]] if grp else (20_000, 90_000)
                arr_c = to_cents(round(rng.uniform(lo, hi), -2))
                term = rng.choice([12, 12, 24, 36])
                if close <= AS_OF:
                    stage = "Closed_Won" if (grp and rng.random() < 0.6) else "Closed_Lost"
                    if stage == "Closed_Lost" and rng.random() < 0.08:
                        stage = "Negotiation"  # stale pipeline: close date slipped into the past
                else:
                    days = (close - AS_OF).days
                    stage = "Commit" if days < 45 else "Negotiation" if days < 90 else "Best_Case" if days < 140 else "Qualification"
                bucket = STAGE_BUCKET[stage]
                prob = BUCKET_PROB[bucket]
                if stage not in ("Closed_Won", "Closed_Lost") and rng.random() < 0.2:
                    prob = max(5.0, min(95.0, prob + rng.choice([-15.0, -10.0, 10.0, 15.0])))
                deal_type = "New_Logo" if not grp else rng.choice(["New_Logo", "Expansion", "Expansion", "Renewal"])
                # product splits
                n_split = rng.randint(1, 3)
                products = rng.sample(SUBSCRIBABLE, n_split)
                weights = [rng.uniform(1, 3) for _ in products]
                splits, remaining = [], arr_c
                for k, (prod, w) in enumerate(zip(products, weights)):
                    share = remaining if k == n_split - 1 else int(arr_c * w / sum(weights))
                    remaining -= share if k < n_split - 1 else remaining
                    split_seq += 1
                    pl_crm = next(p[3] for p in PRODUCT_LINES if p[2] == prod[3])
                    splits.append(dict(deal_split_id=f"DSP-{split_seq:06d}", deal_id=did, catalog_item_id=prod[0],
                                       crm_product_family=pl_crm, split_arr_usd=amt(share),
                                       split_tcv_usd=amt(share * term // 12), term_months=term, _arr_c=share))
                arr_c = sum(s["_arr_c"] for s in splits)
                weighted_c = int(round(arr_c * prob / 100.0))
                deal = dict(
                    deal_id=did, crm_account_id=acct["crm_account_id"], owner_rep_id=acct["account_owner_rep_id"],
                    deal_title=f"{acct['crm_account_name']} - {deal_type.replace('_', ' ')} {close.year}",
                    deal_type=deal_type, pipeline_stage=stage, forecast_cat_code=bucket, win_probability_pct=prob,
                    deal_tcv_usd=amt(arr_c * term // 12), deal_arr_usd=amt(arr_c), weighted_arr_usd=amt(weighted_c),
                    target_close_date=close, close_fiscal_period=period_of(close), linked_agreement_id=None)
                self.deals.append(dict(deal, _created=created, _arr_c=arr_c, _term=term, _gid=gid, _acct=acct))
                self.deal_splits[did] = splits
                for s in splits:
                    R["pipeline_crm.deal_product_splits"].append({k: v for k, v in s.items() if not k.startswith("_")})
                # monthly history snapshots
                last_month = min(first_of_month(close), first_of_month(AS_OF))
                for m in month_range(max(created, WINDOW_START), last_month):
                    me = month_end(m)
                    is_final = (stage in ("Closed_Won", "Closed_Lost") and m == first_of_month(close)) or m == first_of_month(AS_OF)
                    if is_final:
                        s_stage, s_prob, s_arr = stage, prob, arr_c
                    else:
                        span = max(1, (close - created).days)
                        f = (me - created).days / span
                        s_stage = "Qualification" if f < 0.35 else "Best_Case" if f < 0.6 else "Negotiation" if f < 0.85 else "Commit"
                        s_prob = BUCKET_PROB[STAGE_BUCKET[s_stage]]
                        s_arr = int(arr_c * rng.uniform(0.85, 1.1)) // 100 * 100
                    label = s_stage
                    if m < date(2024, 7, 1) and s_stage in LEGACY_STAGE_LABELS and rng.random() < 0.25:
                        label = LEGACY_STAGE_LABELS[s_stage]
                    snap_seq += 1
                    R["pipeline_crm.deal_history_snapshots"].append(dict(
                        snapshot_row_id=f"SNP-{snap_seq:07d}", deal_id=did, as_of_month=period_of(m), snapshot_date=me,
                        stage_at_snapshot=label, forecast_cat_at_snapshot=STAGE_BUCKET[s_stage],
                        probability_at_snapshot=s_prob, arr_at_snapshot_usd=amt(s_arr),
                        weighted_arr_at_snapshot_usd=amt(int(round(s_arr * s_prob / 100.0))),
                        _stage=s_stage, _arr_c=s_arr, _prob=s_prob))
        R["pipeline_crm.deal_opportunities"] = [
            {k: v for k, v in d.items() if not k.startswith("_")} for d in self.deals]

    # ------------------------------------------------------------------ agreements
    def _agreements(self) -> None:
        R, rng = self.rows, self.rng
        R["agreements.repository_drives"] = [
            dict(drive_id="DRV-01", drive_label="Americas Contracts Vault", storage_provider="DocVault",
                 root_uri="vault://legal/americas/", owning_region_code="AMER", scan_enabled=True,
                 last_indexed_at=datetime(2025, 12, 30, 22, 0)),
            dict(drive_id="DRV-02", drive_label="EMEA Shared Agreements", storage_provider="NetworkShare",
                 root_uri="//fs-emea/legal/agreements/", owning_region_code="EMEA", scan_enabled=True,
                 last_indexed_at=datetime(2025, 12, 30, 23, 15)),
            dict(drive_id="DRV-03", drive_label="APAC Contract Bucket", storage_provider="ObjectStore",
                 root_uri="objstore://apac-legal/contracts/", owning_region_code="APAC", scan_enabled=True,
                 last_indexed_at=datetime(2025, 12, 29, 4, 30)),
            dict(drive_id="DRV-04", drive_label="Legacy Scanned Archive", storage_provider="NetworkShare",
                 root_uri="//fs-archive/scans/", owning_region_code=None, scan_enabled=False,
                 last_indexed_at=datetime(2024, 3, 2, 1, 0)),
        ]
        drive_for_region = {"AMER": "DRV-01", "EMEA": "DRV-02", "APAC": "DRV-03"}
        self.agreements: List[Dict[str, Any]] = []
        seq, cit = 0, 0
        msa_done = set()

        def new_agreement(**kw: Any) -> Dict[str, Any]:
            nonlocal seq
            seq += 1
            kw["agreement_id"] = f"AGR-{seq:05d}"
            self.agreements.append(kw)
            return kw

        for deal in self.deals:
            gid = deal["_gid"]
            if not gid:
                continue
            grp = self.group_by_id[gid]
            stage = deal["pipeline_stage"]
            if stage not in ("Closed_Won", "Commit", "Negotiation"):
                continue
            if stage == "Negotiation" and rng.random() > 0.3:
                continue
            debtors = [d for d in self.debtors_by_group[gid] if self.debtor_info[d]["account_status"] == "ACTIVE"]
            debtor = debtors[0] if (len(debtors) == 1 or rng.random() < 0.75) else debtors[-1]
            dinfo = self.debtor_info[debtor]
            ccy = dinfo["billing_currency"]
            close = deal["target_close_date"]
            start = add_months(first_of_month(close), 1)
            term = deal["_term"]
            expiry = add_months(start, term) - timedelta(days=1)
            region = next(e[2] for e in ENTITIES if e[0] == dinfo["legal_entity_code"])
            if gid not in msa_done and stage == "Closed_Won":
                msa_done.add(gid)
                new_agreement(drive_id=drive_for_region[region], file_title=f"{grp['conglomerate_name']} - Master Services Agreement.pdf",
                              agreement_type="MSA", execution_status="Fully_Signed", matched_group_id=gid,
                              raw_counterparty_name=grp["conglomerate_name"], effective_start_date=start - timedelta(days=30),
                              expiration_date=start + timedelta(days=5 * 365), contracted_tcv_amount=0.0,
                              annual_committed_arr=0.0, doc_currency_code=ccy, fx_rate_to_usd=self.rate(ccy, close),
                              contracted_tcv_usd=0.0, signed_date=close - timedelta(days=3),
                              _debtor=debtor, _deal=None, _arr_usd_c=0, _term=0, _start=start, _end=start, _primary_pl=None)
            fx = self.rate(ccy, close)
            arr_usd_c = deal["_arr_c"]
            arr_doc_c = int(round(arr_usd_c / fx))
            tcv_doc_c = arr_doc_c * term // 12
            tcv_usd_c = arr_usd_c * term // 12
            splits = self.deal_splits[deal["deal_id"]]
            primary = max(splits, key=lambda s: s["_arr_c"])
            primary_pl = next(c[3] for c in CATALOG if c[0] == primary["catalog_item_id"])
            if stage == "Closed_Won":
                if rng.random() < 0.04:
                    status = "Terminated"
                    end = add_months(start, rng.randint(3, max(4, term - 2))) - timedelta(days=1)
                elif expiry < AS_OF:
                    status, end = "Expired", expiry
                else:
                    status, end = "Fully_Signed", expiry
                signed = close + timedelta(days=rng.randint(0, 6))
            else:
                status = "Counterpart_Pending" if stage == "Commit" else "Draft"
                end, signed = start, None
            atype = {"New_Logo": "Order_Form", "Expansion": "Amendment", "Renewal": "Renewal"}[deal["deal_type"]]
            unmatched = rng.random() < 0.06
            aliases = [a["alias_name"] for a in R["agreements.counterparty_aliases"] if a["conglomerate_id"] == gid]
            agr = new_agreement(
                drive_id=drive_for_region[region] if rng.random() > 0.05 else "DRV-04",
                file_title=f"{grp['conglomerate_name']} - {atype.replace('_', ' ')} {close.year}-{close.month:02d}.pdf",
                agreement_type=atype, execution_status=status, matched_group_id=None if unmatched else gid,
                raw_counterparty_name=rng.choice(aliases) if aliases else grp["conglomerate_name"],
                effective_start_date=start, expiration_date=expiry, contracted_tcv_amount=amt(tcv_doc_c),
                annual_committed_arr=amt(arr_doc_c), doc_currency_code=ccy, fx_rate_to_usd=fx,
                contracted_tcv_usd=amt(tcv_usd_c), signed_date=signed,
                _debtor=debtor, _deal=deal, _arr_usd_c=arr_usd_c, _arr_doc_c=arr_doc_c, _term=term,
                _start=start, _end=end, _primary_pl=primary_pl, _splits=splits)
            if stage == "Closed_Won" and rng.random() > 0.06:
                deal["linked_agreement_id"] = agr["agreement_id"]
        # refresh deal rows with links
        links = {d["deal_id"]: d["linked_agreement_id"] for d in self.deals}
        for row in R["pipeline_crm.deal_opportunities"]:
            row["linked_agreement_id"] = links[row["deal_id"]]
        for a in self.agreements:
            R["agreements.agreement_files"].append({k: v for k, v in a.items() if not k.startswith("_")})
            if a["agreement_type"] == "MSA":
                continue
            for kind, prefix in [("PO_NUMBER", "PO"), ("SOW_CODE", "SOW"), ("MASTER_CONTRACT_NO", "MC")]:
                if kind != "PO_NUMBER" and rng.random() < 0.5:
                    continue
                cit += 1
                R["agreements.agreement_citations"].append(dict(
                    citation_id=f"CIT-{cit:05d}", agreement_id=a["agreement_id"], ref_kind=kind,
                    ref_token=f"{prefix}-{rng.randint(100000, 999999)}",
                    extraction_confidence=round(rng.uniform(0.62, 0.995), 4), page_number=rng.randint(1, 14)))
        signed_groups = {a["matched_group_id"] for a in self.agreements if a["execution_status"] in ("Fully_Signed", "Expired", "Terminated")}
        signed_groups.discard(None)  # unmatched agreements belong to no account
        for acct in R["pipeline_crm.prospect_accounts"]:
            acct["is_existing_customer"] = acct["conglomerate_id"] in signed_groups

    # ------------------------------------------------------------------ dimension bundles
    def bundle(self, dept: str, cc: str, pl: str, geo: str) -> str:
        sig = f"{dept}|{cc}|{pl}|{geo}"
        if sig not in self.bundles:
            bid = f"DSB-{len(self.bundles) + 1:04d}"
            self.bundles[sig] = bid
            self.rows["ledger_erp.tag_bundles"].append(dict(
                dim_bundle_id=bid, bundle_signature=sig, dept_code=dept, cost_center_code=cc,
                product_line_code=pl, geo_unit_code=geo))
        return self.bundles[sig]

    # ------------------------------------------------------------------ GL
    def voucher(self, entity: str, source: str, d: date, memo: str, lines: List[Tuple[str, int, str, str]],
                status: str = "POSTED") -> str:
        """lines: (account_number, signed_cents (+debit / -credit), bundle_id, narrative)."""
        if sum(l[1] for l in lines) != 0:
            raise AssertionError(f"Unbalanced voucher {memo}: {lines}")
        self._voucher_seq += 1
        vid = f"JV-{self._voucher_seq:07d}"
        self.rows["ledger_erp.journal_vouchers"].append(dict(
            voucher_id=vid, voucher_number=f"{entity[-2:]}-{d.year}-{self._voucher_seq:07d}", legal_entity_code=entity,
            journal_source=source, posting_date=d, fiscal_period=period_of(d), fiscal_year=d.year,
            posting_status=status, header_memo=memo))
        for seq, (acct, cents, bid, narr) in enumerate(lines, start=1):
            if cents == 0:
                continue
            self._posting_seq += 1
            self.rows["ledger_erp.journal_postings"].append(dict(
                posting_line_id=f"JL-{self._posting_seq:08d}", voucher_id=vid, line_seq=seq,
                account_id=self.account_ids[acct], dim_bundle_id=bid,
                debit_amount_usd=amt(cents) if cents > 0 else 0.0, credit_amount_usd=amt(-cents) if cents < 0 else 0.0,
                net_signed_usd=amt(cents), line_narrative=narr,
                _acct=acct, _cents=cents, _status=status, _period=period_of(d)))
        return vid

    # ------------------------------------------------------------------ invoices
    def _invoices_and_settlements(self) -> None:
        R, rng = self.rows, self.rng
        entity_meta = {e[0]: e for e in ENTITIES}
        terms_days = {"NET30": 30, "NET45": 45, "NET60": 60, "IMMED": 0}
        inv_seq, item_seq, ev_seq = 0, 0, 0
        for agr in self.agreements:
            if agr["agreement_type"] == "MSA" or agr["execution_status"] not in ("Fully_Signed", "Expired", "Terminated"):
                continue
            debtor = self.debtor_info[agr["_debtor"]]
            ent = debtor["legal_entity_code"]
            _code, _n, region, _f, ccy, _regime, tax_rate, geo = entity_meta[ent]
            gid = debtor["conglomerate_id"]
            grp = self.group_by_id.get(gid) if gid else None
            rating = grp["credit_risk_rating"] if grp else "BBB"
            last = min(agr["_end"], AS_OF)
            for m in month_range(max(agr["_start"], WINDOW_START), last):
                inv_seq += 1
                iid = f"INV-{inv_seq:06d}"
                fx = self.rate(ccy, m)
                lines = []
                for s in agr["_splits"]:
                    split_doc_c = int(round(s["_arr_c"] / agr["fx_rate_to_usd"])) // 12
                    lines.append((s["catalog_item_id"], split_doc_c))
                if any(c[3] == "ANL" for c in CATALOG if c[0] in [s["catalog_item_id"] for s in agr["_splits"]]) and rng.random() < 0.15:
                    lines.append(("PRD-006", to_cents(rng.uniform(400, 4800))))
                line_rows = []
                for ln, (pid, doc_c) in enumerate(lines, start=1):
                    prod = next(c for c in CATALOG if c[0] == pid)
                    qty = rng.randint(1, 40) if prod[4] != "Usage_Overage" else rng.randint(2000, 40000)
                    unit = round(doc_c / 100.0 / qty, 4)
                    doc_c = to_cents(qty * unit)
                    usd_c = int(round(doc_c * fx))
                    item_seq += 1
                    bid = self.bundle("D-REV", f"CC-{ent}", prod[3], geo)
                    line_rows.append(dict(
                        invoice_item_id=f"IIT-{item_seq:07d}", invoice_id=iid, line_number=ln, catalog_item_id=pid,
                        dim_bundle_id=bid, quantity=float(qty), unit_price_doc=unit, line_net_amount_doc=amt(doc_c),
                        line_net_amount_usd=amt(usd_c), service_start_date=m, service_end_date=month_end(m),
                        _doc_c=doc_c, _usd_c=usd_c, _acct=prod[5], _pl=prod[3]))
                sub_doc = sum(l["_doc_c"] for l in line_rows)
                sub_usd = sum(l["_usd_c"] for l in line_rows)
                tax_doc = int(round(sub_doc * tax_rate))
                tax_usd = int(round(tax_doc * fx))
                due = m + timedelta(days=terms_days[debtor["payment_terms_code"]])
                inv = InvoiceFact(
                    invoice_id=iid, debtor_id=debtor["debtor_id"], entity=ent,
                    agreement_id=None if rng.random() < 0.04 else agr["agreement_id"], invoice_date=m, due_date=due,
                    currency=ccy, fx=fx, lines=line_rows, subtotal_doc=sub_doc, tax_doc=tax_doc, subtotal_usd=sub_usd,
                    tax_usd=tax_usd, gross_doc=sub_doc + tax_doc, gross_usd=sub_usd + tax_usd)
                inv.region, inv.gid, inv.agr = region, gid, agr  # type: ignore[attr-defined]
                self._settle(inv, rating)
                self.invoices.append(inv)

        # Emit rows + GL
        for inv in self.invoices:
            first_bundle = inv.lines[0]["dim_bundle_id"]
            if inv.status != "VOID":
                gl = [("1100", inv.gross_usd, first_bundle, f"AR for {inv.invoice_id}")]
                for l in inv.lines:
                    gl.append((l["_acct"], -l["_usd_c"], l["dim_bundle_id"], f"Revenue {l['catalog_item_id']}"))
                gl.append(("2200", -inv.tax_usd, first_bundle, "Indirect tax"))
                vid = self.voucher(inv.entity, "AR_SUBLEDGER", inv.invoice_date, f"Invoice {inv.invoice_id}", gl)
                ev_seq += 1
                R["ledger_erp.receivable_ledger_events"].append(dict(
                    ar_event_id=f"ARE-{ev_seq:07d}", debtor_id=inv.debtor_id, applied_invoice_id=inv.invoice_id,
                    event_type="INVOICE_POST", event_date=inv.invoice_date, fiscal_period=period_of(inv.invoice_date),
                    signed_amount_doc=amt(inv.gross_doc), signed_amount_usd=amt(inv.gross_usd),
                    settlement_ref_code=None, gl_voucher_id=vid, is_settled=inv.open_usd == 0))
            for ev in inv.events:
                if ev["type"] == "CASH_RECEIPT":
                    pay_rate = self.rate(inv.currency, ev["date"])
                    cash_c = int(round(ev["doc_c"] * pay_rate))
                    fx_diff = ev["usd_c"] - cash_c  # positive => loss
                    lines = [("1000", cash_c, first_bundle, "Cash received"),
                             ("1100", -ev["usd_c"], first_bundle, f"Clear AR {inv.invoice_id}")]
                    if fx_diff:
                        lines.append(("7100", fx_diff, first_bundle, "Realized FX on settlement"))
                    vid = self.voucher(inv.entity, "AR_SUBLEDGER", ev["date"], f"Receipt {ev['ref']}", lines)
                elif ev["type"] == "CREDIT_MEMO":
                    vid = self.voucher(inv.entity, "AR_SUBLEDGER", ev["date"], f"Credit memo {ev['ref']}",
                                       [("4900", ev["usd_c"], first_bundle, "Customer credit"),
                                        ("1100", -ev["usd_c"], first_bundle, f"Credit AR {inv.invoice_id}")])
                else:
                    vid = self.voucher(inv.entity, "AR_SUBLEDGER", ev["date"], f"Write-off {inv.invoice_id}",
                                       [("6900", ev["usd_c"], self.bundle("D-GA", f"CC-{inv.entity}", "CORP", ERP_GEO[inv.region]), "Bad debt"),
                                        ("1100", -ev["usd_c"], first_bundle, f"Write-off AR {inv.invoice_id}")])
                ev_seq += 1
                R["ledger_erp.receivable_ledger_events"].append(dict(
                    ar_event_id=f"ARE-{ev_seq:07d}", debtor_id=inv.debtor_id, applied_invoice_id=inv.invoice_id,
                    event_type=ev["type"], event_date=ev["date"], fiscal_period=period_of(ev["date"]),
                    signed_amount_doc=amt(-ev["doc_c"]), signed_amount_usd=amt(-ev["usd_c"]),
                    settlement_ref_code=ev["ref"], gl_voucher_id=vid, is_settled=inv.open_usd == 0))
            R["ledger_erp.sales_invoices"].append(dict(
                invoice_id=inv.invoice_id, invoice_number=f"AE-{inv.entity[-2:]}-{inv.invoice_date.year}-{inv.invoice_id[-6:]}",
                debtor_id=inv.debtor_id, legal_entity_code=inv.entity, agreement_id=inv.agreement_id,
                invoice_date=inv.invoice_date, due_date=inv.due_date, fiscal_period=period_of(inv.invoice_date),
                doc_currency=inv.currency, fx_rate_to_usd=inv.fx, subtotal_amount_doc=amt(inv.subtotal_doc),
                tax_amount_doc=amt(inv.tax_doc), gross_total_doc=amt(inv.gross_doc), subtotal_amount_usd=amt(inv.subtotal_usd),
                tax_amount_usd=amt(inv.tax_usd), gross_total_usd=amt(inv.gross_usd), open_balance_usd=amt(inv.open_usd),
                settlement_status=inv.status))
            for l in inv.lines:
                R["ledger_erp.sales_invoice_items"].append({k: v for k, v in l.items() if not k.startswith("_")})

    def _settle(self, inv: InvoiceFact, rating: str) -> None:
        rng = self.rng
        lo, hi = {"AAA": (-5, 8), "AA": (-5, 12), "A": (0, 25), "BBB": (0, 35), "WATCH": (10, 90)}[rating]
        pay_date = inv.due_date + timedelta(days=rng.randint(lo, hi))
        r = rng.random()
        ref_n = inv.invoice_id[-6:]
        events: List[Dict[str, Any]] = []
        remaining_doc, remaining_usd = inv.gross_doc, inv.gross_usd

        def take(doc_c: int, kind: str, d: date, ref: str) -> None:
            nonlocal remaining_doc, remaining_usd
            if d > AS_OF or doc_c <= 0:
                return
            doc_c = min(doc_c, remaining_doc)
            usd_c = remaining_usd if doc_c == remaining_doc else int(round(doc_c * inv.fx))
            remaining_doc -= doc_c
            remaining_usd -= usd_c
            events.append(dict(type=kind, date=d, doc_c=doc_c, usd_c=usd_c, ref=ref))

        if r < 0.02:
            inv.status, inv.open_usd = "VOID", 0
            return
        if r < 0.05:
            take(inv.gross_doc, "CREDIT_MEMO", inv.invoice_date + timedelta(days=rng.randint(5, 25)), f"CM-{ref_n}")
        elif r < 0.12:
            take(int(inv.gross_doc * rng.uniform(0.1, 0.3)), "CREDIT_MEMO", inv.invoice_date + timedelta(days=rng.randint(5, 20)), f"CM-{ref_n}")
            take(remaining_doc, "CASH_RECEIPT", pay_date, f"WIRE-{ref_n}")
        elif r < 0.22:
            take(int(inv.gross_doc * rng.uniform(0.3, 0.8)), "CASH_RECEIPT", pay_date, f"WIRE-{ref_n}A")
        elif r < 0.24:
            take(int(inv.gross_doc * rng.uniform(0.2, 0.6)), "CASH_RECEIPT", pay_date, f"WIRE-{ref_n}A")
            if events:
                take(remaining_doc, "WRITE_OFF", pay_date + timedelta(days=120), f"WO-{ref_n}")
        else:
            take(inv.gross_doc, "CASH_RECEIPT", pay_date, f"WIRE-{ref_n}")
        inv.events = events
        inv.open_usd = remaining_usd
        kinds = {e["type"] for e in events}
        if remaining_usd == 0:
            if kinds == {"CREDIT_MEMO"}:
                inv.status = "CREDITED"
            elif "WRITE_OFF" in kinds:
                inv.status = "WRITTEN_OFF"
            else:
                inv.status = "PAID"
                if inv.invoice_date < date(2024, 4, 1) and rng.random() < 0.35:
                    inv.status = "CLOSED"  # legacy migration status code meaning PAID
        elif events:
            inv.status = "PARTIALLY_PAID"
        else:
            inv.status = "OVERDUE" if inv.due_date < AS_OF else "OPEN"

    # ------------------------------------------------------------------ opex
    def _operating_expenses(self) -> None:
        rng = self.rng
        scale = {"AETH-US": 1.0, "AETH-UK": 0.45, "AETH-DE": 0.35, "AETH-SG": 0.25}
        rev_by_ent_period: Dict[Tuple[str, str], int] = defaultdict(int)
        for inv in self.invoices:
            if inv.status != "VOID":
                rev_by_ent_period[(inv.entity, period_of(inv.invoice_date))] += inv.subtotal_usd
        self.opex_actuals: Dict[Tuple[str, str, str], int] = defaultdict(int)  # (period, region, cost_node)
        for ent, _n, region, *_ in ENTITIES:
            geo = ERP_GEO[region]
            for i, m in enumerate(month_range(WINDOW_START, AS_OF)):
                growth = 1 + 0.012 * i
                end = month_end(m)
                payroll = [("6100", "D-RND", 420_000), ("6200", "D-SM", 310_000), ("6300", "D-GA", 180_000)]
                lines = []
                for acct, dept, base in payroll:
                    c = to_cents(base * scale[ent] * growth * rng.uniform(0.97, 1.03))
                    lines.append((acct, c, self.bundle(dept, f"CC-{ent}", "CORP", geo), "Monthly payroll"))
                    self.opex_actuals[(period_of(m), region, self.cost_leaf_for_account[acct])] += c
                lines.append(("1000", -sum(l[1] for l in lines), self.bundle("D-GA", f"CC-{ent}", "CORP", geo), "Payroll funding"))
                self.voucher(ent, "PAYROLL", end, f"Payroll {period_of(m)} {ent}", lines)
                ap = [("6400", "D-SM", to_cents(85_000 * scale[ent] * growth * rng.uniform(0.8, 1.2))),
                      ("6500", "D-SM", to_cents(22_000 * scale[ent] * rng.uniform(0.7, 1.3))),
                      ("5000", "D-OPS", int(rev_by_ent_period[(ent, period_of(m))] * rng.uniform(0.16, 0.2)))]
                lines = []
                for acct, dept, c in ap:
                    lines.append((acct, c, self.bundle(dept, f"CC-{ent}", "CORP", geo), "Vendor invoices"))
                    if acct in self.cost_leaf_for_account:
                        self.opex_actuals[(period_of(m), region, self.cost_leaf_for_account[acct])] += c
                lines.append(("1000", -sum(l[1] for l in lines), self.bundle("D-GA", f"CC-{ent}", "CORP", geo), "AP payments"))
                self.voucher(ent, "AP_EXPENSE", end - timedelta(days=2), f"AP expenses {period_of(m)} {ent}", lines)
        # Non-posted vouchers: must be excluded from posted ledger analysis.
        b = self.bundle("D-GA", "CC-AETH-US", "CORP", "GEO-NA")
        for k in range(3):
            c = to_cents(rng.uniform(15_000, 60_000))
            self.voucher("AETH-US", "MANUAL_ADJ", date(2025, 12, 31), f"Year-end accrual draft #{k + 1}",
                         [("6300", c, b, "Accrued bonus (draft)"), ("2300", -c, b, "Accrual")], status="PENDING_APPROVAL")
        rb = self.bundle("D-REV", "CC-AETH-US", "CLD", "GEO-NA")
        for k in range(2):
            c = to_cents(rng.uniform(80_000, 150_000))
            self.voucher("AETH-US", "MANUAL_ADJ", date(2025, 6, 30), f"Revenue reclass (reversed) #{k + 1}",
                         [("2100", c, rb, "Reclass from deferred"), ("4010", -c, rb, "Reclass to revenue")], status="REVERSED")

    def _dimension_tables(self) -> None:
        R = self.rows
        axes = [("AX-01", "DEPT", "Department", True), ("AX-02", "COST_CTR", "Cost Center", True),
                ("AX-03", "PROD_LINE", "Product Line", False), ("AX-04", "GEO_UNIT", "Geographic Unit", True)]
        for a in axes:
            R["ledger_erp.analytic_axes"].append(dict(axis_id=a[0], axis_code=a[1], axis_name=a[2], is_required_on_gl=a[3]))
        labels = {"D-REV": "Revenue Operations", "D-RND": "Research & Development", "D-SM": "Sales & Marketing",
                  "D-GA": "General & Administrative", "D-OPS": "Cloud Operations", "CLD": "Cloud", "ANL": "Analytics",
                  "SEC": "Security", "ADV": "Advisory", "CORP": "Corporate", "GEO-NA": "North America",
                  "GEO-EU": "Europe", "GEO-AP": "Asia Pacific"}
        member_ids: Dict[Tuple[str, str], str] = {}
        positions = [("AX-01", 0), ("AX-02", 1), ("AX-03", 2), ("AX-04", 3)]
        for sig in self.bundles:
            parts = sig.split("|")
            for ax, pos in positions:
                key = (ax, parts[pos])
                if key not in member_ids:
                    mid = f"AXM-{len(member_ids) + 1:04d}"
                    member_ids[key] = mid
                    R["ledger_erp.analytic_axis_members"].append(dict(
                        member_id=mid, axis_id=ax, member_code=parts[pos],
                        member_label=labels.get(parts[pos], parts[pos].replace("CC-", "Cost Center ")), is_active=True))
        n = 0
        for sig, bid in self.bundles.items():
            parts = sig.split("|")
            for ax, pos in positions:
                n += 1
                R["ledger_erp.tag_bundle_members"].append(dict(
                    bundle_member_id=f"DBM-{n:05d}", dim_bundle_id=bid, axis_id=ax, member_id=member_ids[(ax, parts[pos])]))

    # ------------------------------------------------------------------ revenue facts (shared by budget & reporting)
    def revenue_cells(self) -> Dict[Tuple[str, str, str, str], int]:
        """Recognized revenue by (period, region, product line node code, industry node code) in cents."""
        pl_node = {p[2]: p[0] for p in PRODUCT_LINES}
        cells: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
        for inv in self.invoices:
            if inv.status == "VOID":
                continue
            grp = self.group_by_id.get(inv.gid) if inv.gid else None  # type: ignore[attr-defined]
            ind = grp["primary_industry_code"] if grp else "IND_ALL"
            for l in inv.lines:
                cells[(period_of(inv.invoice_date), inv.region, pl_node[l["_pl"]], ind)] += l["_usd_c"]  # type: ignore[attr-defined]
            for ev in inv.events:
                if ev["type"] == "CREDIT_MEMO":
                    cells[(period_of(ev["date"]), inv.region, pl_node[inv.lines[0]["_pl"]], ind)] -= ev["usd_c"]  # type: ignore[attr-defined]
        return cells

    def _budget(self) -> None:
        R, rng = self.rows, self.rng
        cells = self.revenue_cells()
        self.budget_cells: Dict[Tuple[str, str, str, str, str], int] = {}
        seq = 0

        def line(scn: str, per: str, metric: str, region: str, pl: str, ind: str, cost: Optional[str], cents: int) -> None:
            nonlocal seq
            seq += 1
            R["fpna_plan.financial_targets"].append(dict(
                target_line_id=f"TGT-{seq:06d}", plan_scenario=scn, fiscal_period=per, fiscal_year=int(per[:4]),
                metric_category=metric, region_node_id=self.node_id[("REGION", region)],
                product_line_node_id=self.node_id[("PRODUCT_LINE", pl)], industry_node_id=self.node_id[("INDUSTRY", ind)],
                cost_node_id=cost, target_amount_usd=amt(cents)))
            if metric == "REVENUE_TARGET":
                self.budget_cells[(scn, per, region, pl, ind)] = cents

        keys = sorted({(r, pl, ind) for (_p, r, pl, ind) in cells})
        for year, scn in [(2024, "FY2024_APPROVED_BUDGET"), (2025, "FY2025_APPROVED_BUDGET")]:
            for m in month_range(date(year, 1, 1), date(year, 12, 1)):
                per = period_of(m)
                for (r, pl, ind) in keys:
                    act = cells.get((per, r, pl, ind), 0)
                    if act <= 0:
                        continue
                    line(scn, per, "REVENUE_TARGET", r, pl, ind, None, int(act * rng.uniform(0.9, 1.12)) // 100 * 100)
        for m in month_range(date(2025, 7, 1), date(2025, 12, 1)):
            per = period_of(m)
            for (r, pl, ind) in keys:
                act = cells.get((per, r, pl, ind), 0)
                if act > 0:
                    line("FY2025_Q2_REFORECAST", per, "REVENUE_TARGET", r, pl, ind, None, int(act * rng.uniform(0.96, 1.05)) // 100 * 100)
        for m in month_range(date(2026, 1, 1), date(2026, 12, 1)):
            per, prior = period_of(m), period_of(date(2025, m.month, 1))
            for (r, pl, ind) in keys:
                act = cells.get((prior, r, pl, ind), 0)
                if act > 0:
                    line("FY2026_BASELINE_PLAN", per, "REVENUE_TARGET", r, pl, ind, None, int(act * rng.uniform(1.1, 1.2)) // 100 * 100)
        for (per, region, cost_node), c in sorted(self.opex_actuals.items()):
            scn = f"FY{per[:4]}_APPROVED_BUDGET"
            line(scn, per, "OPEX_BUDGET", region, "PL_ALL", "IND_ALL", cost_node, int(c * rng.uniform(0.92, 1.08)) // 100 * 100)
        quota_by_region: Dict[str, float] = defaultdict(float)
        for rep in R["pipeline_crm.quota_carriers"]:
            region = self.rep_region.get(rep["rep_id"])
            if region:
                quota_by_region[region] += rep["annual_quota_usd"]
        for year in (2024, 2025):
            for m in month_range(date(year, 1, 1), date(year, 12, 1)):
                for region, q in sorted(quota_by_region.items()):
                    line(f"FY{year}_APPROVED_BUDGET", period_of(m), "NEW_ARR_QUOTA", region, "PL_ALL", "IND_ALL", None, to_cents(q / 12))

    # ------------------------------------------------------------------ reporting
    def _reporting_snapshots(self) -> None:
        R = self.rows
        cells = self.revenue_cells()
        pl_node = {p[2]: p[0] for p in PRODUCT_LINES}
        crm_region = {v: k for k, vs in CRM_REGION_VARIANTS.items() for v in vs}
        crm_industry = {raw: code for code, _l, raws in INDUSTRIES for raw in raws}
        crm_pl = {p[3]: p[0] for p in PRODUCT_LINES}
        acct_by_id = {a["crm_account_id"]: a for a in R["pipeline_crm.prospect_accounts"]}
        deal_by_id = {d["deal_id"]: d for d in self.deals}
        snaps_by_month: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for s in R["pipeline_crm.deal_history_snapshots"]:
            snaps_by_month[s["as_of_month"]].append(s)

        def pipeline_cells(per: str) -> Dict[Tuple[str, str, str], Tuple[int, int, Dict[str, Any]]]:
            out: Dict[Tuple[str, str, str], List[int]] = defaultdict(lambda: [0, 0])
            for s in snaps_by_month.get(per, []):
                if s["_stage"] in ("Closed_Won", "Closed_Lost"):
                    continue
                deal = deal_by_id[s["deal_id"]]
                acct = acct_by_id[deal["crm_account_id"]]
                region = crm_region[acct["crm_region_raw"]]
                ind = crm_industry.get(acct["crm_industry_raw"], "IND_ALL")
                splits = self.deal_splits[deal["deal_id"]]
                total = sum(sp["_arr_c"] for sp in splits)
                for sp in splits:
                    share_arr = s["_arr_c"] * sp["_arr_c"] // total
                    share_w = int(round(share_arr * s["_prob"] / 100.0))
                    key = (region, crm_pl[sp["crm_product_family"]], ind)
                    out[key][0] += share_arr
                    out[key][1] += share_w
            return out

        seq = 0
        for m in month_range(WINDOW_START, AS_OF):
            per, pend = period_of(m), month_end(m)
            contracted: Dict[Tuple[str, str, str], int] = defaultdict(int)
            for a in self.agreements:
                if a["agreement_type"] == "MSA" or a["execution_status"] not in ("Fully_Signed", "Expired", "Terminated"):
                    continue
                if not (a["_start"] <= pend <= a["_end"]):
                    continue
                d = self.debtor_info[a["_debtor"]]
                region = next(e[2] for e in ENTITIES if e[0] == d["legal_entity_code"])
                grp = self.group_by_id.get(d["conglomerate_id"]) if d["conglomerate_id"] else None
                ind = grp["primary_industry_code"] if grp else "IND_ALL"
                contracted[(region, pl_node[a["_primary_pl"]], ind)] += a["_arr_usd_c"]
            pipe = pipeline_cells(per)
            keys = {(r, pl, ind) for (p, r, pl, ind) in cells if p == per} | set(contracted) | set(pipe)
            for (r, pl, ind) in sorted(keys):
                rec = cells.get((per, r, pl, ind), 0)
                bud = self.budget_cells.get((f"FY{per[:4]}_APPROVED_BUDGET", per, r, pl, ind), 0)
                pu, pw = pipe.get((r, pl, ind), (0, 0))
                seq += 1
                R["exec_reporting.monthly_revenue_posture"].append(dict(
                    posture_id=f"MRP-{seq:06d}", snapshot_period=per, region_node_id=self.node_id[("REGION", r)],
                    product_line_node_id=self.node_id[("PRODUCT_LINE", pl)], industry_node_id=self.node_id[("INDUSTRY", ind)],
                    recognized_gl_revenue_usd=amt(rec), contracted_arr_usd=amt(contracted.get((r, pl, ind), 0)),
                    open_pipeline_unweighted_usd=amt(pu), open_pipeline_weighted_usd=amt(pw),
                    budget_target_usd=amt(bud), variance_to_budget_usd=amt(rec - bud)))
        # forward-year outlook (FY2026) observed monthly through 2025
        seq = 0
        y_start, y_end = date(2026, 1, 1), date(2026, 12, 31)
        baseline: Dict[Tuple[str, str], int] = defaultdict(int)
        for (scn, _per, r, pl, _ind), c in self.budget_cells.items():
            if scn == "FY2026_BASELINE_PLAN":
                baseline[(r, pl)] += c
        for m in month_range(date(2025, 1, 1), AS_OF):
            per, pend = period_of(m), month_end(m)
            locked: Dict[Tuple[str, str], int] = defaultdict(int)
            for a in self.agreements:
                if a["agreement_type"] == "MSA" or a["execution_status"] not in ("Fully_Signed", "Expired", "Terminated"):
                    continue
                if a["signed_date"] is None or a["signed_date"] > pend:
                    continue
                months = sum(1 for mm in month_range(y_start, y_end) if a["_start"] <= mm <= a["_end"])
                if months:
                    d = self.debtor_info[a["_debtor"]]
                    region = next(e[2] for e in ENTITIES if e[0] == d["legal_entity_code"])
                    locked[(region, pl_node[a["_primary_pl"]])] += a["_arr_usd_c"] * months // 12
            weighted: Dict[Tuple[str, str], int] = defaultdict(int)
            for s in snaps_by_month.get(per, []):
                if s["_stage"] in ("Closed_Won", "Closed_Lost"):
                    continue
                deal = deal_by_id[s["deal_id"]]
                close = deal["target_close_date"]
                if close > y_end:
                    continue
                frac_months = 12 if close < y_start else max(0, 12 - close.month)
                acct = acct_by_id[deal["crm_account_id"]]
                region = crm_region[acct["crm_region_raw"]]
                splits = self.deal_splits[deal["deal_id"]]
                total = sum(sp["_arr_c"] for sp in splits)
                for sp in splits:
                    w = int(round(s["_arr_c"] * sp["_arr_c"] // total * s["_prob"] / 100.0))
                    weighted[(region, crm_pl[sp["crm_product_family"]])] += w * frac_months // 12
            for (r, pl) in sorted(set(locked) | set(weighted) | set(baseline)):
                lk, wt = locked.get((r, pl), 0), weighted.get((r, pl), 0)
                seq += 1
                R["exec_reporting.horizon_revenue_outlook"].append(dict(
                    outlook_id=f"HRO-{seq:05d}", as_of_period=per, forward_fiscal_year=2026,
                    region_node_id=self.node_id[("REGION", r)], product_line_node_id=self.node_id[("PRODUCT_LINE", pl)],
                    locked_contracted_rev_usd=amt(lk), weighted_pipeline_addition_usd=amt(wt),
                    combined_outlook_usd=amt(lk + wt), forward_baseline_plan_usd=amt(baseline.get((r, pl), 0))))

    # ------------------------------------------------------------------ app / ops
    def _app_and_ops(self) -> None:
        R, rng = self.rows, self.rng
        roles = ["Finance_Controller", "RevOps_Analyst", "Executive", "Auditor"]
        ops = []
        for i in range(10):
            tenant = "TEN-AETH-FIN" if i < 6 else "TEN-AETH-SALES"
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            ops.append(dict(operator_id=f"OPR-{i + 1:03d}", email_handle=f"{name.lower().replace(' ', '.')}@aethelgard.example",
                            display_name=name, security_role=roles[i % 4], tenant_org_id=tenant, is_active=i != 9,
                            created_at=datetime(2024, 1, 15) + timedelta(days=rng.randint(0, 400))))
        R["portal_app.platform_operators"] = ops
        topics = ["Open AR by region", "Q3 pipeline coverage", "Budget variance FY2025", "Overdue invoices EMEA",
                  "Contracted ARR trend", "Credit memo analysis", "Top customers by billing", "Forward year outlook"]
        u = 0
        for t in range(20):
            op = rng.choice(ops)
            start = datetime(2025, 6, 1) + timedelta(hours=rng.randint(0, 4000))
            tid = f"THR-{t + 1:04d}"
            n_msgs = rng.randint(2, 6)
            R["portal_app.inquiry_threads"].append(dict(
                thread_id=tid, operator_id=op["operator_id"], tenant_org_id=op["tenant_org_id"],
                thread_topic=rng.choice(topics), started_at=start, last_activity_at=start + timedelta(minutes=3 * n_msgs)))
            for k in range(n_msgs):
                u += 1
                role = "user" if k % 2 == 0 else "assistant"
                cites = [] if role == "user" else rng.sample(["ledger_erp.sales_invoices", "pipeline_crm.deal_opportunities",
                                                              "exec_reporting.monthly_revenue_posture", "fpna_plan.financial_targets"], 2)
                R["portal_app.thread_utterances"].append(dict(
                    utterance_id=f"UTT-{u:05d}", thread_id=tid, speaker_role=role,
                    message_body="Question about the selected topic." if role == "user" else "Answer with cited evidence.",
                    cited_objects_json=json.dumps(cites), prompt_tokens=rng.randint(200, 4000) if role == "assistant" else 0,
                    completion_tokens=rng.randint(80, 900) if role == "assistant" else 0,
                    emitted_at=start + timedelta(minutes=3 * k)))
        connectors = [("ERP_LEDGERCORE", "ledger_erp"), ("CRM_PIPELINEHUB", "pipeline_crm"),
                      ("DOC_VAULTSCAN", "agreements"), ("FPNA_PLANGRID", "fpna_plan")]
        b = 0
        for day in range(0, 365, 7):
            for conn, schema in connectors:
                b += 1
                started = datetime(2025, 1, 1, 2, 0) + timedelta(days=day, minutes=rng.randint(0, 50))
                extracted = rng.randint(500, 20000)
                roll = rng.random()
                status = "FAILED" if roll < 0.04 else "PARTIAL_WARNING" if roll < 0.15 else "SUCCEEDED"
                rejected = 0 if status == "SUCCEEDED" else extracted if status == "FAILED" else rng.randint(1, 60)
                diag = None if status == "SUCCEEDED" else (
                    "Connection timeout after 3 retries" if status == "FAILED" else
                    f"{rejected} rows rejected: missing taxonomy crosswalk for raw value")
                R["data_ops.ingestion_batches"].append(dict(
                    batch_id=f"BAT-{b:05d}", upstream_connector=conn, target_schema_name=schema, run_status=status,
                    records_extracted=extracted, records_loaded=extracted - rejected, rejected_rows_count=rejected,
                    error_diagnostic=diag, started_at=started, completed_at=started + timedelta(minutes=rng.randint(2, 40))))


def strip_private(frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    return {k: v[[c for c in v.columns if not c.startswith("_")]] for k, v in frames.items()}
