# DBA Reference to Synthetic Financial Schema Mapping

This document provides the complete 1-to-1 structural mapping between the 39 tables across 8 domains in `dba_ref.txt` and the fictional **Aethelgard Enterprise** synthetic financial schema.

## 1. Domain-Level Schema Mapping

| Original DBA Domain | Synthetic PostgreSQL Schema | Domain Responsibility | Table Count |
| :--- | :--- | :--- | :---: |
| `contracts` | `agreements` | Customer contract files, repository drives, PO/SOW citations, and counterparty aliases | 4 |
| `app` | `portal_app` | Assistant application users, multi-tenant conversations, and cited message logs | 3 |
| `ref` | `master_ref` | Global parent customer groups, legal entity jurisdictions, and cross-system dimension mappings | 5 |
| `erp` | `ledger_erp` | Accounts receivable, invoices, subledger settlements, products, dimensions, and General Ledger | 15 |
| `crm` | `pipeline_crm` | Prospect/customer accounts, sales opportunities, historical pipeline snapshots, teams, and quotas | 7 |
| `plan` | `fpna_plan` | Monthly budget and reforecast targets across standard reporting dimensions | 1 |
| `reporting` | `exec_reporting` | Precomputed monthly revenue position and forward-year revenue outlook snapshots | 2 |
| `ops` | `data_ops` | Upstream connector synchronization batch logs, row counts, and rejection diagnostics | 1 |
| **Total** | **8 Schemas** | **Complete Cross-System Enterprise Financial Architecture** | **39** |

## 2. Complete 39-Table Mapping Matrix

