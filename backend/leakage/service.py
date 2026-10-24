"""Leakage hunting as a running capability: detect -> investigate -> prioritise -> draft recovery -> approve -> mock AP.

* scan() runs every detector (read-only, through the gateway) and upserts cases with a stable id (type + record), so
  repeated scans update rather than duplicate. Cases no longer detected (e.g. after a credit note) become 'resolved'.
* Each new case gets a drafted recovery action in the Approvals queue. Nothing is sent anywhere until a person
  approves; then the request goes to the mock AP / ERP API (``backend_runtime.mock_ap_requests``). Payment holds block
  the unpaid invoice there; nothing touches a real ERP or supplier.
* Cases live in ``backend_runtime.leakage_cases`` and are exposed read-only as the ``leakage`` source, so the agents
  can answer questions about them like any other data.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from backend.gateway.registry import AccessContext
from backend.leakage.detectors import DETECTORS
from backend.pg_pool import shared_pool

DDL = """
CREATE SCHEMA IF NOT EXISTS backend_runtime;
CREATE TABLE IF NOT EXISTS backend_runtime.leakage_cases (
    case_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    leakage_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    related_records JSONB NOT NULL DEFAULT '[]',
    supplier_id TEXT,
    supplier_name TEXT,
    recoverable_usd NUMERIC(14,2) NOT NULL,
    confidence NUMERIC(4,3) NOT NULL,
    priority_score NUMERIC(16,2) NOT NULL,
    root_cause TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]',
    recovery_action TEXT NOT NULL,
    status TEXT NOT NULL,
    approval_id TEXT,
    recovery JSONB,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, case_id)
);
CREATE INDEX IF NOT EXISTS leakage_cases_priority_idx ON backend_runtime.leakage_cases (tenant_id, status, priority_score DESC);
CREATE TABLE IF NOT EXISTS backend_runtime.mock_ap_requests (
    request_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    supplier_name TEXT,
    record_id TEXT,
    amount_usd NUMERIC(14,2),
    status TEXT NOT NULL,
    approved_by TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
ACTION_TEXT = {
    "claw_back": ("Request a refund of duplicate invoice {ref}", "Claw-back request"),
    "payment_hold": ("Hold the payment of duplicate invoice {ref}", "Payment hold"),
    "credit_note": ("Request a credit note for the overcharge on {ref}", "Credit note request"),
    "refund_request": ("Request a refund of the overpayment on {ref}", "Refund request"),
    "discount_recovery": ("Ask the supplier to honour the missed early-payment discount on {ref}", "Discount recovery request"),
    "seat_reduction": ("Reduce unused seats on {ref} at renewal", "Seat reduction"),
}
TYPE_LABEL = {"duplicate_invoice": "Duplicate invoices", "price_variance": "Price above contract", "missed_discount": "Missed early-payment discounts",
              "overpayment": "Overpayments", "unused_saas": "Unused SaaS seats"}
_dumps = lambda o: json.dumps(o, default=str)  # noqa: E731


class LeakageService:
    def __init__(self, engine: Any, uri: str, source_id: str = "finance_pg"):
        self.e = engine
        self.source_id = source_id
        self._pool = shared_pool(uri)
        self._exec(DDL)
        self._lock = threading.Lock()
        self._sweeper: Optional[threading.Thread] = None

    def _exec(self, sql: str, params: Any = None, fetch: str = "") -> Any:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                out = None
                if fetch == "one":
                    out = cur.fetchone()
                elif fetch == "all":
                    out = cur.fetchall()
                if fetch and cur.description:
                    cols = [d[0] for d in cur.description]
                    out = (dict(zip(cols, out)) if out else None) if fetch == "one" else [dict(zip(cols, r)) for r in out]
            conn.commit()
            return out
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    # ------------------------------------------------------------------ detection
    def detect(self, tenant: str = "default") -> List[Dict[str, Any]]:
        ctx = AccessContext(tenant_id=tenant, user_id="leakage-hunter", max_rows=10000)

        def run(sql: str) -> List[Dict[str, Any]]:
            res = self.e.gateway.execute_query(ctx, self.source_id, sql, max_rows=10000)
            if not res.succeeded:
                raise RuntimeError(res.error_message)
            if res.truncated:  # a silently cut candidate list would hide leakage
                raise RuntimeError(f"a detector query returned more than {len(res.rows)} rows; narrow it before trusting the result")
            return res.rows

        cases = []
        for kind, detector in DETECTORS.items():
            for c in detector(run):
                c["case_id"] = f"{kind}:{c['ref']}"
                c["priority_score"] = round(c["recoverable_usd"] * c["confidence"], 2)
                cases.append(c)
        return sorted(cases, key=lambda c: -c["priority_score"])

    def scan(self, tenant: str = "default") -> Dict[str, Any]:
        """Detect, upsert cases, draft recovery for new ones and resolve cases that are no longer detected."""
        with self._lock:
            t0 = time.perf_counter()
            cases = self.detect(tenant)
            known = {r["case_id"]: r for r in self._exec("SELECT case_id, status FROM backend_runtime.leakage_cases WHERE tenant_id = %s",
                                                        (tenant,), fetch="all")}
            new = []
            for c in cases:
                values = (c["related"], c.get("supplier_id"), c.get("supplier_name"), c["recoverable_usd"], c["confidence"],
                          c["priority_score"], c["root_cause"], c["evidence"], c["action"])
                if c["case_id"] in known:
                    self._exec("""UPDATE backend_runtime.leakage_cases SET related_records = %s, supplier_id = %s, supplier_name = %s,
                                  recoverable_usd = %s, confidence = %s, priority_score = %s, root_cause = %s, evidence = %s,
                                  recovery_action = %s, last_seen = now(),
                                  status = CASE WHEN status = 'resolved' THEN 'open' ELSE status END WHERE case_id = %s AND tenant_id = %s""",
                               (Json(values[0]), *values[1:7], Json(values[7], dumps=_dumps), values[8], c["case_id"], tenant))
                else:
                    self._exec("""INSERT INTO backend_runtime.leakage_cases (case_id, tenant_id, leakage_type, record_id, related_records,
                                  supplier_id, supplier_name, recoverable_usd, confidence, priority_score, root_cause, evidence,
                                  recovery_action, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')""",
                               (c["case_id"], tenant, c["type"], c["ref"], Json(values[0]), *values[1:7], Json(values[7], dumps=_dumps), values[8]))
                    new.append(c)
            gone = [cid for cid, r in known.items() if cid not in {c["case_id"] for c in cases} and r["status"] in ("open", "awaiting_approval")]
            for cid in gone:
                self._exec("UPDATE backend_runtime.leakage_cases SET status = 'resolved', last_seen = now() WHERE case_id = %s AND tenant_id = %s", (cid, tenant))
            for c in new:
                self._draft_recovery(tenant, c)
            return {"detected": len(cases), "new": len(new), "resolved": len(gone),
                    "recoverable_usd": round(sum(c["recoverable_usd"] for c in cases), 2), "ms": round((time.perf_counter() - t0) * 1000)}

    # ------------------------------------------------------------------ recovery
    def _draft_recovery(self, tenant: str, c: Dict[str, Any]) -> None:
        title, kind = ACTION_TEXT[c["action"]]
        approval_id = "REC-" + hashlib.sha1(f"{tenant}:{c['case_id']}".encode()).hexdigest()[:10]
        now = datetime.now().strftime("%H:%M:%S")
        payload = {
            "id": approval_id, "kind": "leakage_recovery", "case_id": c["case_id"], "agent": "Recovery",
            "title": title.format(ref=c["ref"]) + f" — {c.get('supplier_name')}", "entity": f"{c.get('supplier_name')} · {c['ref']}",
            "amountL": 0, "impact": f"${c['recoverable_usd']:,.2f} recoverable · confidence {c['confidence']:.0%}",
            "rationale": c["root_cause"], "status": "pending", "autonomy": "L2", "approval": "AP manager",
            "reversible": c["action"] == "payment_hold", "sources": [],
            "checks": [{"label": e["label"], "result": "warn", "detail": "; ".join(", ".join(f"{k}: {v}" for k, v in row.items()) for row in e["rows"][:3])}
                       for e in c["evidence"]] + [{"label": "Executes only after approval", "result": "pass", "detail": f"{kind} via the mock AP API"}],
            "audit": [{"time": now, "actor": f"{TYPE_LABEL[c['type']]} detector", "event": f"Detected {c['case_id']}"},
                      {"time": now, "actor": "Recovery agent", "event": f"Drafted {kind.lower()}"}],
        }
        try:
            self.e.governance.create_approval(tenant, approval_id, payload)
        except Exception:
            return  # already drafted (e.g. a case that was resolved and reopened)
        self._exec("UPDATE backend_runtime.leakage_cases SET status = 'awaiting_approval', approval_id = %s WHERE case_id = %s AND tenant_id = %s",
                   (approval_id, c["case_id"], tenant))

    def decided(self, tenant: str, approval: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = approval.get("payload") or {}
        if payload.get("kind") != "leakage_recovery":
            return None
        case = self._exec("SELECT * FROM backend_runtime.leakage_cases WHERE case_id = %s AND tenant_id = %s",
                          (payload["case_id"], tenant), fetch="one")
        if not case:
            return None
        if approval["status"] != "approved":
            self._exec("UPDATE backend_runtime.leakage_cases SET status = 'dismissed' WHERE case_id = %s AND tenant_id = %s", (case["case_id"], tenant))
            return {"case_id": case["case_id"], "status": "dismissed"}
        request = self._submit_to_mock_ap(tenant, case, approval.get("decided_by"))
        self._exec("UPDATE backend_runtime.leakage_cases SET status = 'executed', recovery = %s WHERE case_id = %s AND tenant_id = %s",
                   (Json(request, dumps=_dumps), case["case_id"], tenant))
        return {"case_id": case["case_id"], "status": "executed", "request": request}

    def _submit_to_mock_ap(self, tenant: str, case: Dict[str, Any], approver: Optional[str]) -> Dict[str, Any]:
        """Mock AP / ERP API: records the request; a payment hold blocks the unpaid invoice in the mock ledger."""
        _title, kind = ACTION_TEXT[case["recovery_action"]]
        request_id = "AP-" + hashlib.sha1(f"{case['case_id']}:{time.time()}".encode()).hexdigest()[:10].upper()
        status = "payment_blocked" if case["recovery_action"] == "payment_hold" else "submitted_to_supplier"
        body = {"request_type": kind, "supplier": case["supplier_name"], "record": case["record_id"],
                "amount_usd": float(case["recoverable_usd"]), "reason": case["root_cause"]}
        self._exec("""INSERT INTO backend_runtime.mock_ap_requests (request_id, tenant_id, case_id, request_type, supplier_name, record_id,
                      amount_usd, status, approved_by, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (request_id, tenant, case["case_id"], kind, case["supplier_name"], case["record_id"], case["recoverable_usd"], status,
                    approver, Json(body)))
        return {"request_id": request_id, "status": status, **body}

    # ------------------------------------------------------------------ views
    def cases(self, tenant: str, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM backend_runtime.leakage_cases WHERE tenant_id = %s"
        params: List[Any] = [tenant]
        if status:
            sql += " AND status = %s"
            params.append(status)
        rows = self._exec(sql + " ORDER BY priority_score DESC LIMIT %s", params + [limit], fetch="all")
        for r in rows:
            r["recoverable_usd"], r["confidence"], r["priority_score"] = float(r["recoverable_usd"]), float(r["confidence"]), float(r["priority_score"])
            r["type_label"] = TYPE_LABEL.get(r["leakage_type"], r["leakage_type"])
        return rows

    def summary(self, tenant: str) -> Dict[str, Any]:
        rows = self.cases(tenant, limit=10000)
        by_type: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            t = by_type.setdefault(r["leakage_type"], {"label": r["type_label"], "cases": 0, "recoverable_usd": 0.0, "open": 0, "executed": 0})
            t["cases"] += 1
            t["recoverable_usd"] = round(t["recoverable_usd"] + r["recoverable_usd"], 2)
            t["open"] += int(r["status"] in ("open", "awaiting_approval"))
            t["executed"] += int(r["status"] == "executed")
        active = [r for r in rows if r["status"] != "resolved"]
        return {"cases": len(active), "recoverable_usd": round(sum(r["recoverable_usd"] for r in active), 2),
                "awaiting_approval": sum(1 for r in rows if r["status"] == "awaiting_approval"),
                "executed": sum(1 for r in rows if r["status"] == "executed"),
                "recovered_usd": round(sum(r["recoverable_usd"] for r in rows if r["status"] == "executed"), 2),
                "dismissed": sum(1 for r in rows if r["status"] == "dismissed"), "by_type": by_type}

    def mock_ap_requests(self, tenant: str) -> List[Dict[str, Any]]:
        rows = self._exec("SELECT * FROM backend_runtime.mock_ap_requests WHERE tenant_id = %s ORDER BY created_at DESC", (tenant,), fetch="all")
        for r in rows:
            r["amount_usd"] = float(r["amount_usd"] or 0)
        return rows

    # ------------------------------------------------------------------ always running
    def start_sweeper(self, every_s: float, tenant: str = "default") -> None:
        if self._sweeper:
            return

        def loop() -> None:
            time.sleep(5)
            while True:
                try:
                    self.scan(tenant)
                except Exception:
                    pass  # the next sweep retries; the data may be mid-load
                time.sleep(every_s)

        self._sweeper = threading.Thread(target=loop, daemon=True, name="leakage-sweeper")
        self._sweeper.start()
