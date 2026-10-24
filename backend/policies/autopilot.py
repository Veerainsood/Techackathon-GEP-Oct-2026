"""Autopilot: HubSpot changes trigger decisions on the customer's pending contracts and overdue invoices.

  HubSpot sync event -> contacts whose lifecycle stage changed (compared with the stage autopilot last saw)
  -> the customer group they belong to -> its pending contracts and overdue invoices -> a proposed action per record
  -> policy check on the state the action would produce (violation: blocked; no policy: nothing is done)
  -> guardrail for the action (Autonomous: done; Ask me, or a policy that needs a person: Approvals; Never: refused).

Actions are recorded in the app (``autopilot_actions``) and never written to the finance data. Approved actions are checked
against the policies again before they count as done. The emergency stop pauses everything.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2.errors
from psycopg2.extras import Json

from backend.gateway.registry import AccessContext
from backend.pg_pool import shared_pool
from backend.policies.engine import REGIONS, explain, fmt_value

DDL = """
CREATE SCHEMA IF NOT EXISTS backend_runtime;
CREATE TABLE IF NOT EXISTS backend_runtime.autopilot_contacts (
    tenant_id TEXT NOT NULL, contact_id TEXT NOT NULL, lifecyclestage TEXT, crm_account_id TEXT, seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, contact_id)
);
CREATE TABLE IF NOT EXISTS backend_runtime.autopilot_actions (
    action_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, action TEXT NOT NULL, collection TEXT NOT NULL, record_id TEXT NOT NULL,
    reference TEXT, customer TEXT, region TEXT, trigger JSONB NOT NULL, changes JSONB NOT NULL, policy JSONB NOT NULL, guardrail TEXT,
    mode TEXT, decision TEXT NOT NULL, reason TEXT NOT NULL, approval_id TEXT, simulated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), decided_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS autopilot_actions_recent_idx ON backend_runtime.autopilot_actions (tenant_id, created_at DESC);
DELETE FROM backend_runtime.autopilot_actions a USING backend_runtime.autopilot_actions b
    WHERE a.tenant_id = b.tenant_id AND a.action = b.action AND a.record_id = b.record_id AND a.created_at > b.created_at
    AND a.decision IN ('executed', 'awaiting_approval') AND b.decision IN ('executed', 'awaiting_approval');
CREATE UNIQUE INDEX IF NOT EXISTS autopilot_actions_once_idx ON backend_runtime.autopilot_actions (tenant_id, action, record_id)
    WHERE decision IN ('executed', 'awaiting_approval');
