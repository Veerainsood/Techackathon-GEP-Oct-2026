"""Guardrails and approval queue, against the backend knowledge database. Each test uses its own tenant.

Run from the repository root:  python -m pytest tests/test_governance.py -v
"""

import uuid
from typing import Iterator

import pytest

from backend.config import Settings, pg_uri
from backend.governance.store import (
    DEFAULT_GUARDRAILS,
    ApprovalConflict,
    GovernanceStore,
    GuardrailLocked,
)


@pytest.fixture(scope="module")
def store() -> GovernanceStore:
    return GovernanceStore(pg_uri(Settings().knowledge_db))


def _purge(store: GovernanceStore, tenant_id: str) -> None:
    store._exec("DELETE FROM backend_runtime.approvals WHERE tenant_id = %s", (tenant_id,))
    store._exec("DELETE FROM backend_runtime.guardrails WHERE tenant_id = %s", (tenant_id,))


@pytest.fixture
def tenant(store) -> Iterator[str]:
    tenant_id = f"test-gov-{uuid.uuid4().hex[:10]}"
    yield tenant_id
    _purge(store, tenant_id)


def _action(action_id: str) -> dict:
    return {
        "id": action_id,
        "agent": "Payments",
        "title": "Schedule payment",
        "entity": "Vendor A",
        "amountL": 3.5,
        "impact": "Pays Vendor A on 30 Oct",
        "rationale": "Payment is due and within budget",
        "status": "pending",
        "autonomy": "L2",
        "approval": "CFO — payment > ₹2L",
        "reversible": False,
        "sources": [1],
        "checks": [],
        "audit": [{"time": "10:00:00", "actor": "Payments agent", "event": "Proposed"}],
    }


def test_defaults_are_seeded_once(store, tenant):
    store.ensure_guardrails(tenant)
    store.ensure_guardrails(tenant)  # second call must not duplicate rows
    rules = store.list_guardrails(tenant)
    # one rule per action autopilot can take (backend/policies/actions.py); nothing the system does not do
    assert [r["rule_id"] for r in rules] == [d[0] for d in DEFAULT_GUARDRAILS] == ["sign-contracts", "record-invoice-payments"]
    assert {r["rule_id"]: r["mode"] for r in rules} == {"sign-contracts": "approve", "record-invoice-payments": "auto"}


def test_rules_for_actions_that_do_not_exist_are_not_listed(store, tenant):
    store.ensure_guardrails(tenant)
    store._exec("INSERT INTO backend_runtime.guardrails (tenant_id, rule_id, action, agent, mode) VALUES (%s, 'move-money', 'Move money', 'x', 'auto') "
                "ON CONFLICT DO NOTHING", (tenant,))
    assert "move-money" not in {r["rule_id"] for r in store.list_guardrails(tenant)}
    with pytest.raises(KeyError):
        store.set_mode(tenant, "move-money", "never", "Meera Rao")


def test_mode_change_is_saved(store, tenant):
    store.ensure_guardrails(tenant)
    updated = store.set_mode(tenant, "sign-contracts", "auto", "Meera Rao")
    assert updated["mode"] == "auto"
    current = {r["rule_id"]: r for r in store.list_guardrails(tenant)}
    assert current["sign-contracts"]["mode"] == "auto"
    assert current["record-invoice-payments"]["mode"] == "auto"  # other rules untouched


def test_locked_rule_cannot_change(store, tenant):
    store.ensure_guardrails(tenant)
    store._exec("UPDATE backend_runtime.guardrails SET locked = TRUE, mode = 'never' WHERE tenant_id = %s AND rule_id = 'record-invoice-payments'",
                (tenant,))
    with pytest.raises(GuardrailLocked):
        store.set_mode(tenant, "record-invoice-payments", "auto", "Meera Rao")
    current = {r["rule_id"]: r for r in store.list_guardrails(tenant)}
    assert current["record-invoice-payments"]["mode"] == "never"


def test_unknown_rule_and_bad_mode_are_refused(store, tenant):
    store.ensure_guardrails(tenant)
    with pytest.raises(KeyError):
        store.set_mode(tenant, "no-such-rule", "auto", "Meera Rao")
    with pytest.raises(ValueError):
        store.set_mode(tenant, "sign-contracts", "sometimes", "Meera Rao")


def test_approval_is_created_pending_and_approved_once(store, tenant):
    created = store.create_approval(tenant, "PY-test-1", _action("PY-test-1"))
    assert created["status"] == "pending"
    assert [a["approval_id"] for a in store.list_approvals(tenant, "pending")] == ["PY-test-1"]

    decided = store.decide(tenant, "PY-test-1", True, "Meera Rao (CFO)", None)
    assert decided["status"] == "approved"
    assert decided["payload"]["status"] == "approved"
    assert decided["decided_by"] == "Meera Rao (CFO)"
    assert decided["payload"]["audit"][-1]["event"] == "Approved"
    assert store.list_approvals(tenant, "pending") == []

    with pytest.raises(ApprovalConflict):
        store.decide(tenant, "PY-test-1", False, "Someone else", None)


def test_rejection_keeps_the_note(store, tenant):
    store.create_approval(tenant, "PY-test-2", _action("PY-test-2"))
    rejected = store.decide(tenant, "PY-test-2", False, "Meera Rao (CFO)", "Amount too high this month")
    assert rejected["status"] == "rejected"
    assert rejected["decision_note"] == "Amount too high this month"
    assert rejected["payload"]["audit"][-1]["event"] == "Rejected: Amount too high this month"


def test_duplicate_approval_id_is_refused(store, tenant):
    store.create_approval(tenant, "PY-test-3", _action("PY-test-3"))
    with pytest.raises(ApprovalConflict):
        store.create_approval(tenant, "PY-test-3", _action("PY-test-3"))


def test_tenants_do_not_see_each_others_approvals(store, tenant):
    other = f"test-gov-{uuid.uuid4().hex[:10]}"
    try:
        store.create_approval(tenant, "PY-test-4", _action("PY-test-4"))
        assert store.list_approvals(other, "all") == []
        assert store.get_approval(other, "PY-test-4") is None
    finally:
        _purge(store, other)
