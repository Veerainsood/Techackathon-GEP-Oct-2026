"""State-based policies for pending invoices and pending contracts, by region.

A policy constrains one state variable of one collection in one region (or all regions): allowed values for a category, a
minimum/maximum for a number, and the values (or amount) that need a person. When several policies apply to a record, the
effective constraint is their intersection. A new or changed policy whose intersection with the existing ones is empty is
rejected and the conflicting policies are named, so no state variable is ever left without an allowed value.

One evaluator, two paths:
  * action path (autopilot): evaluate the state a proposed action would produce -> blocked / hitl / allowed / no_policy
  * state check: re-evaluate every pending record (at start, after any policy change, after a sync event on the scope tables),
    keep the last state per record in ``policy_states`` (so changes show as before -> after) and record new violations.

State is read through the gateway, read-only. Policies never change business data.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Dict, List, Literal, Optional, Tuple

from psycopg2.extras import Json
from pydantic import BaseModel, Field

from backend.gateway.registry import AccessContext
from backend.pg_pool import shared_pool

REGIONS = {"AMER": "Americas", "EMEA": "Europe, Middle East & Africa", "APAC": "Asia-Pacific"}
Collection = Literal["pending_invoices", "pending_contracts"]
SCOPES: Dict[str, Dict[str, Any]] = {
    "pending_invoices": {
        "label": "Pending invoices",
        "table": "ledger_erp.sales_invoices",
        "sql": """SELECT i.invoice_id AS record_id, i.invoice_number AS reference, c.conglomerate_id AS customer_id,
                         c.conglomerate_name AS customer, j.region_code AS region, i.settlement_status, i.open_balance_usd,
                         i.gross_total_usd, i.doc_currency, i.due_date
                  FROM ledger_erp.sales_invoices i
                  JOIN ledger_erp.receivable_debtors d ON d.debtor_id = i.debtor_id
                  JOIN master_ref.client_conglomerates c ON c.conglomerate_id = d.conglomerate_id
                  LEFT JOIN master_ref.entity_jurisdictions j ON j.legal_entity_code = i.legal_entity_code
                  WHERE i.settlement_status IN ('OPEN', 'PARTIALLY_PAID', 'OVERDUE')""",
        "fields": {"settlement_status": ("categorical", "Settlement status", "SELECT DISTINCT settlement_status AS v FROM ledger_erp.sales_invoices"),
                   "open_balance_usd": ("numeric", "Open balance (USD)", None),
                   "gross_total_usd": ("numeric", "Invoice total (USD)", None),
                   "doc_currency": ("categorical", "Currency", "SELECT DISTINCT doc_currency AS v FROM ledger_erp.sales_invoices")},
    },
    "pending_contracts": {
        "label": "Pending contracts",
        "table": "agreements.agreement_files",
        "sql": """SELECT a.agreement_id AS record_id, a.file_title AS reference, c.conglomerate_id AS customer_id,
                         c.conglomerate_name AS customer, c.primary_region_code AS region, a.execution_status, a.agreement_type,
                         a.doc_currency_code, a.contracted_tcv_usd, a.annual_committed_arr
                  FROM agreements.agreement_files a
                  JOIN master_ref.client_conglomerates c ON c.conglomerate_id = a.matched_group_id
                  WHERE a.execution_status IN ('Draft', 'Counterpart_Pending')""",
        "fields": {"execution_status": ("categorical", "Execution status", "SELECT DISTINCT execution_status AS v FROM agreements.agreement_files"),
                   "agreement_type": ("categorical", "Agreement type", "SELECT DISTINCT agreement_type AS v FROM agreements.agreement_files"),
                   "doc_currency_code": ("categorical", "Currency", "SELECT DISTINCT doc_currency_code AS v FROM agreements.agreement_files"),
                   "contracted_tcv_usd": ("numeric", "Contract value, TCV (USD)", None),
                   "annual_committed_arr": ("numeric", "Annual committed revenue (USD)", None)},
    },
}

DDL = """
CREATE SCHEMA IF NOT EXISTS backend_runtime;
CREATE TABLE IF NOT EXISTS backend_runtime.policies (
    policy_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, collection TEXT NOT NULL, region TEXT NOT NULL,
    variable TEXT NOT NULL, allowed_values JSONB, min_value NUMERIC, max_value NUMERIC, hitl_values JSONB NOT NULL DEFAULT '[]',
    hitl_above NUMERIC, active BOOLEAN NOT NULL DEFAULT TRUE, owner TEXT NOT NULL DEFAULT 'Finance', version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by TEXT
);
CREATE TABLE IF NOT EXISTS backend_runtime.policy_states (
    tenant_id TEXT NOT NULL, collection TEXT NOT NULL, record_id TEXT NOT NULL, region TEXT, reference TEXT, customer TEXT,
    state JSONB NOT NULL, previous_state JSONB, decision TEXT NOT NULL, violations JSONB NOT NULL DEFAULT '[]',
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id, collection, record_id)
);
CREATE TABLE IF NOT EXISTS backend_runtime.policy_events (
    event_id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, kind TEXT NOT NULL, collection TEXT, record_id TEXT, title TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS policy_events_recent_idx ON backend_runtime.policy_events (tenant_id, created_at DESC);
"""

# Demo defaults, created the first time a tenant's policies are read (only when the tenant has none).
DEFAULT_POLICIES = [
    {"name": "Contract signature needs a person", "collection": "pending_contracts", "region": "ALL", "variable": "execution_status",
     "allowed_values": ["Draft", "Counterpart_Pending", "Fully_Signed"], "hitl_values": ["Fully_Signed"]},
    {"name": "Americas contracts up to $1M", "collection": "pending_contracts", "region": "AMER", "variable": "contracted_tcv_usd",
     "max_value": 1_000_000},
    {"name": "Invoice settlement states", "collection": "pending_invoices", "region": "ALL", "variable": "settlement_status",
     "allowed_values": ["OPEN", "PARTIALLY_PAID", "OVERDUE", "PAID"]},
    {"name": "Large invoices need a person", "collection": "pending_invoices", "region": "ALL", "variable": "gross_total_usd",
     "hitl_above": 50_000},
    {"name": "Asia-Pacific open balance up to $30k", "collection": "pending_invoices", "region": "APAC", "variable": "open_balance_usd",
     "max_value": 30_000},
]


class PolicyIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    collection: Collection
    region: Literal["ALL", "AMER", "EMEA", "APAC"] = "ALL"
    variable: str
    allowed_values: Optional[List[str]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    hitl_values: List[str] = Field(default_factory=list)
    hitl_above: Optional[float] = None
    active: bool = True
    owner: str = "Finance"


class PolicyRejected(ValueError):
    def __init__(self, message: str, conflicts: Optional[List[str]] = None):
        super().__init__(message)
        self.conflicts = conflicts or []


def _f(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def describe(p: Dict[str, Any]) -> str:
    kind, label, _ = SCOPES[p["collection"]]["fields"][p["variable"]]
    where = "all regions" if p["region"] == "ALL" else REGIONS[p["region"]]
    parts = []
    if kind == "categorical":
        parts.append(f"{label} must be one of {', '.join(p['allowed_values'] or [])}")
        if p.get("hitl_values"):
            parts.append(f"a person approves {', '.join(p['hitl_values'])}")
    else:
        if p.get("min_value") is not None and p.get("max_value") is not None:
            parts.append(f"{label} between ${p['min_value']:,.0f} and ${p['max_value']:,.0f}")
        elif p.get("max_value") is not None:
            parts.append(f"{label} at most ${p['max_value']:,.0f}")
        elif p.get("min_value") is not None:
            parts.append(f"{label} at least ${p['min_value']:,.0f}")
        if p.get("hitl_above") is not None:
            parts.append(f"a person approves above ${p['hitl_above']:,.0f}")
    return f"{SCOPES[p['collection']]['label']} in {where}: " + "; ".join(parts) + "."


class PolicyEngine:
    def __init__(self, engine: Any, uri: str, source_id: str = "finance_pg"):
        self.e = engine
        self.source_id = source_id
        self._pool = shared_pool(uri)
        self._exec(DDL)
        self._lock = threading.Lock()

    def _exec(self, sql: str, params: Any = None, fetch: str = "") -> Any:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                out = None
                if fetch and cur.description:
                    cols = [d[0] for d in cur.description]
                    out = (dict(zip(cols, r)) if (r := cur.fetchone()) else None) if fetch == "one" else [dict(zip(cols, r)) for r in cur.fetchall()]
            conn.commit()
            return out
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def _query(self, tenant: str, sql: str) -> List[Dict[str, Any]]:
        res = self.e.gateway.execute_query(AccessContext(tenant_id=tenant, user_id="policy-engine", max_rows=5000), self.source_id, sql,
                                           max_rows=5000)
        if not res.succeeded:
            raise RuntimeError(res.error_message)
        return res.rows

    # ------------------------------------------------------------------ catalog (editor dropdowns come from the data)
    def catalog(self, tenant: str) -> Dict[str, Any]:
        out = {"regions": [{"code": "ALL", "name": "All regions"}] + [{"code": c, "name": n} for c, n in REGIONS.items()], "collections": []}
        for cid, scope in SCOPES.items():
            rows = self._query(tenant, scope["sql"])
            fields = []
            for fid, (kind, label, values_sql) in scope["fields"].items():
                f: Dict[str, Any] = {"id": fid, "label": label, "kind": kind}
                if kind == "categorical":
                    f["values"] = sorted({str(r["v"]) for r in self._query(tenant, values_sql) if r["v"] is not None})
                else:
                    nums = [float(r[fid]) for r in rows if r.get(fid) is not None]
                    f.update(min=min(nums) if nums else None, max=max(nums) if nums else None)
                fields.append(f)
            by_region: Dict[str, int] = {}
            for r in rows:
                by_region[r.get("region") or "?"] = by_region.get(r.get("region") or "?", 0) + 1
            out["collections"].append({"id": cid, "label": scope["label"], "records": len(rows), "by_region": by_region, "fields": fields})
        return out

    # ------------------------------------------------------------------ policies
    def _row(self, r: Dict[str, Any]) -> Dict[str, Any]:
        p = {"policy_id": r["policy_id"], "name": r["name"], "collection": r["collection"], "region": r["region"],
             "region_name": "All regions" if r["region"] == "ALL" else REGIONS.get(r["region"], r["region"]), "variable": r["variable"],
             "allowed_values": r["allowed_values"], "min_value": _f(r["min_value"]), "max_value": _f(r["max_value"]),
             "hitl_values": r["hitl_values"] or [], "hitl_above": _f(r["hitl_above"]), "active": r["active"], "owner": r["owner"],
             "version": r["version"], "updated_at": r["updated_at"], "updated_by": r.get("updated_by")}
        p["rule"] = describe(p)
        return p

    def list(self, tenant: str) -> List[Dict[str, Any]]:
        rows = self._exec("SELECT * FROM backend_runtime.policies WHERE tenant_id = %s ORDER BY created_at", (tenant,), fetch="all")
        if not rows and not self._exec("SELECT 1 AS x FROM backend_runtime.policy_events WHERE tenant_id = %s AND kind = 'defaults_created'",
                                       (tenant,), fetch="one"):
            self._event(tenant, "defaults_created", None, None, "Default demo policies created")
            for d in DEFAULT_POLICIES:  # consistent with each other by construction
                p = PolicyIn(**d)
                self._validate(p)
                self._insert(tenant, p, "system", "POL-" + uuid.uuid4().hex[:8].upper())
            rows = self._exec("SELECT * FROM backend_runtime.policies WHERE tenant_id = %s ORDER BY created_at", (tenant,), fetch="all")
        return [self._row(r) for r in rows]

    def _values(self, p: PolicyIn, user: str) -> tuple:
        return (p.name, p.collection, p.region, p.variable, Json(p.allowed_values) if p.allowed_values is not None else None, p.min_value,
                p.max_value, Json(p.hitl_values), p.hitl_above, p.active, p.owner, user)

    def _insert(self, tenant: str, p: PolicyIn, user: str, policy_id: str) -> None:
        self._exec("""INSERT INTO backend_runtime.policies (name, collection, region, variable, allowed_values, min_value, max_value,
                      hitl_values, hitl_above, active, owner, updated_by, policy_id, tenant_id)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", (*self._values(p, user), policy_id, tenant))

    def get(self, tenant: str, policy_id: str) -> Optional[Dict[str, Any]]:
        r = self._exec("SELECT * FROM backend_runtime.policies WHERE tenant_id = %s AND policy_id = %s", (tenant, policy_id), fetch="one")
        return self._row(r) if r else None

    def _validate(self, p: PolicyIn) -> None:
        fields = SCOPES[p.collection]["fields"]
        if p.variable not in fields:
            raise PolicyRejected(f"{p.variable} is not a field of {SCOPES[p.collection]['label']}; fields: {', '.join(fields)}")
        kind = fields[p.variable][0]
        if kind == "categorical":
            if not p.allowed_values:
                raise PolicyRejected("choose at least one allowed value")
            if not set(p.hitl_values) <= set(p.allowed_values):
                raise PolicyRejected("values that need a person must also be allowed")
            if p.min_value is not None or p.max_value is not None or p.hitl_above is not None:
                raise PolicyRejected(f"{p.variable} is a category: use allowed values, not amounts")
        else:
            if p.allowed_values or p.hitl_values:
                raise PolicyRejected(f"{p.variable} is a number: use a minimum, a maximum or an approval threshold")
            if p.min_value is None and p.max_value is None and p.hitl_above is None:
                raise PolicyRejected("set a minimum, a maximum or an approval threshold")
            if p.min_value is not None and p.max_value is not None and p.min_value > p.max_value:
                raise PolicyRejected("the minimum is above the maximum")

    def _admission(self, tenant: str, p: PolicyIn, policy_id: Optional[str]) -> None:
        """Reject a policy whose intersection with the active policies it overlaps is empty."""
        if not p.active:
            return
        others = [x for x in self.list(tenant) if x["active"] and x["policy_id"] != policy_id and x["collection"] == p.collection
                  and x["variable"] == p.variable and (x["region"] == "ALL" or p.region == "ALL" or x["region"] == p.region)]
        new = p.model_dump()
        for region in (REGIONS if p.region == "ALL" else [p.region]):
            group = [x for x in others if x["region"] in ("ALL", region)]
            if not group:
                continue
            if new["allowed_values"] is not None:
                allowed = set(new["allowed_values"])
                for x in group:
                    allowed &= set(x["allowed_values"] or [])
                if not allowed:
                    names = [x["name"] for x in group]
                    raise PolicyRejected(f"no {p.variable} value would be allowed in {REGIONS[region]}: this policy conflicts with "
                                         + ", ".join(f'"{n}"' for n in names), names)
            else:
                lows = [v for v in [new["min_value"]] + [x["min_value"] for x in group] if v is not None]
                highs = [v for v in [new["max_value"]] + [x["max_value"] for x in group] if v is not None]
                if lows and highs and max(lows) > min(highs):
                    names = [x["name"] for x in group if x["min_value"] is not None or x["max_value"] is not None]
                    raise PolicyRejected(f"no {p.variable} would be allowed in {REGIONS[region]} (at least {max(lows):,.0f} and at most "
                                         f"{min(highs):,.0f}): conflicts with " + ", ".join(f'"{n}"' for n in names), names)

    def save(self, tenant: str, p: PolicyIn, user: str, policy_id: Optional[str] = None, refresh: bool = True) -> Dict[str, Any]:
        self._validate(p)
        try:
            self._admission(tenant, p, policy_id)
        except PolicyRejected as exc:
            self._event(tenant, "policy_rejected", p.collection, None, f'Policy "{p.name}" rejected', {"reason": str(exc)})
            raise
        if policy_id:
            if not self.get(tenant, policy_id):
                raise KeyError(policy_id)
            self._exec("""UPDATE backend_runtime.policies SET name = %s, collection = %s, region = %s, variable = %s, allowed_values = %s,
                          min_value = %s, max_value = %s, hitl_values = %s, hitl_above = %s, active = %s, owner = %s, updated_by = %s,
                          version = version + 1, updated_at = now() WHERE tenant_id = %s AND policy_id = %s""",
                       (*self._values(p, user), tenant, policy_id))
        else:
            policy_id = "POL-" + uuid.uuid4().hex[:8].upper()
            self._insert(tenant, p, user, policy_id)
        saved = self.get(tenant, policy_id)
        self._event(tenant, "policy_saved", p.collection, None, f'Policy "{p.name}" saved', {"policy_id": policy_id, "rule": saved["rule"]})
        if refresh:
            self.refresh_async(tenant, p.collection)
        return saved

    def delete(self, tenant: str, policy_id: str, user: str) -> None:
        p = self.get(tenant, policy_id)
        if not p:
            raise KeyError(policy_id)
        self._exec("DELETE FROM backend_runtime.policies WHERE tenant_id = %s AND policy_id = %s", (tenant, policy_id))
        self._event(tenant, "policy_deleted", p["collection"], None, f'Policy "{p["name"]}" deleted', {"by": user})
        self.refresh_async(tenant, p["collection"])

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, tenant: str, collection: str, record: Dict[str, Any], proposed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Decide on a record's state (or the state a proposed change would produce): blocked, hitl, allowed or no_policy."""
        state = {**record, **(proposed or {})}
        region = state.get("region")
        applicable = [p for p in self.list(tenant) if p["active"] and p["collection"] == collection and p["region"] in ("ALL", region)]
        if not applicable:
            return {"decision": "no_policy", "collection": collection, "record_id": record.get("record_id"), "region": region,
                    "violations": [], "hitl": [], "policies": []}
        violations, hitl = [], []
        for var in sorted({p["variable"] for p in applicable}):
            ps = [p for p in applicable if p["variable"] == var]
            kind = SCOPES[collection]["fields"][var][0]
            value = state.get(var)
            if kind == "categorical":
                cat = [p for p in ps if p["allowed_values"] is not None]
                allowed = set.intersection(*(set(p["allowed_values"]) for p in cat)) if cat else None
                if allowed is not None and str(value) not in allowed:
                    violations.append({"variable": var, "value": value, "allowed": sorted(allowed), "policies": [p["name"] for p in cat]})
                for p in ps:
                    if str(value) in (p["hitl_values"] or []):
                        hitl.append({"variable": var, "value": value, "policy": p["name"]})
            else:
                v = None if value is None else float(value)
                lows = [p for p in ps if p["min_value"] is not None]
                highs = [p for p in ps if p["max_value"] is not None]
                lo = max((p["min_value"] for p in lows), default=None)
                hi = min((p["max_value"] for p in highs), default=None)
                if v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi)):
                    violations.append({"variable": var, "value": v, "min": lo, "max": hi,
                                       "policies": list(dict.fromkeys(p["name"] for p in lows + highs))})  # a min-and-max policy once
                for p in ps:
                    if p["hitl_above"] is not None and v is not None and v > p["hitl_above"]:
                        hitl.append({"variable": var, "value": v, "policy": p["name"]})
        decision = "blocked" if violations else "hitl" if hitl else "allowed"
        return {"decision": decision, "collection": collection, "record_id": record.get("record_id"), "region": region,
                "violations": violations, "hitl": hitl, "policies": [p["name"] for p in applicable]}

    def records(self, tenant: str, collection: str, where: str = "") -> List[Dict[str, Any]]:
        return self._query(tenant, SCOPES[collection]["sql"] + (f" AND {where}" if where else ""))

    # ------------------------------------------------------------------ state check (event path)
    def refresh(self, tenant: str, collection: Optional[str] = None) -> Dict[str, Any]:
        """Re-evaluate every pending record: store its state, keep the previous one, record violations that appear or clear."""
        out = {}
        with self._lock:
            for cid in [collection] if collection else list(SCOPES):
                rows = self.records(tenant, cid)
                known = {r["record_id"]: r for r in self._exec(
                    "SELECT record_id, state, decision FROM backend_runtime.policy_states WHERE tenant_id = %s AND collection = %s",
                    (tenant, cid), fetch="all")}
                seen, new_violations, cleared = set(), 0, 0
                fields = list(SCOPES[cid]["fields"])
                for r in rows:
                    rid = r["record_id"]
                    seen.add(rid)
                    res = self.evaluate(tenant, cid, r)
                    state = {k: r.get(k) for k in fields}
                    prev = known.get(rid)
                    changed = prev is None or prev["state"] != json.loads(json.dumps(state, default=str))
                    if prev is None or changed or prev["decision"] != res["decision"]:
                        self._exec("""INSERT INTO backend_runtime.policy_states (tenant_id, collection, record_id, region, reference, customer, state,
                                      previous_state, decision, violations, evaluated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                                      ON CONFLICT (tenant_id, collection, record_id) DO UPDATE SET region = EXCLUDED.region,
                                      reference = EXCLUDED.reference, customer = EXCLUDED.customer, state = EXCLUDED.state,
                                      previous_state = CASE WHEN backend_runtime.policy_states.state <> EXCLUDED.state
                                                            THEN backend_runtime.policy_states.state ELSE backend_runtime.policy_states.previous_state END,
                                      decision = EXCLUDED.decision, violations = EXCLUDED.violations, evaluated_at = now()""",
                                   (tenant, cid, rid, r.get("region"), r.get("reference"), r.get("customer"), Json(state, dumps=_dumps),
                                    Json(prev["state"]) if prev else None, res["decision"], Json(res["violations"], dumps=_dumps)))
                    was_blocked = bool(prev) and prev["decision"] == "blocked"
                    if res["decision"] == "blocked" and not was_blocked:
                        new_violations += 1
                        v = res["violations"][0]
                        self._event(tenant, "violation", cid, rid, f"{r.get('customer')} · {rid} violates {', '.join(v['policies'])}",
                                    {"reference": r.get("reference"), "customer": r.get("customer"), "region": r.get("region"),
                                     "violations": res["violations"], "before": prev["state"] if prev else None, "after": state})
                    elif was_blocked and res["decision"] != "blocked":
                        cleared += 1
                        self._event(tenant, "violation_cleared", cid, rid, f"{r.get('customer')} · {rid} complies again",
                                    {"before": prev["state"], "after": state})
                gone = [rid for rid in known if rid not in seen]
                for rid in gone:  # no longer pending (paid, signed, ...)
                    self._exec("DELETE FROM backend_runtime.policy_states WHERE tenant_id = %s AND collection = %s AND record_id = %s",
                               (tenant, cid, rid))
                out[cid] = {"records": len(rows), "new_violations": new_violations, "cleared": cleared, "left_scope": len(gone)}
        return out

    def refresh_async(self, tenant: str, collection: Optional[str] = None) -> None:
        threading.Thread(target=self._safe_refresh, args=(tenant, collection), daemon=True, name="policy-refresh").start()

    def _safe_refresh(self, tenant: str, collection: Optional[str]) -> None:
        try:
            self.refresh(tenant, collection)
        except Exception:
            pass  # retried on the next policy change or sync event

    def states(self, tenant: str, collection: Optional[str] = None, decision: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, params = "SELECT * FROM backend_runtime.policy_states WHERE tenant_id = %s", [tenant]
        if collection:
            sql += " AND collection = %s"
            params.append(collection)
        if decision:
            sql += " AND decision = %s"
            params.append(decision)
        return self._exec(sql + " ORDER BY evaluated_at DESC LIMIT 500", params, fetch="all")

    def summary(self, tenant: str) -> List[Dict[str, Any]]:
        """Per policy: records in scope and how many violate it (the Autopilot goals)."""
        states = self.states(tenant)
        out = []
        for p in self.list(tenant):
            scope = [s for s in states if s["collection"] == p["collection"] and (p["region"] == "ALL" or s["region"] == p["region"])]
            bad = [s for s in scope if any(p["name"] in v["policies"] for v in s["violations"] or [])]
            out.append({**p, "in_scope": len(scope), "violations": len(bad)})
        return out

    # ------------------------------------------------------------------ events
    def _event(self, tenant: str, kind: str, collection: Optional[str], record_id: Optional[str], title: str,
               detail: Optional[Dict[str, Any]] = None) -> None:
        self._exec("INSERT INTO backend_runtime.policy_events (tenant_id, kind, collection, record_id, title, detail) VALUES (%s, %s, %s, %s, %s, %s)",
                   (tenant, kind, collection, record_id, title, Json(detail or {}, dumps=_dumps)))

    def events(self, tenant: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self._exec("SELECT * FROM backend_runtime.policy_events WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
                          (tenant, limit), fetch="all")


def _dumps(o: Any) -> str:
    return json.dumps(o, default=str)


def fmt_value(v: Any) -> str:
    return f"${float(v):,.2f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def explain(result: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Plain-language reason for a decision and the policy names involved."""
    if result["decision"] == "blocked":
        v = result["violations"][0]
        bound = (f"allowed: {', '.join(v['allowed'])}" if "allowed" in v else
                 " and ".join(x for x in (f"at least {fmt_value(v['min'])}" if v.get("min") is not None else "",
                                          f"at most {fmt_value(v['max'])}" if v.get("max") is not None else "") if x))
        return f"{v['variable']} would be {fmt_value(v['value'])} ({bound})", v["policies"]
    if result["decision"] == "hitl":
        return "; ".join(f"{h['variable']} {fmt_value(h['value'])} needs a person" for h in result["hitl"]), [h["policy"] for h in result["hitl"]]
    if result["decision"] == "no_policy":
        return "no policy covers this record", []
    return "passes the policy check (" + "; ".join(result["policies"]) + ")", result["policies"]