| # | Original Domain | Original DBA Table | Synthetic Schema.Table | Primary Key | Key Foreign Keys & Cross-Domain Relationships | Logical Responsibility |
| :-: | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `contracts` | `document_sources` | `agreements.repository_drives` | `drive_id` | *(Root / Self-Referencing)* | Folders or cloud repositories where customer agreement files are scanned from. |
| 2 | `contracts` | `contract_documents` | `agreements.agreement_files` | `agreement_id` | `drive_id` -> `agreements.repository_drives(drive_id)`, `matched_group_id` -> `master_ref.client_conglomerates(conglomerate_id)` | One row per contract file, with its type, dates, value, signature status and matched customer. |
| 3 | `contracts` | `document_references` | `agreements.agreement_citations` | `citation_id` | `agreement_id` -> `agreements.agreement_files(agreement_id)` | PO, SOW and contract numbers found inside a document. |
| 4 | `contracts` | `account_aliases` | `agreements.counterparty_aliases` | `alias_id` | `conglomerate_id` -> `master_ref.client_conglomerates(conglomerate_id)` | Other names for a customer, used to match documents to the right account. |
| 5 | `app` | `users` | `portal_app.platform_operators` | `operator_id` | *(Root / Self-Referencing)* | People who can log in, with their roles and tenant. |
| 6 | `app` | `conversations` | `portal_app.inquiry_threads` | `thread_id` | `operator_id` -> `portal_app.platform_operators(operator_id)` | A user's chat threads with the assistant. |
| 7 | `app` | `messages` | `portal_app.thread_utterances` | `utterance_id` | `thread_id` -> `portal_app.inquiry_threads(thread_id)` | Individual messages in a conversation, with citations and token usage. |
| 8 | `ref` | `customer_groups` | `master_ref.client_conglomerates` | `conglomerate_id` | *(Root / Self-Referencing)* | Parent groups that tie a customer's ERP and CRM records together. |
| 9 | `ref` | `company_regions` | `master_ref.entity_jurisdictions` | `legal_entity_code` | *(Root / Self-Referencing)* | Maps each legal entity (company code) to a region. |
| 10 | `ref` | `reporting_dimension_types` | `master_ref.taxonomy_facets` | `facet_code` | *(Root / Self-Referencing)* | Kinds of reporting dimension, e.g. region, product line or industry. |
| 11 | `ref` | `reporting_dimensions` | `master_ref.taxonomy_nodes` | `node_id` | `facet_code` -> `master_ref.taxonomy_facets(facet_code)` | The standard values for each dimension type. |
| 12 | `ref` | `reporting_dimension_mappings` | `master_ref.taxonomy_crosswalks` | `crosswalk_id` | `facet_code` -> `master_ref.taxonomy_facets(facet_code)`, `target_node_id` -> `master_ref.taxonomy_nodes(node_id)` | Converts raw values from each source system into a standard dimension value. |
| 13 | `erp` | `ar_customers` | `ledger_erp.receivable_debtors` | `debtor_id` | `conglomerate_id` -> `master_ref.client_conglomerates(conglomerate_id)`, `legal_entity_code` -> `master_ref.entity_jurisdictions(legal_entity_code)` | Customers as billed in the ERP, with payment terms, currency and address. |
| 14 | `erp` | `ar_invoice_headers` | `ledger_erp.sales_invoices` | `invoice_id` | `debtor_id` -> `ledger_erp.receivable_debtors(debtor_id)`, `legal_entity_code` -> `master_ref.entity_jurisdictions(legal_entity_code)`, `agreement_id` -> `agreements.agreement_files(agreement_id)` | One row per customer invoice, with totals, tax, open balance and due date. |
| 15 | `erp` | `ar_invoice_lines` | `ledger_erp.sales_invoice_items` | `invoice_item_id` | `invoice_id` -> `ledger_erp.sales_invoices(invoice_id)`, `catalog_item_id` -> `ledger_erp.catalog_offerings(catalog_item_id)`, `dim_bundle_id` -> `ledger_erp.tag_bundles(dim_bundle_id)` | Line items on each invoice, with quantity, price and service period. |
| 16 | `erp` | `ar_transactions` | `ledger_erp.receivable_ledger_events` | `ar_event_id` | `debtor_id` -> `ledger_erp.receivable_debtors(debtor_id)`, `applied_invoice_id` -> `ledger_erp.sales_invoices(invoice_id)`, `gl_voucher_id` -> `ledger_erp.journal_vouchers(voucher_id)` | Customer ledger entries (invoices, payments, credits) and how they were settled. |
| 17 | `erp` | `products` | `ledger_erp.catalog_offerings` | `catalog_item_id` | `default_rev_account_id` -> `ledger_erp.chart_of_accounts(account_id)` | Items and services that can be sold, with their revenue type and default account. |
| 18 | `erp` | `dimension_attributes` | `ledger_erp.analytic_axes` | `axis_id` | *(Root / Self-Referencing)* | The financial dimensions defined in the ERP, e.g. department or cost center. |
| 19 | `erp` | `dimension_values` | `ledger_erp.analytic_axis_members` | `member_id` | `axis_id` -> `ledger_erp.analytic_axes(axis_id)` | The allowed values for each financial dimension. |
| 20 | `erp` | `dimension_sets` | `ledger_erp.tag_bundles` | `dim_bundle_id` | *(Root / Self-Referencing)* | A saved combination of dimension values that a transaction is tagged with. |
| 21 | `erp` | `dimension_set_values` | `ledger_erp.tag_bundle_members` | `bundle_member_id` | `dim_bundle_id` -> `ledger_erp.tag_bundles(dim_bundle_id)`, `axis_id` -> `ledger_erp.analytic_axes(axis_id)`, `member_id` -> `ledger_erp.analytic_axis_members(member_id)` | Breaks each dimension set into its individual values. |
| 22 | `erp` | `gl_account_categories` | `ledger_erp.ledger_statement_groups` | `stmt_group_id` | *(Root / Self-Referencing)* | Groups of general ledger accounts used for financial statements. |
| 23 | `erp` | `gl_accounts` | `ledger_erp.chart_of_accounts` | `account_id` | `stmt_group_id` -> `ledger_erp.ledger_statement_groups(stmt_group_id)` | The chart of accounts: account number, type and normal balance. |
| 24 | `erp` | `gl_journal_headers` | `ledger_erp.journal_vouchers` | `voucher_id` | `legal_entity_code` -> `master_ref.entity_jurisdictions(legal_entity_code)` | One row per journal entry, with its fiscal period, dates and voucher. |
| 25 | `erp` | `gl_journal_lines` | `ledger_erp.journal_postings` | `posting_line_id` | `voucher_id` -> `ledger_erp.journal_vouchers(voucher_id)`, `account_id` -> `ledger_erp.chart_of_accounts(account_id)`, `dim_bundle_id` -> `ledger_erp.tag_bundles(dim_bundle_id)` | The debit and credit lines of each journal entry. |
| 26 | `erp` | `legacy_account_map` | `ledger_erp.legacy_coa_crosswalk` | `legacy_map_id` | `modern_account_id` -> `ledger_erp.chart_of_accounts(account_id)` | Maps old account numbers to the current chart of accounts. |
| 27 | `erp` | `expense_categories` | `ledger_erp.cost_taxonomy_nodes` | `cost_node_id` | *(Root / Self-Referencing)* | The expense hierarchy (function down to expense line), each row pointing to its parent. |
| 28 | `erp` | `expense_account_map` | `ledger_erp.cost_account_links` | `link_id` | `cost_node_id` -> `ledger_erp.cost_taxonomy_nodes(cost_node_id)`, `account_id` -> `ledger_erp.chart_of_accounts(account_id)` | Assigns GL accounts to expense categories. |
| 29 | `crm` | `accounts` | `pipeline_crm.prospect_accounts` | `crm_account_id` | `conglomerate_id` -> `master_ref.client_conglomerates(conglomerate_id)`, `account_owner_rep_id` -> `pipeline_crm.quota_carriers(rep_id)` | Customer accounts in the CRM, with region, industry and segment. |
| 30 | `crm` | `opportunities` | `pipeline_crm.deal_opportunities` | `deal_id` | `crm_account_id` -> `pipeline_crm.prospect_accounts(crm_account_id)`, `owner_rep_id` -> `pipeline_crm.quota_carriers(rep_id)`, `forecast_cat_code` -> `pipeline_crm.forecast_buckets(forecast_cat_code)`, `linked_agreement_id` -> `agreements.agreement_files(agreement_id)` | Current deals, with stage, probability, contract value and close date. |
| 31 | `crm` | `opportunity_snapshots` | `pipeline_crm.deal_history_snapshots` | `snapshot_row_id` | `deal_id` -> `pipeline_crm.deal_opportunities(deal_id)` | Dated copies of each deal, used to track how the pipeline changes over time. |
| 32 | `crm` | `opportunity_line_items` | `pipeline_crm.deal_product_splits` | `deal_split_id` | `deal_id` -> `pipeline_crm.deal_opportunities(deal_id)`, `catalog_item_id` -> `ledger_erp.catalog_offerings(catalog_item_id)` | How each deal's value splits across product categories. |
| 33 | `crm` | `sales_teams` | `pipeline_crm.territory_squads` | `squad_id` | *(Root / Self-Referencing)* | Sales teams, nested under parent teams. |
| 34 | `crm` | `sales_reps` | `pipeline_crm.quota_carriers` | `rep_id` | `squad_id` -> `pipeline_crm.territory_squads(squad_id)` | Salespeople, with their team, manager and quota. |
| 35 | `crm` | `forecast_categories` | `pipeline_crm.forecast_buckets` | `forecast_cat_code` | *(Root / Self-Referencing)* | Forecast categories and the default win probability for each. |
| 36 | `plan` | `budget_lines` | `fpna_plan.financial_targets` | `target_line_id` | `region_node_id` -> `master_ref.taxonomy_nodes(node_id)`, `product_line_node_id` -> `master_ref.taxonomy_nodes(node_id)`, `industry_node_id` -> `master_ref.taxonomy_nodes(node_id)`, `cost_node_id` -> `ledger_erp.cost_taxonomy_nodes(cost_node_id)` | Budget or forecast amounts by month and reporting dimension. |
| 37 | `reporting` | `revenue_position_snapshots` | `exec_reporting.monthly_revenue_posture` | `posture_id` | `region_node_id` -> `master_ref.taxonomy_nodes(node_id)`, `product_line_node_id` -> `master_ref.taxonomy_nodes(node_id)`, `industry_node_id` -> `master_ref.taxonomy_nodes(node_id)` | Monthly summary of contracted revenue versus pipeline, by product line, industry and region. |
| 38 | `reporting` | `forward_year_snapshots` | `exec_reporting.horizon_revenue_outlook` | `outlook_id` | `region_node_id` -> `master_ref.taxonomy_nodes(node_id)`, `product_line_node_id` -> `master_ref.taxonomy_nodes(node_id)` | Monthly view of next year's revenue: contracted, and contracted plus pipeline. |
| 39 | `ops` | `sync_runs` | `data_ops.ingestion_batches` | `batch_id` | *(Root / Self-Referencing)* | A log of each data load from a source system, with status, row counts and errors. |

