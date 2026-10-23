# API examples

Captured from real calls against the running engine (`python scripts/generate_api_examples.py`). Long lists are truncated.

Start the server with `uvicorn udre.api.app:app --port 8000`. Identity headers: `X-Tenant-Id`, `X-User-Id`.

## GET /health

Response (200):

```json
{
  "status": "ok",
  "sources": [
    "finance_pg",
    "uploads"
  ]
}
```

## GET /sources

Response (200):

```json
[
  {
    "source_id": "finance_pg",
    "name": "Finance warehouse (PostgreSQL)",
    "type": "postgresql",
    "dialect": "postgres",
    "collections": 39,
    "read_only": true,
    "max_rows": 5000
  },
  {
    "source_id": "uploads",
    "name": "Uploaded files",
    "type": "csv",
    "dialect": "duckdb",
    "collections": 6,
    "read_only": true,
    "max_rows": 5000
  }
]
```

## GET /sources/finance_pg/collections?q=customer%20invoices%20open%20balance

Response (200):

```json
[
  {
    "source_id": "finance_pg",
    "collection": "ledger_erp.sales_invoices",
    "summary": "One row per customer invoice, with totals, tax, open balance and due date. Concepts: invoice, revenue, open_balance, tax.",
    "rows": 889,
    "metrics": [
      "fx_rate_to_usd",
      "subtotal_amount_doc",
      "tax_amount_doc",
      "gross_total_doc",
      "... 4 more"
    ],
    "dimensions": [
      "debtor_id",
      "legal_entity_code",
      "fiscal_period",
      "doc_currency",
      "... 1 more"
    ],
    "keys": [
      "invoice_id",
      "debtor_id",
      "legal_entity_code",
      "agreement_id"
    ],
    "joinable_with": [
      "agreements.agreement_files",
      "ledger_erp.receivable_debtors",
      "ledger_erp.receivable_ledger_events",
      "ledger_erp.sales_invoice_items",
      "... 1 more"
    ],
    "relevance": 1.056
  },
  {
    "source_id": "finance_pg",
    "collection": "ledger_erp.sales_invoice_items",
    "summary": "Line items on each invoice, with quantity, price and service period. Concepts: invoice, revenue, fiscal_period.",
    "rows": 1776,
    "metrics": [
      "quantity",
      "unit_price_doc",
      "line_net_amount_doc",
      "line_net_amount_usd"
    ],
    "dimensions": [
      "catalog_item_id",
      "dim_bundle_id"
    ],
    "keys": [
      "invoice_item_id",
      "invoice_id",
      "catalog_item_id",
      "dim_bundle_id"
    ],
    "joinable_with": [
      "ledger_erp.catalog_offerings",
      "ledger_erp.sales_invoices",
      "ledger_erp.tag_bundles"
    ],
    "relevance": 0.584
  },
  {
    "source_id": "finance_pg",
    "collection": "ledger_erp.receivable_ledger_events",
    "summary": "Customer ledger entries (invoices, payments, credits) and how they were settled. Concepts: receivable, invoice, payment, credit_note.",
    "rows": 1742,
    "metrics": [
      "signed_amount_doc",
      "signed_amount_usd"
    ],
    "dimensions": [
      "debtor_id",
      "event_type",
      "fiscal_period",
      "is_settled"
    ],
    "keys": [
      "ar_event_id",
      "debtor_id",
      "applied_invoice_id",
      "gl_voucher_id"
    ],
    "joinable_with": [
      "ledger_erp.journal_vouchers",
      "ledger_erp.receivable_debtors",
      "ledger_erp.sales_invoices"
    ],
    "relevance": 0.286
  },
  {
    "source_id": "finance_pg",
    "collection": "master_ref.client_conglomerates",
    "summary": "Parent groups that tie a customer's ERP and CRM records together. Concepts: customer.",
    "rows": 40,
    "metrics": [],
    "dimensions": [
      "conglomerate_name",
      "hq_country_iso",
      "global_tier",
      "primary_industry_code",
      "... 2 more"
    ],
    "keys": [
      "conglomerate_id"
    ],
    "joinable_with": [
      "agreements.agreement_files",
      "agreements.counterparty_aliases",
      "ledger_erp.receivable_debtors",
      "pipeline_crm.prospect_accounts"
    ],
    "relevance": 0.197
  },
  "... 35 more"
]
```

