"""What autopilot can do. Each action changes records of one policy scope (pending contracts or pending invoices), is
checked against that scope's policies, and has a guardrail that decides who runs it. The Guardrails screen lists exactly
these actions, and only for scopes that have an active policy (without a policy, autopilot does nothing there)."""

ACTIONS = {
    "sign_contract": {"collection": "pending_contracts", "guardrail": "sign-contracts", "label": "Mark pending contracts signed",
                      "default_mode": "approve", "changes": {"execution_status": "Fully_Signed"}, "title": "Mark contract {ref} signed",
                      "approver": "Contract owner"},
    "record_payment": {"collection": "pending_invoices", "guardrail": "record-invoice-payments", "label": "Record payment of overdue invoices",
                       "default_mode": "auto", "changes": {"settlement_status": "PAID", "open_balance_usd": 0},
                       "title": "Record payment of overdue invoice {ref}", "approver": "AR manager"},
}
GUARDRAIL_SCOPE = {a["guardrail"]: a["collection"] for a in ACTIONS.values()}