CREATE TABLE IF NOT EXISTS backend_runtime.autopilot_settings (
    tenant_id TEXT PRIMARY KEY, paused BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by TEXT
);
"""
ACTIONS = {
    "sign_contract": {"collection": "pending_contracts", "guardrail": "sign-contracts", "changes": {"execution_status": "Fully_Signed"},
                      "title": "Mark contract {ref} signed", "approver": "Contract owner"},
    "record_payment": {"collection": "pending_invoices", "guardrail": "record-invoice-payments",
                       "changes": {"settlement_status": "PAID", "open_balance_usd": 0}, "title": "Record payment of overdue invoice {ref}",
                       "approver": "AR manager"},
}
DONE = ("executed", "awaiting_approval")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


class Autopilot:
    def __init__(self, engine: Any, uri: str):
        self.e = engine
        self._pool = shared_pool(uri)
        self._exec(DDL)
        self._lock = threading.Lock()  # one decision run at a time: a record is never acted on twice

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

    # ------------------------------------------------------------------ settings
    def paused(self, tenant: str) -> bool:
        r = self._exec("SELECT paused FROM backend_runtime.autopilot_settings WHERE tenant_id = %s", (tenant,), fetch="one")
        return bool(r and r["paused"])

    def set_paused(self, tenant: str, paused: bool, user: str) -> Dict[str, Any]:
        self._exec("""INSERT INTO backend_runtime.autopilot_settings (tenant_id, paused, updated_by) VALUES (%s, %s, %s)
                      ON CONFLICT (tenant_id) DO UPDATE SET paused = EXCLUDED.paused, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (tenant, paused, user))
        return {"paused": paused}

    # ------------------------------------------------------------------ HubSpot trigger
    def _contacts(self, tenant: str) -> List[Dict[str, Any]]:
        res = self.e.gateway.execute_query(
            AccessContext(tenant_id=tenant, user_id="autopilot", max_rows=5000), "hubspot",
            """SELECT c.contact_id, c.email, c.firstname, c.lastname, lower(c.lifecyclestage) AS stage, c.crm_account_id,
                      p.conglomerate_id, p.crm_account_name
               FROM hubspot.contacts c LEFT JOIN pipeline_crm.prospect_accounts p ON p.crm_account_id = c.crm_account_id""", max_rows=5000)
        if not res.succeeded:
            raise RuntimeError(res.error_message)
        return res.rows

    def prime(self, tenant: str) -> int:
        """Remember every contact's current stage, so only later changes trigger anything."""
        rows = self._contacts(tenant)
        for c in rows:
            self._exec("""INSERT INTO backend_runtime.autopilot_contacts (tenant_id, contact_id, lifecyclestage, crm_account_id)
                          VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, contact_id) DO NOTHING""",
                       (tenant, str(c["contact_id"]), c["stage"], c["crm_account_id"]))
        return len(rows)

    def on_hubspot_event(self, tenant: str) -> Dict[str, Any]:
        known = {r["contact_id"]: r["lifecyclestage"] for r in self._exec(
            "SELECT contact_id, lifecyclestage FROM backend_runtime.autopilot_contacts WHERE tenant_id = %s", (tenant,), fetch="all")}
        out = {"contacts": 0, "transitions": [], "actions": []}
        for c in self._contacts(tenant):
            out["contacts"] += 1
            cid, stage = str(c["contact_id"]), c["stage"]
            prev = known.get(cid)
            self._exec("""INSERT INTO backend_runtime.autopilot_contacts (tenant_id, contact_id, lifecyclestage, crm_account_id) VALUES (%s, %s, %s, %s)
                          ON CONFLICT (tenant_id, contact_id) DO UPDATE SET lifecyclestage = EXCLUDED.lifecyclestage,
                          crm_account_id = EXCLUDED.crm_account_id, seen_at = now()""", (tenant, cid, stage, c["crm_account_id"]))
            if cid in known and prev != stage:
                out["transitions"].append({"contact": c.get("email"), "from": prev, "to": stage})
                out["actions"] += self.handle(tenant, c, prev, stage, simulated=False)
        return out

    def simulate(self, tenant: str, email: str, stage: str) -> Dict[str, Any]:
        """Run the trigger as if HubSpot had reported this stage change (nothing is changed in HubSpot or the data)."""
        c = next((x for x in self._contacts(tenant) if (x.get("email") or "").lower() == email.lower()), None)
        if c is None:
            raise KeyError(email)
        return {"contact": email, "from": c["stage"], "to": stage.lower(), "actions": self.handle(tenant, c, c["stage"], stage.lower(), simulated=True)}

    def handle(self, tenant: str, contact: Dict[str, Any], prev: Optional[str], stage: str, simulated: bool) -> List[Dict[str, Any]]:
        group = contact.get("conglomerate_id")
        who = f"{contact.get('firstname') or ''} {contact.get('lastname') or ''}".strip() or contact.get("email")
        trigger = {"source": "HubSpot", "contact": contact.get("email"), "name": who, "account": contact.get("crm_account_name"),
                   "from": prev, "to": stage, "at": datetime.now().isoformat(timespec="seconds")}
        if stage != "customer" or not group or not SAFE_ID.match(str(group)):
            return []  # only a move to "customer" of a contact linked to a customer group leads to decisions
        out = []
        with self._lock:
            for r in self.e.policies.records(tenant, "pending_contracts", f"c.conglomerate_id = '{group}'"):
                out.append(self.propose(tenant, "sign_contract", r, trigger, simulated))
            for r in self.e.policies.records(tenant, "pending_invoices", f"c.conglomerate_id = '{group}' AND i.settlement_status = 'OVERDUE'"):
                out.append(self.propose(tenant, "record_payment", r, trigger, simulated))
        return [a for a in out if a]

    # ------------------------------------------------------------------ decisions
    def propose(self, tenant: str, action: str, record: Dict[str, Any], trigger: Dict[str, Any], simulated: bool) -> Optional[Dict[str, Any]]:
        spec = ACTIONS[action]
        rid = record["record_id"]
        if self._exec("SELECT 1 AS x FROM backend_runtime.autopilot_actions WHERE tenant_id = %s AND action = %s AND record_id = %s AND decision IN %s",
                      (tenant, action, rid, DONE), fetch="one"):
            return None  # already done or waiting for a person
        res = self.e.policies.evaluate(tenant, spec["collection"], record, spec["changes"])
        reason, policies = explain(res)
        mode = self.e.governance.mode(tenant, spec["guardrail"])
        approval_id = None
        if self.paused(tenant):
            decision, reason = "paused", "Autopilot is paused (emergency stop)"
        elif res["decision"] == "no_policy":
            decision = "skipped"
        elif res["decision"] == "blocked":
            decision = "blocked"
        elif mode == "never":
            decision, reason = "refused", f"guardrail '{spec['guardrail']}' is set to Never"
        elif res["decision"] == "hitl" or mode == "approve":
            decision = "awaiting_approval"
            if res["decision"] != "hitl":
                reason = f"{reason}; guardrail '{spec['guardrail']}' asks a person"
        else:
            decision = "executed"
        action_id = "AUTO-" + hashlib.sha1(f"{tenant}:{action}:{rid}:{datetime.now().isoformat()}".encode()).hexdigest()[:10].upper()
        row = {"action_id": action_id, "action": action, "collection": spec["collection"], "record_id": rid, "reference": record.get("reference"),
               "customer": record.get("customer"), "region": record.get("region"), "trigger": trigger, "changes": spec["changes"],
               "policy": {**res, "reason": reason, "policy_names": policies}, "guardrail": spec["guardrail"], "mode": mode,
               "decision": decision, "reason": reason, "simulated": simulated, "state": {k: record.get(k) for k in record}}
        if decision == "awaiting_approval":
            approval_id = action_id
        try:
            self._exec("""INSERT INTO backend_runtime.autopilot_actions (action_id, tenant_id, action, collection, record_id, reference, customer,
                          region, trigger, changes, policy, guardrail, mode, decision, reason, approval_id, simulated, decided_at)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NULL ELSE now() END)""",
                       (action_id, tenant, action, spec["collection"], rid, record.get("reference"), record.get("customer"), record.get("region"),
                        Json(trigger), Json(spec["changes"]), Json(row["policy"], dumps=_dumps), spec["guardrail"], mode, decision, reason,
                        approval_id, simulated, decision == "awaiting_approval"))
        except psycopg2.errors.UniqueViolation:
            return None  # another run already acted on this record
        if approval_id:
            self.e.governance.create_approval(tenant, approval_id, self._approval_payload(row, spec))
        return {k: row[k] for k in ("action_id", "action", "record_id", "reference", "customer", "region", "decision", "reason", "guardrail", "mode")}

    def _approval_payload(self, row: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now().strftime("%H:%M:%S")
        t = row["trigger"]
        st = row["state"]
        value = st.get("contracted_tcv_usd") or st.get("open_balance_usd") or st.get("gross_total_usd")
        p = row["policy"]
        return {
            "id": row["action_id"], "kind": "autopilot_action", "action_id": row["action_id"], "agent": "Autopilot",
            "title": spec["title"].format(ref=row["record_id"]) + f" — {row['customer']}", "entity": f"{row['customer']} · {row['reference'] or row['record_id']}",
            "amountL": 0, "impact": (f"{fmt_value(value)} · " if value is not None else "") + REGIONS.get(row["region"] or "", row["region"] or ""),
            "rationale": f"{t['name']} moved from {t['from'] or 'unknown'} to {t['to']} in HubSpot ({t['account']}). {p['reason'].capitalize()}.",
            "status": "pending", "autonomy": "L3", "approval": spec["approver"], "reversible": False, "sources": [],
            "checks": [{"label": "Policy check", "result": "warn" if p["decision"] == "hitl" else "pass",
                        "detail": p["reason"] + (f" ({', '.join(p['policy_names'])})" if p["policy_names"] else "")},
                       {"label": "Guardrail", "result": "warn" if row["mode"] == "approve" else "pass",
                        "detail": f"{row['guardrail']}: {'Ask me' if row['mode'] == 'approve' else 'Autonomous'}"},
                       {"label": "Recorded in the app only", "result": "pass", "detail": "nothing is written to the ERP"}],
            "audit": [{"time": now, "actor": "HubSpot", "event": f"{t['contact']} → {t['to']}"},
                      {"time": now, "actor": "Autopilot", "event": f"Proposed: {spec['title'].format(ref=row['record_id'])}"}],
        }

    def decided(self, tenant: str, approval: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = approval.get("payload") or {}
        if payload.get("kind") != "autopilot_action":
            return None
        a = self._exec("SELECT * FROM backend_runtime.autopilot_actions WHERE tenant_id = %s AND action_id = %s",
                       (tenant, payload["action_id"]), fetch="one")
        if not a or a["decision"] != "awaiting_approval":
            return None
        if approval["status"] != "approved":
            decision, reason = "rejected", f"rejected by {approval.get('decided_by') or 'a reviewer'}"
        else:
            rows = self.e.policies.records(tenant, a["collection"], f"{'a.agreement_id' if a['collection'] == 'pending_contracts' else 'i.invoice_id'} = '{a['record_id']}'") \
                if SAFE_ID.match(a["record_id"]) else []
            if not rows:
                decision, reason = "superseded", "the record is no longer pending"
            else:  # policies may have changed while it waited: check the resulting state again
                res = self.e.policies.evaluate(tenant, a["collection"], rows[0], a["changes"])
                reason, _ = explain(res)
                decision = "blocked" if res["decision"] == "blocked" else "executed"
                if decision == "executed":
                    reason = f"approved by {approval.get('decided_by') or 'a reviewer'}; recorded (not written to the ERP)"
        self._exec("UPDATE backend_runtime.autopilot_actions SET decision = %s, reason = %s, decided_at = now() WHERE tenant_id = %s AND action_id = %s",
                   (decision, reason, tenant, a["action_id"]))
        return {"action_id": a["action_id"], "decision": decision, "reason": reason}

    def actions(self, tenant: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self._exec("SELECT * FROM backend_runtime.autopilot_actions WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
                          (tenant, limit), fetch="all")


def _dumps(o: Any) -> str:
    return json.dumps(o, default=str)