## POST /ask

Request:

```json
{
  "question": "What is the total open balance in USD of invoices for debtors with payment terms NET45?",
  "mode": "dual",
  "use_memory": true,
  "export_formats": [
    "excel"
  ]
}
```

Response (200):

```json
{
  "question": "What is the total open balance in USD of invoices for debtors with payment terms NET45?",
  "answer": "Total of open_balance_usd: 1,098,224.66 USD",
  "verification_status": "verified_fact",
  "selected_path": "Reconciled_Consensus",
  "agreement_status": "exact_match",
  "confidence_score": 0.8,
  "data": [
    {
      "value": 1098224.66
    }
  ],
  "query": "SELECT SUM(t0.\"open_balance_usd\") AS value FROM \"ledger_erp\".\"sales_invoices\" t0 JOIN \"ledger_erp\".\"receivable_debtors\" t1 ON t0.\"debtor_id\" = t1.\"debtor_id\" WHERE t1.\"payment_terms_code\" = 'NET45'",
  "source_id": "finance_pg",
  "query_lineage": [
    "Explorer: SELECT SUM(t0.\"open_balance_usd\") AS value FROM \"ledger_erp\".\"sales_invoices\" t0 JOIN \"ledger_erp\".\"receivable_debtors\" t1 ON t0.\"debtor_id\" = t1.\"debtor_id\" WHERE t1.\"payment_terms_code\" = 'NET45'",
    "Hercules: SELECT SUM(t0.\"open_balance_usd\") AS value FROM \"ledger_erp\".\"sales_invoices\" t0 JOIN \"ledger_erp\".\"receivable_debtors\" t1 ON t0.\"debtor_id\" = t1.\"debtor_id\" WHERE t1.\"payment_terms_code\" = 'NET45'"
  ],
  "disagreement_analysis": null,
  "assumptions": [],
  "evidence": [
    {
      "id": "ev:2af5d09cfc470578",
      "type": "query_result",
      "action": "SELECT SUM(t0.\"open_balance_usd\") AS value FROM \"ledger_erp\".\"sales_invoices\" t0 JOIN \"ledger_erp\".\"receivable_debtors\" t1 ON t0.\"debtor_id\" = t1.\"debtor_id\" WHERE t1.\"payment_terms_code\" = 'NET45'",
      "observed": "1 rows; first=[{'value': 1098224.66}]"
    },
    {
      "id": "ev:70642d61ffa1803b",
      "type": "query_result",
      "action": "SELECT COUNT(*) AS n FROM \"ledger_erp\".\"receivable_debtors\" WHERE \"payment_terms_code\" = 'NET45'",
      "observed": "1 rows; first=[{'n': 9}]"
    },
    {
      "id": "ev:e5e6fb838abbb839",
      "type": "query_result",
      "action": "SELECT SUM(m) AS value FROM (SELECT DISTINCT t0.\"invoice_id\" AS k, t0.\"open_balance_usd\" AS m FROM \"ledger_erp\".\"sales_invoices\" t0 JOIN \"ledger_erp\".\"receivable_debtors\" t1 ON t0.\"debtor_id\" = t1.\"debtor_id\" WHERE t1.\"payment_terms_code\" = 'NET45') d",
      "observed": "1 rows; first=[{'value': 1098224.66}]"
    },
    {
      "id": "ev:d1582b9c3dc26837",
      "type": "row_sample",
      "action": "sample ledger_erp.sales_invoices",
      "observed": "3 rows; first=[{'invoice_id': 'INV-000001', 'invoice_number': 'AE-US-2024-000001', 'debtor_id': 'DBT-00002', 'legal_entity_code': 'AETH-US', 'agreement_id': 'AGR-00002', 'invoice_date': '2024-01-01', 'due_date': '2"
    },
    "... 2 more"
  ],
  "validation": [
    {
      "validator": "syntax_and_columns",
      "passed": true,
      "severity": "info",
      "message": "parsed; 2 tables and 3 columns exist"
    },
    {
      "validator": "source_binding",
      "passed": true,
      "severity": "info",
      "message": "bound to finance_pg: ['ledger_erp.receivable_debtors', 'ledger_erp.sales_invoices']"
    },
    {
      "validator": "execution",
      "passed": true,
      "severity": "info",
      "message": "1 rows in 3.3 ms"
    },
    {
      "validator": "join_multiplicity",
      "passed": true,
      "severity": "info",
      "message": "joins preserve grain (186 joined rows, 186 base rows)"
    },
    "... 6 more"
  ],
  "exports": [
    {
      "path": "C:\\Users\\veerain.sood\\Desktop\\Hackathon 23-Oct-26\\exports\\What_is_the_total_open_balance_in_USD_of_20261023T065924122158.xlsx",
      "format": "excel",
      "rows": 1
    }
  ],
  "total_ms": 2711.2
}
```

