"""Guardrails and approvals in PostgreSQL (schema ``backend_runtime``, same database as the runtime store).

* guardrails -- one row per action the agent may take, with its mode:
                ``auto`` (run on its own), ``approve`` (ask a person first) or ``never`` (refuse).
                Locked rules cannot be changed through the API.
* approvals  -- actions waiting for a decision. The payload is the action as the UI shows it, and the
                decision is recorded in the payload's audit trail.

This module records guardrail settings and approval decisions only. Nothing here executes an action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from backend.pg_pool import shared_pool

MODES = ("auto", "approve", "never")
APPROVAL_STATUSES = ("pending", "approved", "rejected")

DDL = """
CREATE SCHEMA IF NOT EXISTS backend_runtime;
CREATE TABLE IF NOT EXISTS backend_runtime.guardrails (
    tenant_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    action TEXT NOT NULL,
    agent TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('auto', 'approve', 'never')),
    limit_text TEXT NOT NULL DEFAULT '',
    locked BOOLEAN NOT NULL DEFAULT FALSE,
    position INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT,
    PRIMARY KEY (tenant_id, rule_id)
);
CREATE TABLE IF NOT EXISTS backend_runtime.approvals (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    payload JSONB NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    decision_note TEXT
);
CREATE INDEX IF NOT EXISTS approvals_tenant_status_idx ON backend_runtime.approvals (tenant_id, status, requested_at);
"""

# Default rules, in display order. Same rules and wording as the Settings screen's starting list.
# (rule_id, action, agent, mode, limit, locked)
DEFAULT_GUARDRAILS: List[tuple] = [
    ("send-collection-reminders", "Send collection reminders", "Collections", "auto", "Approved templates only", False),
    ("escalate-overdue-accounts", "Escalate overdue accounts", "Collections", "auto", "Any amount", False),
    ("approve-purchase-requests", "Approve purchase requests", "Spend Control", "auto", "Up to ₹25K, within budget", False),
    ("hold-over-budget-requests", "Hold over-budget requests", "Spend Control", "auto", "Any amount", False),
    ("schedule-vendor-payments", "Schedule vendor payments", "Payments", "auto", "Up to ₹2L · above needs approval", False),
    ("pay-early-for-discounts", "Pay early for discounts", "Payments", "approve", "If reserve stays ≥ ₹50L", False),
    ("hold-suspicious-payments", "Hold suspicious payments", "Risk", "auto", "Release needs a human", False),
    ("cancel-subscriptions", "Cancel subscriptions", "Spend Control", "approve", "Prepared, not executed", False),
    ("renew-or-terminate-contracts", "Renew or terminate contracts", "Contracts", "approve", "Owner + legal", False),
    ("sign-contracts", "Mark pending contracts signed", "Autopilot", "approve", "After the policy check", False),
    ("record-invoice-payments", "Record payment of due invoices", "Autopilot", "auto", "After the policy check", False),
    ("move-money-between-accounts", "Move money between accounts", "Cash-Flow", "never", "Out of scope", True),
    ("change-vendor-bank-details", "Change vendor bank details", "Risk", "never", "Hard-coded block", True),
]


class GuardrailLocked(ValueError):
    """The rule is locked and its mode cannot be changed."""


class ApprovalConflict(ValueError):
    """The approval already exists, or it is no longer pending."""


def _guardrail(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rule_id": row["rule_id"],
        "action": row["action"],
        "agent": row["agent"],
        "mode": row["mode"],
        "limit": row["limit_text"],
        "locked": row["locked"],
    }


class GovernanceStore:
    def __init__(self, uri: str):
        self._pool = shared_pool(uri)
        self._exec(DDL)
        self._seeded: set = set()

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

    # ------------------------------------------------------------------ guardrails
    def ensure_guardrails(self, tenant_id: str) -> None:
        """Create the default rules for a tenant the first time they are read. Existing rules are left alone."""
        for position, (rule_id, action, agent, mode, limit, locked) in enumerate(DEFAULT_GUARDRAILS):
            self._exec(
                """
                INSERT INTO backend_runtime.guardrails (tenant_id, rule_id, action, agent, mode, limit_text, locked, position)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, rule_id) DO NOTHING
                """,
                (tenant_id, rule_id, action, agent, mode, limit, locked, position),
            )

    def list_guardrails(self, tenant_id: str) -> List[Dict[str, Any]]:
        rows = self._exec(
            "SELECT rule_id, action, agent, mode, limit_text, locked FROM backend_runtime.guardrails "
            "WHERE tenant_id = %s ORDER BY position",
            (tenant_id,),
            fetch="all",
        )
        return [_guardrail(r) for r in rows]

    def mode(self, tenant_id: str, rule_id: str) -> str:
        """The guardrail's current mode; an unknown action is never run on its own."""
        if tenant_id not in self._seeded:
            self.ensure_guardrails(tenant_id)
            self._seeded.add(tenant_id)
        row = self._exec("SELECT mode FROM backend_runtime.guardrails WHERE tenant_id = %s AND rule_id = %s",
                         (tenant_id, rule_id), fetch="one")
        return row["mode"] if row else "never"

    def set_mode(self, tenant_id: str, rule_id: str, mode: str, user_id: str) -> Dict[str, Any]:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        row = self._exec(
            "SELECT rule_id, action, agent, mode, limit_text, locked FROM backend_runtime.guardrails "
            "WHERE tenant_id = %s AND rule_id = %s",
            (tenant_id, rule_id),
            fetch="one",
        )
        if row is None:
            raise KeyError(rule_id)
        if row["locked"]:
            raise GuardrailLocked(f"{rule_id} is locked and cannot be changed")
        self._exec(
            "UPDATE backend_runtime.guardrails SET mode = %s, updated_at = now(), updated_by = %s "
            "WHERE tenant_id = %s AND rule_id = %s",
            (mode, user_id, tenant_id, rule_id),
        )
        row["mode"] = mode
        return _guardrail(row)

    # ------------------------------------------------------------------ approvals
    def create_approval(self, tenant_id: str, approval_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.get_approval(tenant_id, approval_id) is not None:
            raise ApprovalConflict(f"approval {approval_id} already exists")
        self._exec(
            "INSERT INTO backend_runtime.approvals (approval_id, tenant_id, status, payload) VALUES (%s, %s, 'pending', %s)",
            (approval_id, tenant_id, Json({**payload, "status": "pending"})),
        )
        return self.get_approval(tenant_id, approval_id)

    def get_approval(self, tenant_id: str, approval_id: str) -> Optional[Dict[str, Any]]:
        row = self._exec(
            "SELECT approval_id, status, payload, requested_at, decided_at, decided_by, decision_note "
            "FROM backend_runtime.approvals WHERE tenant_id = %s AND approval_id = %s",
            (tenant_id, approval_id),
            fetch="one",
        )
        return _approval(row) if row else None

    def list_approvals(self, tenant_id: str, status: str = "pending") -> List[Dict[str, Any]]:
        if status == "all":
            rows = self._exec(
                "SELECT approval_id, status, payload, requested_at, decided_at, decided_by, decision_note "
                "FROM backend_runtime.approvals WHERE tenant_id = %s ORDER BY requested_at DESC",
                (tenant_id,),
                fetch="all",
            )
        else:
            rows = self._exec(
                "SELECT approval_id, status, payload, requested_at, decided_at, decided_by, decision_note "
                "FROM backend_runtime.approvals WHERE tenant_id = %s AND status = %s ORDER BY requested_at",
                (tenant_id, status),
                fetch="all",
            )
        return [_approval(r) for r in rows]

    def decide(self, tenant_id: str, approval_id: str, approved: bool, user_id: str, note: Optional[str]) -> Dict[str, Any]:
        current = self.get_approval(tenant_id, approval_id)
        if current is None:
            raise KeyError(approval_id)
        if current["status"] != "pending":
            raise ApprovalConflict(f"approval {approval_id} is already {current['status']}")

        status = "approved" if approved else "rejected"
        event = "Approved" if approved else "Rejected"
        payload = dict(current["payload"])
        payload["status"] = status
        payload["audit"] = list(payload.get("audit") or []) + [
            {
                "time": datetime.now().astimezone().strftime("%H:%M:%S"),
                "actor": user_id,
                "event": f"{event}: {note}" if note else event,
            }
        ]
        # The status check in the WHERE clause stops two decisions from both succeeding.
        updated = self._exec(
            "UPDATE backend_runtime.approvals SET status = %s, decided_at = now(), decided_by = %s, decision_note = %s, payload = %s "
            "WHERE tenant_id = %s AND approval_id = %s AND status = 'pending' RETURNING approval_id",
            (status, user_id, note, Json(payload), tenant_id, approval_id),
            fetch="one",
        )
        if updated is None:
            raise ApprovalConflict(f"approval {approval_id} was decided by someone else")
        return self.get_approval(tenant_id, approval_id)


def _approval(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "approval_id": row["approval_id"],
        "status": row["status"],
        "payload": row["payload"],
        "requested_at": row["requested_at"],
        "decided_at": row["decided_at"],
        "decided_by": row["decided_by"],
        "decision_note": row["decision_note"],
    }