## 3. Built-in Financial Invariants & Deliberate Evaluation Edge Cases

1. **Invoice Header vs Line Item Reconciliation**: For every invoice in `ledger_erp.sales_invoices`, `subtotal_amount_doc = SUM(sales_invoice_items.line_net_amount_doc)` and `subtotal_amount_usd = SUM(sales_invoice_items.line_net_amount_usd)`.
2. **AR Subledger Open Balance Invariant**: For every active (non-VOID) invoice, `open_balance_usd = gross_total_usd + SUM(receivable_ledger_events.signed_amount_usd WHERE event_type != 'INVOICE_POST')`.
3. **Double-Entry General Ledger Balance**: Every posted journal voucher in `ledger_erp.journal_vouchers` satisfies `SUM(debit_amount_usd) = SUM(credit_amount_usd)` across `ledger_erp.journal_postings`.
4. **Deliberate Edge Cases for Agent Verification**:
   - **Multi-Currency Transactions**: Invoices billed in `USD`, `EUR`, and `GBP` with explicit `fx_rate_to_usd`. Naive summation of `gross_total_doc` across currencies produces an invalid mixed-currency number; agents must use `*_usd` or group by currency.
   - **Partial Payments & Credit Memos**: Invoices with `PARTIALLY_PAID` and `CREDITED` statuses where `gross_total_usd != open_balance_usd`.
   - **Ambiguous / Non-Revenue Statuses**: Includes `VOID` invoices, `Draft` / `Expired` contracts in `agreements.agreement_files`, and `PENDING_APPROVAL` / `REVERSED` journal vouchers in `ledger_erp.journal_vouchers`.
   - **Multiple Customer Identities & Missing Mappings**: Some ERP debtors (`receivable_debtors.conglomerate_id IS NULL`) and contract documents (`matched_group_id IS NULL`) lack direct parent links and can only be linked via `agreements.counterparty_aliases` or source-specific names.
   - **Duplicate-Prone Joins (1-to-Many Fanout)**: Joining `sales_invoices` to `sales_invoice_items` or `receivable_ledger_events` and summing header-level `gross_total_usd` or `open_balance_usd` causes fanout inflation unless aggregated at the header grain or using `COUNT(DISTINCT invoice_id)`.
   - **Cross-System Dimension Terminology**: ERP uses codes like `GEO-NA`, `GEO-EU`, `GEO-AP` while CRM uses `North America`, `Europe`, `Asia Pacific`, reconciled through `master_ref.taxonomy_crosswalks` to standard nodes (`AMER`, `EMEA`, `APAC`).