## POST /ask

Request:

```json
{
  "question": "What was our revenue last year?",
  "mode": "dual"
}
```

Response (200):

```json
{
  "question": "What was our revenue last year?",
  "answer": "The question is ambiguous, so no single verified answer is given. Best-supported reading: Total of recognized_gl_revenue_usd: 14,574,378.13 USD (via SUM of exec_reporting.monthly_revenue_posture.recognized_gl_revenue_usd where snapshot_period LIKE 2025-%). Please specify: 'last year' interpreted as calendar year 2025; 'last year' interpreted as calendar year 2025 (fiscal calendar not confirmed)",
  "verification_status": "inferred_candidate",
  "selected_path": "Abstained_With_Uncertainty",
  "agreement_status": "material_uncertainty",
  "confidence_score": 0.3,
  "data": [
    {
      "value": 14574378.13
    }
  ],
  "query": "SELECT SUM(t0.\"recognized_gl_revenue_usd\") AS value FROM \"exec_reporting\".\"monthly_revenue_posture\" t0 WHERE CAST(t0.\"snapshot_period\" AS VARCHAR) LIKE '2025-%'",
  "source_id": "finance_pg",
  "query_lineage": [
    "Explorer: SELECT SUM(t0.\"recognized_gl_revenue_usd\") AS value FROM \"exec_reporting\".\"monthly_revenue_posture\" t0 WHERE CAST(t0.\"snapshot_period\" AS VARCHAR) LIKE '2025-%'",
    "Hercules: SELECT SUM(t0.\"recognized_gl_revenue_usd\") AS value FROM \"exec_reporting\".\"monthly_revenue_posture\" t0 WHERE CAST(t0.\"snapshot_period\" AS VARCHAR) LIKE '2025-%'"
  ],
  "disagreement_analysis": " | question admits several definitions: 'last year' interpreted as calendar year 2025; 'last year' interpreted as calendar year 2025 (fiscal calendar not confirmed)",
  "assumptions": [
    "'last year' interpreted as calendar year 2025",
    "'last year' interpreted as calendar year 2025 (fiscal calendar not confirmed)",
    "relative period 'last year' requires an assumed reference date"
  ],
  "evidence": [
    {
      "id": "ev:97c7796f64a4aa7a",
      "type": "query_result",
      "action": "SELECT SUM(t0.\"recognized_gl_revenue_usd\") AS value FROM \"exec_reporting\".\"monthly_revenue_posture\" t0 WHERE CAST(t0.\"snapshot_period\" AS VARCHAR) LIKE '2025-%'",
      "observed": "1 rows; first=[{'value': 14574378.13}]"
    },
    {
      "id": "ev:da735bfc7bf70f8a",
      "type": "row_sample",
      "action": "sample exec_reporting.monthly_revenue_posture",
      "observed": "3 rows; first=[{'posture_id': 'MRP-000001', 'snapshot_period': '2024-01', 'region_node_id': 'TXN-001', 'product_line_node_id': 'TXN-007', 'industry_node_id': 'TXN-009', 'recognized_gl_revenue_usd': 0.0, 'contracted"
    },
    {
      "id": "ev:a1d72dfc4ff06a70",
      "type": "query_result",
      "action": "SELECT SUM(t0.\"recognized_gl_revenue_usd\") AS value FROM \"exec_reporting\".\"monthly_revenue_posture\" t0 WHERE CAST(t0.\"snapshot_period\" AS VARCHAR) LIKE '2025-%'",
      "observed": "1 rows; first=[{'value': 14574378.13}]"
    },
    {
      "id": "ev:8844f9955d91a700",
      "type": "query_result",
      "action": "SELECT COUNT(*) AS n FROM \"exec_reporting\".\"monthly_revenue_posture\"",
      "observed": "1 rows; first=[{'n': 985}]"
    }
  ],
  "validation": [
    {
      "validator": "syntax_and_columns",
      "passed": true,
      "severity": "info",
      "message": "parsed; 1 tables and 2 columns exist"
    },
    {
      "validator": "source_binding",
      "passed": true,
      "severity": "info",
      "message": "bound to finance_pg: ['exec_reporting.monthly_revenue_posture']"
    },
    {
      "validator": "execution",
      "passed": true,
      "severity": "info",
      "message": "1 rows in 2.7 ms"
    },
    {
      "validator": "join_multiplicity",
      "passed": true,
      "severity": "info",
      "message": "single collection; no join fan-out possible"
    },
    "... 6 more"
  ],
  "exports": [],
  "total_ms": 1729.8
}
```

## POST /query

Request:

```json
{
  "source_id": "finance_pg",
  "query": "UPDATE ledger_erp.sales_invoices SET open_balance_usd = 0"
}
```

Response (200):

```json
{
  "source_id": "finance_pg",
  "query": "UPDATE ledger_erp.sales_invoices SET open_balance_usd = 0",
  "succeeded": false,
  "rows": [],
  "columns": [],
  "row_count": 0,
  "truncated": false,
  "execution_time_ms": 0.0,
  "error_message": "REJECTED: only read queries are allowed, got Update",
  "query_plan": null
}
```

## GET /knowledge/search?q=closed%20won&k=3

Response (200):

```json
[
  {
    "id": "val:finance_pg:pipeline_crm.deal_opportunities.pipeline_stage=Closed_Won",
    "type": "ValueMapping",
    "label": "Closed_Won",
    "status": "verified_fact",
    "score": 0.716
  },
  {
    "id": "val:uploads:crm_opportunities_dump.pipeline_stage=Closed_Won",
    "type": "ValueMapping",
    "label": "Closed_Won",
    "status": "verified_fact",
    "score": 0.716
  },
  {
    "id": "val:finance_pg:pipeline_crm.deal_history_snapshots.stage_at_snapshot=Closed_Won",
    "type": "ValueMapping",
    "label": "Closed_Won",
    "status": "verified_fact",
    "score": 0.675
  }
]
```

## GET /knowledge/stats

Response (200):

```json
{
  "graph": {
    "nodes": [
      {
        "type": "Collection",
        "status": "verified_fact",
        "count": 45
      },
      {
        "type": "Column",
        "status": "verified_fact",
        "count": 367
      },
      {
        "type": "Concept",
        "status": "inferred_candidate",
        "count": 31
      },
      {
        "type": "Dimension",
        "status": "verified_fact",
        "count": 190
      },
      "... 3 more"
    ],
    "edges": [
      {
        "type": "CONTAINS",
        "status": "verified_fact",
        "count": 45
      },
      {
        "type": "DERIVED_FROM",
        "status": "verified_fact",
        "count": 196
      },
      {
        "type": "HAS_COLUMN",
        "status": "verified_fact",
        "count": 367
      },
      {
        "type": "MAPS_TO",
        "status": "verified_fact",
        "count": 1543
      },
      "... 4 more"
    ],
    "evidence_records": 138
  },
  "memory": {
    "value_mapping:stale": 18,
    "query_template:stale": 17,
    "query_template:verified": 4,
    "value_mapping:verified": 2
  },
  "context": {
    "raw_bytes": 354254,
    "compressed_bytes": 86119,
    "compression_ratio": 4.11,
    "raw_tokens": 101474,
    "compressed_tokens": 26216,
    "token_reduction_pct": 74.2,
    "template_count": 1,
    "dictionary_entries": 166,
    "collection_count": 45
  }
}
```
