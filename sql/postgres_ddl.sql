-- ============================================================================
-- Aethelgard Enterprise Synthetic Financial Database - Complete PostgreSQL DDL
-- 39 Tables across 8 Domains + Ontology/Knowledge Graph Storage
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS agreements; -- Maps DBA domain: contracts
CREATE SCHEMA IF NOT EXISTS portal_app; -- Maps DBA domain: app
CREATE SCHEMA IF NOT EXISTS master_ref; -- Maps DBA domain: ref
CREATE SCHEMA IF NOT EXISTS ledger_erp; -- Maps DBA domain: erp
CREATE SCHEMA IF NOT EXISTS pipeline_crm; -- Maps DBA domain: crm
CREATE SCHEMA IF NOT EXISTS fpna_plan; -- Maps DBA domain: plan
CREATE SCHEMA IF NOT EXISTS exec_reporting; -- Maps DBA domain: reporting
CREATE SCHEMA IF NOT EXISTS data_ops; -- Maps DBA domain: ops
CREATE SCHEMA IF NOT EXISTS udre_kg; -- Persistent Ontology & Knowledge Graph

-- Domain: contracts.document_sources -> agreements.repository_drives
-- Purpose: Folders or cloud repositories where customer agreement files are scanned from.
CREATE TABLE IF NOT EXISTS agreements.repository_drives (
    drive_id VARCHAR(32) NOT NULL,
    drive_label VARCHAR(120) NOT NULL,
    storage_provider VARCHAR(40) NOT NULL,
    root_uri VARCHAR(255) NOT NULL,
    owning_region_code VARCHAR(16) NULL,
    scan_enabled BOOLEAN NOT NULL,
    last_indexed_at TIMESTAMP NULL,
    PRIMARY KEY (drive_id)
);
COMMENT ON TABLE agreements.repository_drives IS 'Folders or cloud repositories where customer agreement files are scanned from.';
COMMENT ON COLUMN agreements.repository_drives.drive_id IS 'Unique repository drive identifier';
COMMENT ON COLUMN agreements.repository_drives.drive_label IS 'Human-readable drive or bucket name';
COMMENT ON COLUMN agreements.repository_drives.storage_provider IS 'Provider type (ObjectStore, NetworkShare, DocVault)';
COMMENT ON COLUMN agreements.repository_drives.root_uri IS 'Root path or URI scanned';
COMMENT ON COLUMN agreements.repository_drives.owning_region_code IS 'Regional custodian code';
COMMENT ON COLUMN agreements.repository_drives.scan_enabled IS 'Whether automated ingestion is active';
COMMENT ON COLUMN agreements.repository_drives.last_indexed_at IS 'Timestamp of last repository scan';

-- Domain: contracts.contract_documents -> agreements.agreement_files
-- Purpose: One row per contract file, with its type, dates, value, signature status and matched customer.
CREATE TABLE IF NOT EXISTS agreements.agreement_files (
    agreement_id VARCHAR(32) NOT NULL,
    drive_id VARCHAR(32) NOT NULL,
    file_title VARCHAR(200) NOT NULL,
    agreement_type VARCHAR(40) NOT NULL,
    execution_status VARCHAR(40) NOT NULL,
    matched_group_id VARCHAR(32) NULL,
    raw_counterparty_name VARCHAR(160) NOT NULL,
    effective_start_date DATE NOT NULL,
    expiration_date DATE NOT NULL,
    contracted_tcv_amount NUMERIC(18,2) NOT NULL,
    annual_committed_arr NUMERIC(18,2) NOT NULL,
    doc_currency_code VARCHAR(8) NOT NULL,
    fx_rate_to_usd NUMERIC(12,6) NOT NULL,
    contracted_tcv_usd NUMERIC(18,2) NOT NULL,
    signed_date DATE NULL,
    PRIMARY KEY (agreement_id)
);
COMMENT ON TABLE agreements.agreement_files IS 'One row per contract file, with its type, dates, value, signature status and matched customer.';
COMMENT ON COLUMN agreements.agreement_files.agreement_id IS 'Unique contract file identifier';
COMMENT ON COLUMN agreements.agreement_files.drive_id IS 'Source repository drive';
COMMENT ON COLUMN agreements.agreement_files.file_title IS 'Contract document filename';
COMMENT ON COLUMN agreements.agreement_files.agreement_type IS 'Document type (MSA, SOW, Order_Form, Amendment, Renewal)';
COMMENT ON COLUMN agreements.agreement_files.execution_status IS 'Signature status (Fully_Signed, Counterpart_Pending, Draft, Expired, Terminated)';
COMMENT ON COLUMN agreements.agreement_files.matched_group_id IS 'Matched parent customer group (nullable when unmatched)';
COMMENT ON COLUMN agreements.agreement_files.raw_counterparty_name IS 'Customer name extracted from document text';
COMMENT ON COLUMN agreements.agreement_files.effective_start_date IS 'Contract effective start date';
COMMENT ON COLUMN agreements.agreement_files.expiration_date IS 'Contract expiration or renewal date';
COMMENT ON COLUMN agreements.agreement_files.contracted_tcv_amount IS 'Total contracted value in document currency';
COMMENT ON COLUMN agreements.agreement_files.annual_committed_arr IS 'Annualized committed recurring value';
COMMENT ON COLUMN agreements.agreement_files.doc_currency_code IS 'ISO currency code on agreement';
COMMENT ON COLUMN agreements.agreement_files.fx_rate_to_usd IS 'Conversion rate to USD reporting currency';
COMMENT ON COLUMN agreements.agreement_files.contracted_tcv_usd IS 'Total contracted value normalized to USD';
COMMENT ON COLUMN agreements.agreement_files.signed_date IS 'Date of final signature execution';

-- Domain: contracts.document_references -> agreements.agreement_citations
-- Purpose: PO, SOW and contract numbers found inside a document.
CREATE TABLE IF NOT EXISTS agreements.agreement_citations (
    citation_id VARCHAR(32) NOT NULL,
    agreement_id VARCHAR(32) NOT NULL,
    ref_kind VARCHAR(32) NOT NULL,
    ref_token VARCHAR(80) NOT NULL,
    extraction_confidence NUMERIC(5,4) NOT NULL,
    page_number INTEGER NOT NULL,
    PRIMARY KEY (citation_id)
);
COMMENT ON TABLE agreements.agreement_citations IS 'PO, SOW and contract numbers found inside a document.';
COMMENT ON COLUMN agreements.agreement_citations.citation_id IS 'Unique reference citation ID';
COMMENT ON COLUMN agreements.agreement_citations.agreement_id IS 'Parent agreement document';
COMMENT ON COLUMN agreements.agreement_citations.ref_kind IS 'Reference category (PO_NUMBER, SOW_CODE, MASTER_CONTRACT_NO, QUOTE_REF)';
COMMENT ON COLUMN agreements.agreement_citations.ref_token IS 'Extracted identifier token';
COMMENT ON COLUMN agreements.agreement_citations.extraction_confidence IS 'OCR/NLP extraction confidence score';
COMMENT ON COLUMN agreements.agreement_citations.page_number IS 'Page where reference was extracted';

-- Domain: contracts.account_aliases -> agreements.counterparty_aliases
-- Purpose: Other names for a customer, used to match documents to the right account.
CREATE TABLE IF NOT EXISTS agreements.counterparty_aliases (
    alias_id VARCHAR(32) NOT NULL,
    conglomerate_id VARCHAR(32) NOT NULL,
    alias_name VARCHAR(160) NOT NULL,
    alias_source VARCHAR(40) NOT NULL,
    is_verified_match BOOLEAN NOT NULL,
    PRIMARY KEY (alias_id)
);
COMMENT ON TABLE agreements.counterparty_aliases IS 'Other names for a customer, used to match documents to the right account.';
COMMENT ON COLUMN agreements.counterparty_aliases.alias_id IS 'Unique alias record ID';
COMMENT ON COLUMN agreements.counterparty_aliases.conglomerate_id IS 'Canonical customer group';
COMMENT ON COLUMN agreements.counterparty_aliases.alias_name IS 'Alternative DBA, legal, or legacy name';
COMMENT ON COLUMN agreements.counterparty_aliases.alias_source IS 'Where alias originated (Legal_Filing, OCR_Variant, M&A_Legacy, CRM_Nickname)';
COMMENT ON COLUMN agreements.counterparty_aliases.is_verified_match IS 'Whether data steward verified the alias';

-- Domain: app.users -> portal_app.platform_operators
-- Purpose: People who can log in, with their roles and tenant.
CREATE TABLE IF NOT EXISTS portal_app.platform_operators (
    operator_id VARCHAR(32) NOT NULL,
    email_handle VARCHAR(150) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    security_role VARCHAR(40) NOT NULL,
    tenant_org_id VARCHAR(32) NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMP NOT NULL,
    PRIMARY KEY (operator_id)
);
COMMENT ON TABLE portal_app.platform_operators IS 'People who can log in, with their roles and tenant.';
COMMENT ON COLUMN portal_app.platform_operators.operator_id IS 'Unique user operator ID';
COMMENT ON COLUMN portal_app.platform_operators.email_handle IS 'Corporate login email';
COMMENT ON COLUMN portal_app.platform_operators.display_name IS 'Full display name';
COMMENT ON COLUMN portal_app.platform_operators.security_role IS 'Access role (Finance_Controller, RevOps_Analyst, Executive, Auditor)';
COMMENT ON COLUMN portal_app.platform_operators.tenant_org_id IS 'Multi-tenant organization identifier';
COMMENT ON COLUMN portal_app.platform_operators.is_active IS 'Active account flag';
COMMENT ON COLUMN portal_app.platform_operators.created_at IS 'Account provisioning timestamp';

-- Domain: app.conversations -> portal_app.inquiry_threads
-- Purpose: A user's chat threads with the assistant.
CREATE TABLE IF NOT EXISTS portal_app.inquiry_threads (
    thread_id VARCHAR(32) NOT NULL,
    operator_id VARCHAR(32) NOT NULL,
    tenant_org_id VARCHAR(32) NOT NULL,
    thread_topic VARCHAR(200) NOT NULL,
    started_at TIMESTAMP NOT NULL,
    last_activity_at TIMESTAMP NOT NULL,
    PRIMARY KEY (thread_id)
);
COMMENT ON TABLE portal_app.inquiry_threads IS 'A user''s chat threads with the assistant.';
COMMENT ON COLUMN portal_app.inquiry_threads.thread_id IS 'Unique conversation thread ID';
COMMENT ON COLUMN portal_app.inquiry_threads.operator_id IS 'Owner user ID';
COMMENT ON COLUMN portal_app.inquiry_threads.tenant_org_id IS 'Tenant organization boundary';
COMMENT ON COLUMN portal_app.inquiry_threads.thread_topic IS 'Summary title of inquiry thread';
COMMENT ON COLUMN portal_app.inquiry_threads.started_at IS 'Thread creation timestamp';
COMMENT ON COLUMN portal_app.inquiry_threads.last_activity_at IS 'Most recent message timestamp';

-- Domain: app.messages -> portal_app.thread_utterances
-- Purpose: Individual messages in a conversation, with citations and token usage.
CREATE TABLE IF NOT EXISTS portal_app.thread_utterances (
    utterance_id VARCHAR(32) NOT NULL,
    thread_id VARCHAR(32) NOT NULL,
    speaker_role VARCHAR(24) NOT NULL,
    message_body TEXT NOT NULL,
    cited_objects_json TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    emitted_at TIMESTAMP NOT NULL,
    PRIMARY KEY (utterance_id)
);
COMMENT ON TABLE portal_app.thread_utterances IS 'Individual messages in a conversation, with citations and token usage.';
COMMENT ON COLUMN portal_app.thread_utterances.utterance_id IS 'Unique message utterance ID';
COMMENT ON COLUMN portal_app.thread_utterances.thread_id IS 'Parent conversation thread';
COMMENT ON COLUMN portal_app.thread_utterances.speaker_role IS 'Role (user, assistant, adjudicator)';
COMMENT ON COLUMN portal_app.thread_utterances.message_body IS 'Message text content';
COMMENT ON COLUMN portal_app.thread_utterances.cited_objects_json IS 'JSON array of cited tables, queries, and evidence IDs';
COMMENT ON COLUMN portal_app.thread_utterances.prompt_tokens IS 'Input prompt token count';
COMMENT ON COLUMN portal_app.thread_utterances.completion_tokens IS 'Output completion token count';
COMMENT ON COLUMN portal_app.thread_utterances.emitted_at IS 'Message timestamp';

-- Domain: ref.customer_groups -> master_ref.client_conglomerates
-- Purpose: Parent groups that tie a customer's ERP and CRM records together.
CREATE TABLE IF NOT EXISTS master_ref.client_conglomerates (
    conglomerate_id VARCHAR(32) NOT NULL,
    conglomerate_name VARCHAR(160) NOT NULL,
    hq_country_iso VARCHAR(8) NOT NULL,
    global_tier VARCHAR(32) NOT NULL,
    primary_industry_code VARCHAR(32) NOT NULL,
    primary_region_code VARCHAR(32) NOT NULL,
    credit_risk_rating VARCHAR(16) NOT NULL,
    PRIMARY KEY (conglomerate_id)
);
COMMENT ON TABLE master_ref.client_conglomerates IS 'Parent groups that tie a customer''s ERP and CRM records together.';
COMMENT ON COLUMN master_ref.client_conglomerates.conglomerate_id IS 'Global canonical customer parent group ID';
COMMENT ON COLUMN master_ref.client_conglomerates.conglomerate_name IS 'Canonical global enterprise name';
COMMENT ON COLUMN master_ref.client_conglomerates.hq_country_iso IS 'Headquarters ISO country code';
COMMENT ON COLUMN master_ref.client_conglomerates.global_tier IS 'Customer tier (Strategic_G100, Enterprise, Mid_Market)';
COMMENT ON COLUMN master_ref.client_conglomerates.primary_industry_code IS 'Standardized industry vertical code';
COMMENT ON COLUMN master_ref.client_conglomerates.primary_region_code IS 'Standardized global region code';
COMMENT ON COLUMN master_ref.client_conglomerates.credit_risk_rating IS 'Internal credit rating (AAA, AA, A, BBB, WATCH)';

-- Domain: ref.company_regions -> master_ref.entity_jurisdictions
-- Purpose: Maps each legal entity (company code) to a region.
CREATE TABLE IF NOT EXISTS master_ref.entity_jurisdictions (
    legal_entity_code VARCHAR(16) NOT NULL,
    legal_entity_name VARCHAR(120) NOT NULL,
    region_code VARCHAR(32) NOT NULL,
    functional_currency VARCHAR(8) NOT NULL,
    tax_regime VARCHAR(40) NOT NULL,
    PRIMARY KEY (legal_entity_code)
);
COMMENT ON TABLE master_ref.entity_jurisdictions IS 'Maps each legal entity (company code) to a region.';
COMMENT ON COLUMN master_ref.entity_jurisdictions.legal_entity_code IS 'ERP legal company code (e.g., AETH-US, AETH-UK)';
COMMENT ON COLUMN master_ref.entity_jurisdictions.legal_entity_name IS 'Registered legal subsidiary name';
COMMENT ON COLUMN master_ref.entity_jurisdictions.region_code IS 'Standard reporting region code (AMER, EMEA, APAC)';
COMMENT ON COLUMN master_ref.entity_jurisdictions.functional_currency IS 'Local statutory currency code';
COMMENT ON COLUMN master_ref.entity_jurisdictions.tax_regime IS 'Tax jurisdiction classification';

-- Domain: ref.reporting_dimension_types -> master_ref.taxonomy_facets
-- Purpose: Kinds of reporting dimension, e.g. region, product line or industry.
CREATE TABLE IF NOT EXISTS master_ref.taxonomy_facets (
    facet_code VARCHAR(32) NOT NULL,
    facet_title VARCHAR(100) NOT NULL,
    governance_owner VARCHAR(80) NOT NULL,
    is_mandatory_reporting BOOLEAN NOT NULL,
    PRIMARY KEY (facet_code)
);
COMMENT ON TABLE master_ref.taxonomy_facets IS 'Kinds of reporting dimension, e.g. region, product line or industry.';
COMMENT ON COLUMN master_ref.taxonomy_facets.facet_code IS 'Dimension type identifier (REGION, PRODUCT_LINE, INDUSTRY, SEGMENT)';
COMMENT ON COLUMN master_ref.taxonomy_facets.facet_title IS 'Human-readable dimension type name';
COMMENT ON COLUMN master_ref.taxonomy_facets.governance_owner IS 'Data governance team responsible';
COMMENT ON COLUMN master_ref.taxonomy_facets.is_mandatory_reporting IS 'Whether required on executive snapshots';

-- Domain: ref.reporting_dimensions -> master_ref.taxonomy_nodes
-- Purpose: The standard values for each dimension type.
CREATE TABLE IF NOT EXISTS master_ref.taxonomy_nodes (
    node_id VARCHAR(32) NOT NULL,
    facet_code VARCHAR(32) NOT NULL,
    canonical_code VARCHAR(40) NOT NULL,
    canonical_label VARCHAR(120) NOT NULL,
    sort_ordinal INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL,
    PRIMARY KEY (node_id)
);
COMMENT ON TABLE master_ref.taxonomy_nodes IS 'The standard values for each dimension type.';
COMMENT ON COLUMN master_ref.taxonomy_nodes.node_id IS 'Unique canonical dimension value ID';
COMMENT ON COLUMN master_ref.taxonomy_nodes.facet_code IS 'Dimension type code';
COMMENT ON COLUMN master_ref.taxonomy_nodes.canonical_code IS 'Standardized reporting code';
COMMENT ON COLUMN master_ref.taxonomy_nodes.canonical_label IS 'Standardized reporting display label';
COMMENT ON COLUMN master_ref.taxonomy_nodes.sort_ordinal IS 'Presentation sort order';
COMMENT ON COLUMN master_ref.taxonomy_nodes.is_active IS 'Whether active for current fiscal year';

-- Domain: ref.reporting_dimension_mappings -> master_ref.taxonomy_crosswalks
-- Purpose: Converts raw values from each source system into a standard dimension value.
CREATE TABLE IF NOT EXISTS master_ref.taxonomy_crosswalks (
    crosswalk_id VARCHAR(32) NOT NULL,
    source_system_code VARCHAR(32) NOT NULL,
    facet_code VARCHAR(32) NOT NULL,
    raw_source_value VARCHAR(100) NOT NULL,
    target_node_id VARCHAR(32) NOT NULL,
    mapping_confidence NUMERIC(5,4) NOT NULL,
    PRIMARY KEY (crosswalk_id)
);
COMMENT ON TABLE master_ref.taxonomy_crosswalks IS 'Converts raw values from each source system into a standard dimension value.';
COMMENT ON COLUMN master_ref.taxonomy_crosswalks.crosswalk_id IS 'Unique mapping rule ID';
COMMENT ON COLUMN master_ref.taxonomy_crosswalks.source_system_code IS 'Originating system (ERP, CRM, CONTRACTS, LEGACY_PLAN)';
COMMENT ON COLUMN master_ref.taxonomy_crosswalks.facet_code IS 'Dimension facet being mapped';
COMMENT ON COLUMN master_ref.taxonomy_crosswalks.raw_source_value IS 'Raw unstandardized value in source system';
COMMENT ON COLUMN master_ref.taxonomy_crosswalks.target_node_id IS 'Mapped standard taxonomy node';
COMMENT ON COLUMN master_ref.taxonomy_crosswalks.mapping_confidence IS 'Confidence weight (1.0000 for verified deterministic rules)';

-- Domain: erp.ar_customers -> ledger_erp.receivable_debtors
-- Purpose: Customers as billed in the ERP, with payment terms, currency and address.
CREATE TABLE IF NOT EXISTS ledger_erp.receivable_debtors (
    debtor_id VARCHAR(32) NOT NULL,
    conglomerate_id VARCHAR(32) NULL,
    legal_entity_code VARCHAR(16) NOT NULL,
    billing_legal_name VARCHAR(160) NOT NULL,
    payment_terms_code VARCHAR(24) NOT NULL,
    billing_currency VARCHAR(8) NOT NULL,
    billing_city VARCHAR(80) NOT NULL,
    billing_country_iso VARCHAR(8) NOT NULL,
    credit_limit_usd NUMERIC(18,2) NOT NULL,
    account_status VARCHAR(24) NOT NULL,
    PRIMARY KEY (debtor_id)
);
COMMENT ON TABLE ledger_erp.receivable_debtors IS 'Customers as billed in the ERP, with payment terms, currency and address.';
COMMENT ON COLUMN ledger_erp.receivable_debtors.debtor_id IS 'ERP billing customer account number';
COMMENT ON COLUMN ledger_erp.receivable_debtors.conglomerate_id IS 'Parent customer group (nullable for unmapped subsidiary accounts)';
COMMENT ON COLUMN ledger_erp.receivable_debtors.legal_entity_code IS 'Billing legal entity company code';
COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_legal_name IS 'Billed customer legal entity name';
COMMENT ON COLUMN ledger_erp.receivable_debtors.payment_terms_code IS 'Payment terms (NET30, NET45, NET60, IMMED)';
COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_currency IS 'Default invoice currency (USD, EUR, GBP)';
COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_city IS 'Billing address city';
COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_country_iso IS 'Billing address ISO country';
COMMENT ON COLUMN ledger_erp.receivable_debtors.credit_limit_usd IS 'Approved AR credit limit in USD';
COMMENT ON COLUMN ledger_erp.receivable_debtors.account_status IS 'ERP account status (ACTIVE, ON_HOLD, INACTIVE)';

-- Domain: erp.ar_invoice_headers -> ledger_erp.sales_invoices
-- Purpose: One row per customer invoice, with totals, tax, open balance and due date.
CREATE TABLE IF NOT EXISTS ledger_erp.sales_invoices (
    invoice_id VARCHAR(32) NOT NULL,
    invoice_number VARCHAR(40) NOT NULL,
    debtor_id VARCHAR(32) NOT NULL,
    legal_entity_code VARCHAR(16) NOT NULL,
    agreement_id VARCHAR(32) NULL,
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    fiscal_period VARCHAR(16) NOT NULL,
    doc_currency VARCHAR(8) NOT NULL,
    fx_rate_to_usd NUMERIC(12,6) NOT NULL,
    subtotal_amount_doc NUMERIC(18,2) NOT NULL,
    tax_amount_doc NUMERIC(18,2) NOT NULL,
    gross_total_doc NUMERIC(18,2) NOT NULL,
    subtotal_amount_usd NUMERIC(18,2) NOT NULL,
    tax_amount_usd NUMERIC(18,2) NOT NULL,
    gross_total_usd NUMERIC(18,2) NOT NULL,
    open_balance_usd NUMERIC(18,2) NOT NULL,
    settlement_status VARCHAR(32) NOT NULL,
    PRIMARY KEY (invoice_id)
);
COMMENT ON TABLE ledger_erp.sales_invoices IS 'One row per customer invoice, with totals, tax, open balance and due date.';
COMMENT ON COLUMN ledger_erp.sales_invoices.invoice_id IS 'Unique ERP invoice header ID';
COMMENT ON COLUMN ledger_erp.sales_invoices.invoice_number IS 'External printed invoice document number';
COMMENT ON COLUMN ledger_erp.sales_invoices.debtor_id IS 'Billed ERP debtor customer';
COMMENT ON COLUMN ledger_erp.sales_invoices.legal_entity_code IS 'Issuing company code';
COMMENT ON COLUMN ledger_erp.sales_invoices.agreement_id IS 'Linked contract document if matched';
COMMENT ON COLUMN ledger_erp.sales_invoices.invoice_date IS 'Invoice posting date';
COMMENT ON COLUMN ledger_erp.sales_invoices.due_date IS 'Payment due date';
COMMENT ON COLUMN ledger_erp.sales_invoices.fiscal_period IS 'Accounting period YYYY-MM';
COMMENT ON COLUMN ledger_erp.sales_invoices.doc_currency IS 'Transaction currency code';
COMMENT ON COLUMN ledger_erp.sales_invoices.fx_rate_to_usd IS 'Spot FX rate to USD at invoice date';
COMMENT ON COLUMN ledger_erp.sales_invoices.subtotal_amount_doc IS 'Pre-tax invoice subtotal in document currency';
COMMENT ON COLUMN ledger_erp.sales_invoices.tax_amount_doc IS 'Tax amount in document currency';
COMMENT ON COLUMN ledger_erp.sales_invoices.gross_total_doc IS 'Total invoice amount including tax in doc currency';
COMMENT ON COLUMN ledger_erp.sales_invoices.subtotal_amount_usd IS 'Pre-tax invoice subtotal in USD';
COMMENT ON COLUMN ledger_erp.sales_invoices.tax_amount_usd IS 'Tax amount in USD';
COMMENT ON COLUMN ledger_erp.sales_invoices.gross_total_usd IS 'Total invoice amount in USD';
COMMENT ON COLUMN ledger_erp.sales_invoices.open_balance_usd IS 'Remaining unpaid/uncredited balance in USD';
COMMENT ON COLUMN ledger_erp.sales_invoices.settlement_status IS 'Status (PAID, PARTIALLY_PAID, OPEN, OVERDUE, CREDITED, VOID)';

-- Domain: erp.ar_invoice_lines -> ledger_erp.sales_invoice_items
-- Purpose: Line items on each invoice, with quantity, price and service period.
CREATE TABLE IF NOT EXISTS ledger_erp.sales_invoice_items (
    invoice_item_id VARCHAR(32) NOT NULL,
    invoice_id VARCHAR(32) NOT NULL,
    line_number INTEGER NOT NULL,
    catalog_item_id VARCHAR(32) NOT NULL,
    dim_bundle_id VARCHAR(32) NOT NULL,
    quantity NUMERIC(12,2) NOT NULL,
    unit_price_doc NUMERIC(18,4) NOT NULL,
    line_net_amount_doc NUMERIC(18,2) NOT NULL,
    line_net_amount_usd NUMERIC(18,2) NOT NULL,
    service_start_date DATE NOT NULL,
    service_end_date DATE NOT NULL,
    PRIMARY KEY (invoice_item_id)
);
COMMENT ON TABLE ledger_erp.sales_invoice_items IS 'Line items on each invoice, with quantity, price and service period.';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.invoice_item_id IS 'Unique invoice line item ID';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.invoice_id IS 'Parent invoice header ID';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.line_number IS 'Sequential line number on invoice';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.catalog_item_id IS 'Sold product or service item';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.dim_bundle_id IS 'Financial dimension set tag';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.quantity IS 'Billed quantity';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.unit_price_doc IS 'Unit price in document currency';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.line_net_amount_doc IS 'Net line amount in document currency';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.line_net_amount_usd IS 'Net line amount in USD';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.service_start_date IS 'Revenue recognition service period start';
COMMENT ON COLUMN ledger_erp.sales_invoice_items.service_end_date IS 'Revenue recognition service period end';

-- Domain: erp.ar_transactions -> ledger_erp.receivable_ledger_events
-- Purpose: Customer ledger entries (invoices, payments, credits) and how they were settled.
CREATE TABLE IF NOT EXISTS ledger_erp.receivable_ledger_events (
    ar_event_id VARCHAR(32) NOT NULL,
    debtor_id VARCHAR(32) NOT NULL,
    applied_invoice_id VARCHAR(32) NULL,
    event_type VARCHAR(32) NOT NULL,
    event_date DATE NOT NULL,
    fiscal_period VARCHAR(16) NOT NULL,
    signed_amount_doc NUMERIC(18,2) NOT NULL,
    signed_amount_usd NUMERIC(18,2) NOT NULL,
    settlement_ref_code VARCHAR(64) NULL,
    gl_voucher_id VARCHAR(32) NULL,
    is_settled BOOLEAN NOT NULL,
    PRIMARY KEY (ar_event_id)
);
COMMENT ON TABLE ledger_erp.receivable_ledger_events IS 'Customer ledger entries (invoices, payments, credits) and how they were settled.';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.ar_event_id IS 'Unique AR subledger entry ID';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.debtor_id IS 'Customer debtor account';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.applied_invoice_id IS 'Invoice this entry originates from or settles against';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.event_type IS 'Entry type (INVOICE_POST, CASH_RECEIPT, CREDIT_MEMO, FX_ADJUSTMENT, WRITE_OFF)';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.event_date IS 'Subledger posting date';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.fiscal_period IS 'Accounting period YYYY-MM';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.signed_amount_doc IS 'Signed impact on AR in document currency (+ invoice, - payment/credit)';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.signed_amount_usd IS 'Signed impact on AR in USD (+ invoice, - payment/credit)';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.settlement_ref_code IS 'Bank wire, check, or credit memo reference code';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.gl_voucher_id IS 'Posted General Ledger voucher ID';
COMMENT ON COLUMN ledger_erp.receivable_ledger_events.is_settled IS 'Whether fully cleared/reconciled';

-- Domain: erp.products -> ledger_erp.catalog_offerings
-- Purpose: Items and services that can be sold, with their revenue type and default account.
CREATE TABLE IF NOT EXISTS ledger_erp.catalog_offerings (
    catalog_item_id VARCHAR(32) NOT NULL,
    sku_code VARCHAR(40) NOT NULL,
    offering_name VARCHAR(140) NOT NULL,
    product_line_raw VARCHAR(60) NOT NULL,
    revenue_stream_type VARCHAR(40) NOT NULL,
    default_rev_account_id VARCHAR(32) NOT NULL,
    list_unit_price_usd NUMERIC(18,2) NOT NULL,
    PRIMARY KEY (catalog_item_id)
);
COMMENT ON TABLE ledger_erp.catalog_offerings IS 'Items and services that can be sold, with their revenue type and default account.';
COMMENT ON COLUMN ledger_erp.catalog_offerings.catalog_item_id IS 'Unique SKU or product ID';
COMMENT ON COLUMN ledger_erp.catalog_offerings.sku_code IS 'Product SKU code';
COMMENT ON COLUMN ledger_erp.catalog_offerings.offering_name IS 'Product or service name';
COMMENT ON COLUMN ledger_erp.catalog_offerings.product_line_raw IS 'ERP product line classification';
COMMENT ON COLUMN ledger_erp.catalog_offerings.revenue_stream_type IS 'Revenue stream (SaaS_Subscription, Managed_Cloud, Advisory_Services, Usage_Overage)';
COMMENT ON COLUMN ledger_erp.catalog_offerings.default_rev_account_id IS 'Default GL revenue account';
COMMENT ON COLUMN ledger_erp.catalog_offerings.list_unit_price_usd IS 'Standard list price in USD';

-- Domain: erp.dimension_attributes -> ledger_erp.analytic_axes
-- Purpose: The financial dimensions defined in the ERP, e.g. department or cost center.
CREATE TABLE IF NOT EXISTS ledger_erp.analytic_axes (
    axis_id VARCHAR(32) NOT NULL,
    axis_code VARCHAR(32) NOT NULL,
    axis_name VARCHAR(100) NOT NULL,
    is_required_on_gl BOOLEAN NOT NULL,
    PRIMARY KEY (axis_id)
);
COMMENT ON TABLE ledger_erp.analytic_axes IS 'The financial dimensions defined in the ERP, e.g. department or cost center.';
COMMENT ON COLUMN ledger_erp.analytic_axes.axis_id IS 'Unique ERP financial dimension attribute ID';
COMMENT ON COLUMN ledger_erp.analytic_axes.axis_code IS 'Dimension code (DEPT, COST_CTR, PROD_LINE, GEO_UNIT)';
COMMENT ON COLUMN ledger_erp.analytic_axes.axis_name IS 'Descriptive dimension name';
COMMENT ON COLUMN ledger_erp.analytic_axes.is_required_on_gl IS 'Mandatory for GL postings';

-- Domain: erp.dimension_values -> ledger_erp.analytic_axis_members
-- Purpose: The allowed values for each financial dimension.
CREATE TABLE IF NOT EXISTS ledger_erp.analytic_axis_members (
    member_id VARCHAR(32) NOT NULL,
    axis_id VARCHAR(32) NOT NULL,
    member_code VARCHAR(40) NOT NULL,
    member_label VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL,
    PRIMARY KEY (member_id)
);
COMMENT ON TABLE ledger_erp.analytic_axis_members IS 'The allowed values for each financial dimension.';
COMMENT ON COLUMN ledger_erp.analytic_axis_members.member_id IS 'Unique ERP dimension member ID';
COMMENT ON COLUMN ledger_erp.analytic_axis_members.axis_id IS 'Parent dimension attribute';
COMMENT ON COLUMN ledger_erp.analytic_axis_members.member_code IS 'ERP dimension value code';
COMMENT ON COLUMN ledger_erp.analytic_axis_members.member_label IS 'ERP dimension value description';
COMMENT ON COLUMN ledger_erp.analytic_axis_members.is_active IS 'Active flag';

-- Domain: erp.dimension_sets -> ledger_erp.tag_bundles
-- Purpose: A saved combination of dimension values that a transaction is tagged with.
CREATE TABLE IF NOT EXISTS ledger_erp.tag_bundles (
    dim_bundle_id VARCHAR(32) NOT NULL,
    bundle_signature VARCHAR(160) NOT NULL,
    dept_code VARCHAR(40) NOT NULL,
    cost_center_code VARCHAR(40) NOT NULL,
    product_line_code VARCHAR(40) NOT NULL,
    geo_unit_code VARCHAR(40) NOT NULL,
    PRIMARY KEY (dim_bundle_id)
);
COMMENT ON TABLE ledger_erp.tag_bundles IS 'A saved combination of dimension values that a transaction is tagged with.';
COMMENT ON COLUMN ledger_erp.tag_bundles.dim_bundle_id IS 'Unique dimension combination set ID';
COMMENT ON COLUMN ledger_erp.tag_bundles.bundle_signature IS 'Composite key string of dimension codes';
COMMENT ON COLUMN ledger_erp.tag_bundles.dept_code IS 'Department code in bundle';
COMMENT ON COLUMN ledger_erp.tag_bundles.cost_center_code IS 'Cost center code in bundle';
COMMENT ON COLUMN ledger_erp.tag_bundles.product_line_code IS 'Product line code in bundle';
COMMENT ON COLUMN ledger_erp.tag_bundles.geo_unit_code IS 'Geographic unit code in bundle';

-- Domain: erp.dimension_set_values -> ledger_erp.tag_bundle_members
-- Purpose: Breaks each dimension set into its individual values.
CREATE TABLE IF NOT EXISTS ledger_erp.tag_bundle_members (
    bundle_member_id VARCHAR(32) NOT NULL,
    dim_bundle_id VARCHAR(32) NOT NULL,
    axis_id VARCHAR(32) NOT NULL,
    member_id VARCHAR(32) NOT NULL,
    PRIMARY KEY (bundle_member_id)
);
COMMENT ON TABLE ledger_erp.tag_bundle_members IS 'Breaks each dimension set into its individual values.';
COMMENT ON COLUMN ledger_erp.tag_bundle_members.bundle_member_id IS 'Unique bridge row ID';
COMMENT ON COLUMN ledger_erp.tag_bundle_members.dim_bundle_id IS 'Parent dimension set ID';
COMMENT ON COLUMN ledger_erp.tag_bundle_members.axis_id IS 'Dimension attribute ID';
COMMENT ON COLUMN ledger_erp.tag_bundle_members.member_id IS 'Allowed dimension member ID';

-- Domain: erp.gl_account_categories -> ledger_erp.ledger_statement_groups
-- Purpose: Groups of general ledger accounts used for financial statements.
CREATE TABLE IF NOT EXISTS ledger_erp.ledger_statement_groups (
    stmt_group_id VARCHAR(32) NOT NULL,
    group_code VARCHAR(32) NOT NULL,
    group_title VARCHAR(120) NOT NULL,
    financial_statement VARCHAR(32) NOT NULL,
    rollup_section VARCHAR(40) NOT NULL,
    PRIMARY KEY (stmt_group_id)
);
COMMENT ON TABLE ledger_erp.ledger_statement_groups IS 'Groups of general ledger accounts used for financial statements.';
COMMENT ON COLUMN ledger_erp.ledger_statement_groups.stmt_group_id IS 'Unique statement category ID';
COMMENT ON COLUMN ledger_erp.ledger_statement_groups.group_code IS 'Category code (REV_REC, REV_SERV, COGS, OPEX_RD, OPEX_SM, OPEX_GA, ASSET_AR, ASSET_CASH, LIAB_DEF)';
COMMENT ON COLUMN ledger_erp.ledger_statement_groups.group_title IS 'Statement line title';
COMMENT ON COLUMN ledger_erp.ledger_statement_groups.financial_statement IS 'Statement type (INCOME_STATEMENT, BALANCE_SHEET)';
COMMENT ON COLUMN ledger_erp.ledger_statement_groups.rollup_section IS 'Section (REVENUE, COST_OF_SALES, OPERATING_EXPENSE, ASSETS, LIABILITIES)';

-- Domain: erp.gl_accounts -> ledger_erp.chart_of_accounts
-- Purpose: The chart of accounts: account number, type and normal balance.
CREATE TABLE IF NOT EXISTS ledger_erp.chart_of_accounts (
    account_id VARCHAR(32) NOT NULL,
    account_number VARCHAR(24) NOT NULL,
    account_title VARCHAR(120) NOT NULL,
    stmt_group_id VARCHAR(32) NOT NULL,
    account_class VARCHAR(24) NOT NULL,
    normal_balance_side VARCHAR(8) NOT NULL,
    is_posting_allowed BOOLEAN NOT NULL,
    PRIMARY KEY (account_id)
);
COMMENT ON TABLE ledger_erp.chart_of_accounts IS 'The chart of accounts: account number, type and normal balance.';
COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_id IS 'Unique GL account ID';
COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_number IS 'GL account number (e.g., 4010, 1100, 6210)';
COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_title IS 'Account name';
COMMENT ON COLUMN ledger_erp.chart_of_accounts.stmt_group_id IS 'Financial statement category';
COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_class IS 'Class (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)';
COMMENT ON COLUMN ledger_erp.chart_of_accounts.normal_balance_side IS 'Normal balance (DEBIT or CREDIT)';
COMMENT ON COLUMN ledger_erp.chart_of_accounts.is_posting_allowed IS 'Whether direct journal posting is permitted';

-- Domain: erp.gl_journal_headers -> ledger_erp.journal_vouchers
-- Purpose: One row per journal entry, with its fiscal period, dates and voucher.
CREATE TABLE IF NOT EXISTS ledger_erp.journal_vouchers (
    voucher_id VARCHAR(32) NOT NULL,
    voucher_number VARCHAR(40) NOT NULL,
    legal_entity_code VARCHAR(16) NOT NULL,
    journal_source VARCHAR(32) NOT NULL,
    posting_date DATE NOT NULL,
    fiscal_period VARCHAR(16) NOT NULL,
    fiscal_year INTEGER NOT NULL,
    posting_status VARCHAR(24) NOT NULL,
    header_memo VARCHAR(200) NOT NULL,
    PRIMARY KEY (voucher_id)
);
COMMENT ON TABLE ledger_erp.journal_vouchers IS 'One row per journal entry, with its fiscal period, dates and voucher.';
COMMENT ON COLUMN ledger_erp.journal_vouchers.voucher_id IS 'Unique GL journal header ID';
COMMENT ON COLUMN ledger_erp.journal_vouchers.voucher_number IS 'Printed journal voucher code';
COMMENT ON COLUMN ledger_erp.journal_vouchers.legal_entity_code IS 'Company code';
COMMENT ON COLUMN ledger_erp.journal_vouchers.journal_source IS 'Origin (AR_SUBLEDGER, REV_SCHED, PAYROLL, AP_EXPENSE, MANUAL_ADJ)';
COMMENT ON COLUMN ledger_erp.journal_vouchers.posting_date IS 'Effective GL posting date';
COMMENT ON COLUMN ledger_erp.journal_vouchers.fiscal_period IS 'Fiscal period YYYY-MM';
COMMENT ON COLUMN ledger_erp.journal_vouchers.fiscal_year IS 'Fiscal year';
COMMENT ON COLUMN ledger_erp.journal_vouchers.posting_status IS 'Status (POSTED, PENDING_APPROVAL, REVERSED)';
COMMENT ON COLUMN ledger_erp.journal_vouchers.header_memo IS 'Journal entry description';

-- Domain: erp.gl_journal_lines -> ledger_erp.journal_postings
-- Purpose: The debit and credit lines of each journal entry.
CREATE TABLE IF NOT EXISTS ledger_erp.journal_postings (
    posting_line_id VARCHAR(32) NOT NULL,
    voucher_id VARCHAR(32) NOT NULL,
    line_seq INTEGER NOT NULL,
    account_id VARCHAR(32) NOT NULL,
    dim_bundle_id VARCHAR(32) NOT NULL,
    debit_amount_usd NUMERIC(18,2) NOT NULL,
    credit_amount_usd NUMERIC(18,2) NOT NULL,
    net_signed_usd NUMERIC(18,2) NOT NULL,
    line_narrative VARCHAR(200) NOT NULL,
    PRIMARY KEY (posting_line_id)
);
COMMENT ON TABLE ledger_erp.journal_postings IS 'The debit and credit lines of each journal entry.';
COMMENT ON COLUMN ledger_erp.journal_postings.posting_line_id IS 'Unique GL journal line ID';
COMMENT ON COLUMN ledger_erp.journal_postings.voucher_id IS 'Parent journal header';
COMMENT ON COLUMN ledger_erp.journal_postings.line_seq IS 'Line sequence number';
COMMENT ON COLUMN ledger_erp.journal_postings.account_id IS 'GL account posted';
COMMENT ON COLUMN ledger_erp.journal_postings.dim_bundle_id IS 'Financial dimension bundle';
COMMENT ON COLUMN ledger_erp.journal_postings.debit_amount_usd IS 'Debit amount in USD';
COMMENT ON COLUMN ledger_erp.journal_postings.credit_amount_usd IS 'Credit amount in USD';
COMMENT ON COLUMN ledger_erp.journal_postings.net_signed_usd IS 'Debit minus Credit in USD';
COMMENT ON COLUMN ledger_erp.journal_postings.line_narrative IS 'Line item memo';

-- Domain: erp.legacy_account_map -> ledger_erp.legacy_coa_crosswalk
-- Purpose: Maps old account numbers to the current chart of accounts.
CREATE TABLE IF NOT EXISTS ledger_erp.legacy_coa_crosswalk (
    legacy_map_id VARCHAR(32) NOT NULL,
    legacy_system_name VARCHAR(40) NOT NULL,
    legacy_acct_code VARCHAR(32) NOT NULL,
    legacy_acct_desc VARCHAR(120) NOT NULL,
    modern_account_id VARCHAR(32) NOT NULL,
    effective_cutover_date DATE NOT NULL,
    PRIMARY KEY (legacy_map_id)
);
COMMENT ON TABLE ledger_erp.legacy_coa_crosswalk IS 'Maps old account numbers to the current chart of accounts.';
COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_map_id IS 'Unique legacy mapping ID';
COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_system_name IS 'Prior ERP name (LegacyLedger_EU, LegacyBooks_US)';
COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_acct_code IS 'Historical account number';
COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_acct_desc IS 'Historical account label';
COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.modern_account_id IS 'Mapped current GL account ID';
COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.effective_cutover_date IS 'Date migration cutover took effect';

-- Domain: erp.expense_categories -> ledger_erp.cost_taxonomy_nodes
-- Purpose: The expense hierarchy (function down to expense line), each row pointing to its parent.
CREATE TABLE IF NOT EXISTS ledger_erp.cost_taxonomy_nodes (
    cost_node_id VARCHAR(32) NOT NULL,
    parent_cost_node_id VARCHAR(32) NULL,
    hierarchy_level INTEGER NOT NULL,
    cost_category_code VARCHAR(40) NOT NULL,
    cost_category_title VARCHAR(120) NOT NULL,
    PRIMARY KEY (cost_node_id)
);
COMMENT ON TABLE ledger_erp.cost_taxonomy_nodes IS 'The expense hierarchy (function down to expense line), each row pointing to its parent.';
COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.cost_node_id IS 'Unique expense category node ID';
COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.parent_cost_node_id IS 'Self-referencing parent expense category ID';
COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.hierarchy_level IS 'Depth in hierarchy (1=Function, 2=Department_Group, 3=Expense_Line)';
COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.cost_category_code IS 'Code for cost category';
COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.cost_category_title IS 'Display name of expense category';

-- Domain: erp.expense_account_map -> ledger_erp.cost_account_links
-- Purpose: Assigns GL accounts to expense categories.
CREATE TABLE IF NOT EXISTS ledger_erp.cost_account_links (
    link_id VARCHAR(32) NOT NULL,
    cost_node_id VARCHAR(32) NOT NULL,
    account_id VARCHAR(32) NOT NULL,
    allocation_weight NUMERIC(5,4) NOT NULL,
    PRIMARY KEY (link_id)
);
COMMENT ON TABLE ledger_erp.cost_account_links IS 'Assigns GL accounts to expense categories.';
COMMENT ON COLUMN ledger_erp.cost_account_links.link_id IS 'Unique mapping link ID';
COMMENT ON COLUMN ledger_erp.cost_account_links.cost_node_id IS 'Leaf expense category node';
COMMENT ON COLUMN ledger_erp.cost_account_links.account_id IS 'GL expense account';
COMMENT ON COLUMN ledger_erp.cost_account_links.allocation_weight IS 'Allocation percentage (typically 1.0000)';

-- Domain: crm.accounts -> pipeline_crm.prospect_accounts
-- Purpose: Customer accounts in the CRM, with region, industry and segment.
CREATE TABLE IF NOT EXISTS pipeline_crm.prospect_accounts (
    crm_account_id VARCHAR(32) NOT NULL,
    conglomerate_id VARCHAR(32) NULL,
    crm_account_name VARCHAR(160) NOT NULL,
    crm_region_raw VARCHAR(40) NOT NULL,
    crm_industry_raw VARCHAR(60) NOT NULL,
    market_segment VARCHAR(32) NOT NULL,
    account_owner_rep_id VARCHAR(32) NOT NULL,
    is_existing_customer BOOLEAN NOT NULL,
    PRIMARY KEY (crm_account_id)
);
COMMENT ON TABLE pipeline_crm.prospect_accounts IS 'Customer accounts in the CRM, with region, industry and segment.';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_account_id IS 'Unique CRM account ID';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.conglomerate_id IS 'Parent customer group (nullable for unlinked prospects)';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_account_name IS 'Account name in CRM';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_region_raw IS 'CRM raw territory/region string';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_industry_raw IS 'CRM raw industry classification';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.market_segment IS 'Segment (Enterprise, Commercial, Strategic)';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.account_owner_rep_id IS 'Assigned account executive';
COMMENT ON COLUMN pipeline_crm.prospect_accounts.is_existing_customer IS 'Whether account already has active contracts';

-- Domain: crm.opportunities -> pipeline_crm.deal_opportunities
-- Purpose: Current deals, with stage, probability, contract value and close date.
CREATE TABLE IF NOT EXISTS pipeline_crm.deal_opportunities (
    deal_id VARCHAR(32) NOT NULL,
    crm_account_id VARCHAR(32) NOT NULL,
    owner_rep_id VARCHAR(32) NOT NULL,
    deal_title VARCHAR(180) NOT NULL,
    deal_type VARCHAR(32) NOT NULL,
    pipeline_stage VARCHAR(40) NOT NULL,
    forecast_cat_code VARCHAR(32) NOT NULL,
    win_probability_pct NUMERIC(5,2) NOT NULL,
    deal_tcv_usd NUMERIC(18,2) NOT NULL,
    deal_arr_usd NUMERIC(18,2) NOT NULL,
    weighted_arr_usd NUMERIC(18,2) NOT NULL,
    target_close_date DATE NOT NULL,
    close_fiscal_period VARCHAR(16) NOT NULL,
    linked_agreement_id VARCHAR(32) NULL,
    PRIMARY KEY (deal_id)
);
COMMENT ON TABLE pipeline_crm.deal_opportunities IS 'Current deals, with stage, probability, contract value and close date.';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_id IS 'Unique CRM opportunity ID';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.crm_account_id IS 'Customer CRM account';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.owner_rep_id IS 'Sales rep owning the deal';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_title IS 'Opportunity name';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_type IS 'Deal motion (New_Logo, Expansion, Renewal)';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.pipeline_stage IS 'Current stage (Closed_Won, Commit, Best_Case, Negotiation, Qualification, Closed_Lost)';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.forecast_cat_code IS 'Forecast category code';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.win_probability_pct IS 'Win probability percentage (0 to 100)';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_tcv_usd IS 'Total contract value in USD';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_arr_usd IS 'Annual recurring revenue value in USD';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.weighted_arr_usd IS 'Probability-weighted ARR in USD';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.target_close_date IS 'Expected or actual close date';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.close_fiscal_period IS 'Close period YYYY-MM';
COMMENT ON COLUMN pipeline_crm.deal_opportunities.linked_agreement_id IS 'Executed contract file if Closed Won';

-- Domain: crm.opportunity_snapshots -> pipeline_crm.deal_history_snapshots
-- Purpose: Dated copies of each deal, used to track how the pipeline changes over time.
CREATE TABLE IF NOT EXISTS pipeline_crm.deal_history_snapshots (
    snapshot_row_id VARCHAR(32) NOT NULL,
    deal_id VARCHAR(32) NOT NULL,
    as_of_month VARCHAR(16) NOT NULL,
    snapshot_date DATE NOT NULL,
    stage_at_snapshot VARCHAR(40) NOT NULL,
    forecast_cat_at_snapshot VARCHAR(32) NOT NULL,
    probability_at_snapshot NUMERIC(5,2) NOT NULL,
    arr_at_snapshot_usd NUMERIC(18,2) NOT NULL,
    weighted_arr_at_snapshot_usd NUMERIC(18,2) NOT NULL,
    PRIMARY KEY (snapshot_row_id)
);
COMMENT ON TABLE pipeline_crm.deal_history_snapshots IS 'Dated copies of each deal, used to track how the pipeline changes over time.';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.snapshot_row_id IS 'Unique snapshot row ID';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.deal_id IS 'Opportunity ID';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.as_of_month IS 'Snapshot month YYYY-MM';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.snapshot_date IS 'Exact snapshot capture date';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.stage_at_snapshot IS 'Stage as of snapshot date';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.forecast_cat_at_snapshot IS 'Forecast category at snapshot date';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.probability_at_snapshot IS 'Probability % at snapshot date';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.arr_at_snapshot_usd IS 'Unweighted ARR in USD at snapshot date';
COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.weighted_arr_at_snapshot_usd IS 'Weighted ARR in USD at snapshot date';

-- Domain: crm.opportunity_line_items -> pipeline_crm.deal_product_splits
-- Purpose: How each deal's value splits across product categories.
CREATE TABLE IF NOT EXISTS pipeline_crm.deal_product_splits (
    deal_split_id VARCHAR(32) NOT NULL,
    deal_id VARCHAR(32) NOT NULL,
    catalog_item_id VARCHAR(32) NOT NULL,
    crm_product_family VARCHAR(60) NOT NULL,
    split_arr_usd NUMERIC(18,2) NOT NULL,
    split_tcv_usd NUMERIC(18,2) NOT NULL,
    term_months INTEGER NOT NULL,
    PRIMARY KEY (deal_split_id)
);
COMMENT ON TABLE pipeline_crm.deal_product_splits IS 'How each deal''s value splits across product categories.';
COMMENT ON COLUMN pipeline_crm.deal_product_splits.deal_split_id IS 'Unique opportunity line item ID';
COMMENT ON COLUMN pipeline_crm.deal_product_splits.deal_id IS 'Parent opportunity ID';
COMMENT ON COLUMN pipeline_crm.deal_product_splits.catalog_item_id IS 'Product catalog offering';
COMMENT ON COLUMN pipeline_crm.deal_product_splits.crm_product_family IS 'CRM product category label';
COMMENT ON COLUMN pipeline_crm.deal_product_splits.split_arr_usd IS 'Annual recurring value allocated to product line';
COMMENT ON COLUMN pipeline_crm.deal_product_splits.split_tcv_usd IS 'Total contract value allocated to product line';
COMMENT ON COLUMN pipeline_crm.deal_product_splits.term_months IS 'Subscription term in months';

-- Domain: crm.sales_teams -> pipeline_crm.territory_squads
-- Purpose: Sales teams, nested under parent teams.
CREATE TABLE IF NOT EXISTS pipeline_crm.territory_squads (
    squad_id VARCHAR(32) NOT NULL,
    parent_squad_id VARCHAR(32) NULL,
    squad_name VARCHAR(120) NOT NULL,
    theater_region VARCHAR(32) NOT NULL,
    hierarchy_depth INTEGER NOT NULL,
    PRIMARY KEY (squad_id)
);
COMMENT ON TABLE pipeline_crm.territory_squads IS 'Sales teams, nested under parent teams.';
COMMENT ON COLUMN pipeline_crm.territory_squads.squad_id IS 'Unique sales team ID';
COMMENT ON COLUMN pipeline_crm.territory_squads.parent_squad_id IS 'Parent sales team ID in hierarchy';
COMMENT ON COLUMN pipeline_crm.territory_squads.squad_name IS 'Sales team name';
COMMENT ON COLUMN pipeline_crm.territory_squads.theater_region IS 'Assigned geographic theater';
COMMENT ON COLUMN pipeline_crm.territory_squads.hierarchy_depth IS 'Team level (1=Global, 2=Theater, 3=Pod)';

-- Domain: crm.sales_reps -> pipeline_crm.quota_carriers
-- Purpose: Salespeople, with their team, manager and quota.
CREATE TABLE IF NOT EXISTS pipeline_crm.quota_carriers (
    rep_id VARCHAR(32) NOT NULL,
    rep_full_name VARCHAR(120) NOT NULL,
    squad_id VARCHAR(32) NOT NULL,
    manager_rep_id VARCHAR(32) NULL,
    role_title VARCHAR(60) NOT NULL,
    annual_quota_usd NUMERIC(18,2) NOT NULL,
    active_status BOOLEAN NOT NULL,
    PRIMARY KEY (rep_id)
);
COMMENT ON TABLE pipeline_crm.quota_carriers IS 'Salespeople, with their team, manager and quota.';
COMMENT ON COLUMN pipeline_crm.quota_carriers.rep_id IS 'Unique sales representative ID';
COMMENT ON COLUMN pipeline_crm.quota_carriers.rep_full_name IS 'Salesperson full name';
COMMENT ON COLUMN pipeline_crm.quota_carriers.squad_id IS 'Assigned sales team squad';
COMMENT ON COLUMN pipeline_crm.quota_carriers.manager_rep_id IS 'Reporting manager ID';
COMMENT ON COLUMN pipeline_crm.quota_carriers.role_title IS 'Role (VP_Sales, Regional_Director, Enterprise_AE)';
COMMENT ON COLUMN pipeline_crm.quota_carriers.annual_quota_usd IS 'Annual ARR quota target in USD';
COMMENT ON COLUMN pipeline_crm.quota_carriers.active_status IS 'Currently employed/active flag';

-- Domain: crm.forecast_categories -> pipeline_crm.forecast_buckets
-- Purpose: Forecast categories and the default win probability for each.
CREATE TABLE IF NOT EXISTS pipeline_crm.forecast_buckets (
    forecast_cat_code VARCHAR(32) NOT NULL,
    bucket_label VARCHAR(80) NOT NULL,
    default_probability_pct NUMERIC(5,2) NOT NULL,
    included_in_weighted_pipe BOOLEAN NOT NULL,
    PRIMARY KEY (forecast_cat_code)
);
COMMENT ON TABLE pipeline_crm.forecast_buckets IS 'Forecast categories and the default win probability for each.';
COMMENT ON COLUMN pipeline_crm.forecast_buckets.forecast_cat_code IS 'Forecast category code (CLOSED, COMMIT, BEST_CASE, PIPELINE, OMIT)';
COMMENT ON COLUMN pipeline_crm.forecast_buckets.bucket_label IS 'Display label';
COMMENT ON COLUMN pipeline_crm.forecast_buckets.default_probability_pct IS 'Default win weight percentage';
COMMENT ON COLUMN pipeline_crm.forecast_buckets.included_in_weighted_pipe IS 'Whether included in standard weighted pipeline';

-- Domain: plan.budget_lines -> fpna_plan.financial_targets
-- Purpose: Budget or forecast amounts by month and reporting dimension.
CREATE TABLE IF NOT EXISTS fpna_plan.financial_targets (
    target_line_id VARCHAR(32) NOT NULL,
    plan_scenario VARCHAR(32) NOT NULL,
    fiscal_period VARCHAR(16) NOT NULL,
    fiscal_year INTEGER NOT NULL,
    metric_category VARCHAR(40) NOT NULL,
    region_node_id VARCHAR(32) NOT NULL,
    product_line_node_id VARCHAR(32) NOT NULL,
    industry_node_id VARCHAR(32) NOT NULL,
    cost_node_id VARCHAR(32) NULL,
    target_amount_usd NUMERIC(18,2) NOT NULL,
    PRIMARY KEY (target_line_id)
);
COMMENT ON TABLE fpna_plan.financial_targets IS 'Budget or forecast amounts by month and reporting dimension.';
COMMENT ON COLUMN fpna_plan.financial_targets.target_line_id IS 'Unique budget/forecast line ID';
COMMENT ON COLUMN fpna_plan.financial_targets.plan_scenario IS 'Scenario (FY2024_APPROVED_BUDGET, FY2025_APPROVED_BUDGET, FY2025_Q2_REFORECAST, FY2026_BASELINE_PLAN)';
COMMENT ON COLUMN fpna_plan.financial_targets.fiscal_period IS 'Month period YYYY-MM';
COMMENT ON COLUMN fpna_plan.financial_targets.fiscal_year IS 'Fiscal year';
COMMENT ON COLUMN fpna_plan.financial_targets.metric_category IS 'Metric (REVENUE_TARGET, OPEX_BUDGET, NEW_ARR_QUOTA)';
COMMENT ON COLUMN fpna_plan.financial_targets.region_node_id IS 'Standard region dimension node';
COMMENT ON COLUMN fpna_plan.financial_targets.product_line_node_id IS 'Standard product line dimension node';
COMMENT ON COLUMN fpna_plan.financial_targets.industry_node_id IS 'Standard industry dimension node';
COMMENT ON COLUMN fpna_plan.financial_targets.cost_node_id IS 'Optional expense category node for OPEX lines';
COMMENT ON COLUMN fpna_plan.financial_targets.target_amount_usd IS 'Planned budget or forecast amount in USD';

-- Domain: reporting.revenue_position_snapshots -> exec_reporting.monthly_revenue_posture
-- Purpose: Monthly summary of contracted revenue versus pipeline, by product line, industry and region.
CREATE TABLE IF NOT EXISTS exec_reporting.monthly_revenue_posture (
    posture_id VARCHAR(32) NOT NULL,
    snapshot_period VARCHAR(16) NOT NULL,
    region_node_id VARCHAR(32) NOT NULL,
    product_line_node_id VARCHAR(32) NOT NULL,
    industry_node_id VARCHAR(32) NOT NULL,
    recognized_gl_revenue_usd NUMERIC(18,2) NOT NULL,
    contracted_arr_usd NUMERIC(18,2) NOT NULL,
    open_pipeline_unweighted_usd NUMERIC(18,2) NOT NULL,
    open_pipeline_weighted_usd NUMERIC(18,2) NOT NULL,
    budget_target_usd NUMERIC(18,2) NOT NULL,
    variance_to_budget_usd NUMERIC(18,2) NOT NULL,
    PRIMARY KEY (posture_id)
);
COMMENT ON TABLE exec_reporting.monthly_revenue_posture IS 'Monthly summary of contracted revenue versus pipeline, by product line, industry and region.';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.posture_id IS 'Unique snapshot row ID';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.snapshot_period IS 'Reporting month YYYY-MM';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.region_node_id IS 'Canonical region dimension ID';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.product_line_node_id IS 'Canonical product line dimension ID';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.industry_node_id IS 'Canonical industry dimension ID';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.recognized_gl_revenue_usd IS 'Actual recognized GL revenue for period in USD';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.contracted_arr_usd IS 'Active signed contracted ARR in USD';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.open_pipeline_unweighted_usd IS 'Open unweighted CRM pipeline in USD';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.open_pipeline_weighted_usd IS 'Open probability-weighted CRM pipeline in USD';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.budget_target_usd IS 'Approved budget revenue target in USD';
COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.variance_to_budget_usd IS 'Recognized revenue minus budget target in USD';

-- Domain: reporting.forward_year_snapshots -> exec_reporting.horizon_revenue_outlook
-- Purpose: Monthly view of next year's revenue: contracted, and contracted plus pipeline.
CREATE TABLE IF NOT EXISTS exec_reporting.horizon_revenue_outlook (
    outlook_id VARCHAR(32) NOT NULL,
    as_of_period VARCHAR(16) NOT NULL,
    forward_fiscal_year INTEGER NOT NULL,
    region_node_id VARCHAR(32) NOT NULL,
    product_line_node_id VARCHAR(32) NOT NULL,
    locked_contracted_rev_usd NUMERIC(18,2) NOT NULL,
    weighted_pipeline_addition_usd NUMERIC(18,2) NOT NULL,
    combined_outlook_usd NUMERIC(18,2) NOT NULL,
    forward_baseline_plan_usd NUMERIC(18,2) NOT NULL,
    PRIMARY KEY (outlook_id)
);
COMMENT ON TABLE exec_reporting.horizon_revenue_outlook IS 'Monthly view of next year''s revenue: contracted, and contracted plus pipeline.';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.outlook_id IS 'Unique forward outlook row ID';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.as_of_period IS 'Snapshot observation month YYYY-MM';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.forward_fiscal_year IS 'Projected future fiscal year (e.g., 2026)';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.region_node_id IS 'Canonical region dimension ID';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.product_line_node_id IS 'Canonical product line dimension ID';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.locked_contracted_rev_usd IS 'Next-year revenue already locked under signed contracts';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.weighted_pipeline_addition_usd IS 'Next-year expected contribution from weighted pipeline';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.combined_outlook_usd IS 'Contracted plus weighted pipeline outlook in USD';
COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.forward_baseline_plan_usd IS 'Next-year baseline target in USD';

-- Domain: ops.sync_runs -> data_ops.ingestion_batches
-- Purpose: A log of each data load from a source system, with status, row counts and errors.
CREATE TABLE IF NOT EXISTS data_ops.ingestion_batches (
    batch_id VARCHAR(32) NOT NULL,
    upstream_connector VARCHAR(40) NOT NULL,
    target_schema_name VARCHAR(40) NOT NULL,
    run_status VARCHAR(24) NOT NULL,
    records_extracted INTEGER NOT NULL,
    records_loaded INTEGER NOT NULL,
    rejected_rows_count INTEGER NOT NULL,
    error_diagnostic TEXT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (batch_id)
);
COMMENT ON TABLE data_ops.ingestion_batches IS 'A log of each data load from a source system, with status, row counts and errors.';
COMMENT ON COLUMN data_ops.ingestion_batches.batch_id IS 'Unique sync batch execution ID';
COMMENT ON COLUMN data_ops.ingestion_batches.upstream_connector IS 'Source connector (ERP_LEDGERCORE, CRM_PIPELINEHUB, DOC_VAULTSCAN, FPNA_PLANGRID)';
COMMENT ON COLUMN data_ops.ingestion_batches.target_schema_name IS 'Destination schema updated';
COMMENT ON COLUMN data_ops.ingestion_batches.run_status IS 'Execution status (SUCCEEDED, PARTIAL_WARNING, FAILED)';
COMMENT ON COLUMN data_ops.ingestion_batches.records_extracted IS 'Rows read from upstream';
COMMENT ON COLUMN data_ops.ingestion_batches.records_loaded IS 'Rows committed to warehouse';
COMMENT ON COLUMN data_ops.ingestion_batches.rejected_rows_count IS 'Rows rejected due to validation or missing mapping';
COMMENT ON COLUMN data_ops.ingestion_batches.error_diagnostic IS 'Error or warning diagnostic log';
COMMENT ON COLUMN data_ops.ingestion_batches.started_at IS 'Sync start timestamp';
COMMENT ON COLUMN data_ops.ingestion_batches.completed_at IS 'Sync completion timestamp';

-- ============================================================================
-- Foreign Key Constraints
-- ============================================================================
ALTER TABLE agreements.agreement_files ADD CONSTRAINT fk_agreement_files_drive_id FOREIGN KEY (drive_id) REFERENCES agreements.repository_drives(drive_id);
ALTER TABLE agreements.agreement_files ADD CONSTRAINT fk_agreement_files_matched_group_id FOREIGN KEY (matched_group_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);
ALTER TABLE agreements.agreement_citations ADD CONSTRAINT fk_agreement_citations_agreement_id FOREIGN KEY (agreement_id) REFERENCES agreements.agreement_files(agreement_id);
ALTER TABLE agreements.counterparty_aliases ADD CONSTRAINT fk_counterparty_aliases_conglomerate_id FOREIGN KEY (conglomerate_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);
ALTER TABLE portal_app.inquiry_threads ADD CONSTRAINT fk_inquiry_threads_operator_id FOREIGN KEY (operator_id) REFERENCES portal_app.platform_operators(operator_id);
ALTER TABLE portal_app.thread_utterances ADD CONSTRAINT fk_thread_utterances_thread_id FOREIGN KEY (thread_id) REFERENCES portal_app.inquiry_threads(thread_id);
ALTER TABLE master_ref.taxonomy_nodes ADD CONSTRAINT fk_taxonomy_nodes_facet_code FOREIGN KEY (facet_code) REFERENCES master_ref.taxonomy_facets(facet_code);
ALTER TABLE master_ref.taxonomy_crosswalks ADD CONSTRAINT fk_taxonomy_crosswalks_facet_code FOREIGN KEY (facet_code) REFERENCES master_ref.taxonomy_facets(facet_code);
ALTER TABLE master_ref.taxonomy_crosswalks ADD CONSTRAINT fk_taxonomy_crosswalks_target_node_id FOREIGN KEY (target_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE ledger_erp.receivable_debtors ADD CONSTRAINT fk_receivable_debtors_conglomerate_id FOREIGN KEY (conglomerate_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);
ALTER TABLE ledger_erp.receivable_debtors ADD CONSTRAINT fk_receivable_debtors_legal_entity_code FOREIGN KEY (legal_entity_code) REFERENCES master_ref.entity_jurisdictions(legal_entity_code);
ALTER TABLE ledger_erp.sales_invoices ADD CONSTRAINT fk_sales_invoices_debtor_id FOREIGN KEY (debtor_id) REFERENCES ledger_erp.receivable_debtors(debtor_id);
ALTER TABLE ledger_erp.sales_invoices ADD CONSTRAINT fk_sales_invoices_legal_entity_code FOREIGN KEY (legal_entity_code) REFERENCES master_ref.entity_jurisdictions(legal_entity_code);
ALTER TABLE ledger_erp.sales_invoices ADD CONSTRAINT fk_sales_invoices_agreement_id FOREIGN KEY (agreement_id) REFERENCES agreements.agreement_files(agreement_id);
ALTER TABLE ledger_erp.sales_invoice_items ADD CONSTRAINT fk_sales_invoice_items_invoice_id FOREIGN KEY (invoice_id) REFERENCES ledger_erp.sales_invoices(invoice_id);
ALTER TABLE ledger_erp.sales_invoice_items ADD CONSTRAINT fk_sales_invoice_items_catalog_item_id FOREIGN KEY (catalog_item_id) REFERENCES ledger_erp.catalog_offerings(catalog_item_id);
ALTER TABLE ledger_erp.sales_invoice_items ADD CONSTRAINT fk_sales_invoice_items_dim_bundle_id FOREIGN KEY (dim_bundle_id) REFERENCES ledger_erp.tag_bundles(dim_bundle_id);
ALTER TABLE ledger_erp.receivable_ledger_events ADD CONSTRAINT fk_receivable_ledger_events_debtor_id FOREIGN KEY (debtor_id) REFERENCES ledger_erp.receivable_debtors(debtor_id);
ALTER TABLE ledger_erp.receivable_ledger_events ADD CONSTRAINT fk_receivable_ledger_events_applied_invoice_id FOREIGN KEY (applied_invoice_id) REFERENCES ledger_erp.sales_invoices(invoice_id);
ALTER TABLE ledger_erp.receivable_ledger_events ADD CONSTRAINT fk_receivable_ledger_events_gl_voucher_id FOREIGN KEY (gl_voucher_id) REFERENCES ledger_erp.journal_vouchers(voucher_id);
ALTER TABLE ledger_erp.catalog_offerings ADD CONSTRAINT fk_catalog_offerings_default_rev_account_id FOREIGN KEY (default_rev_account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);
ALTER TABLE ledger_erp.analytic_axis_members ADD CONSTRAINT fk_analytic_axis_members_axis_id FOREIGN KEY (axis_id) REFERENCES ledger_erp.analytic_axes(axis_id);
ALTER TABLE ledger_erp.tag_bundle_members ADD CONSTRAINT fk_tag_bundle_members_dim_bundle_id FOREIGN KEY (dim_bundle_id) REFERENCES ledger_erp.tag_bundles(dim_bundle_id);
ALTER TABLE ledger_erp.tag_bundle_members ADD CONSTRAINT fk_tag_bundle_members_axis_id FOREIGN KEY (axis_id) REFERENCES ledger_erp.analytic_axes(axis_id);
ALTER TABLE ledger_erp.tag_bundle_members ADD CONSTRAINT fk_tag_bundle_members_member_id FOREIGN KEY (member_id) REFERENCES ledger_erp.analytic_axis_members(member_id);
ALTER TABLE ledger_erp.chart_of_accounts ADD CONSTRAINT fk_chart_of_accounts_stmt_group_id FOREIGN KEY (stmt_group_id) REFERENCES ledger_erp.ledger_statement_groups(stmt_group_id);
ALTER TABLE ledger_erp.journal_vouchers ADD CONSTRAINT fk_journal_vouchers_legal_entity_code FOREIGN KEY (legal_entity_code) REFERENCES master_ref.entity_jurisdictions(legal_entity_code);
ALTER TABLE ledger_erp.journal_postings ADD CONSTRAINT fk_journal_postings_voucher_id FOREIGN KEY (voucher_id) REFERENCES ledger_erp.journal_vouchers(voucher_id);
ALTER TABLE ledger_erp.journal_postings ADD CONSTRAINT fk_journal_postings_account_id FOREIGN KEY (account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);
ALTER TABLE ledger_erp.journal_postings ADD CONSTRAINT fk_journal_postings_dim_bundle_id FOREIGN KEY (dim_bundle_id) REFERENCES ledger_erp.tag_bundles(dim_bundle_id);
ALTER TABLE ledger_erp.legacy_coa_crosswalk ADD CONSTRAINT fk_legacy_coa_crosswalk_modern_account_id FOREIGN KEY (modern_account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);
ALTER TABLE ledger_erp.cost_account_links ADD CONSTRAINT fk_cost_account_links_cost_node_id FOREIGN KEY (cost_node_id) REFERENCES ledger_erp.cost_taxonomy_nodes(cost_node_id);
ALTER TABLE ledger_erp.cost_account_links ADD CONSTRAINT fk_cost_account_links_account_id FOREIGN KEY (account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);
ALTER TABLE pipeline_crm.prospect_accounts ADD CONSTRAINT fk_prospect_accounts_conglomerate_id FOREIGN KEY (conglomerate_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);
ALTER TABLE pipeline_crm.prospect_accounts ADD CONSTRAINT fk_prospect_accounts_account_owner_rep_id FOREIGN KEY (account_owner_rep_id) REFERENCES pipeline_crm.quota_carriers(rep_id);
ALTER TABLE pipeline_crm.deal_opportunities ADD CONSTRAINT fk_deal_opportunities_crm_account_id FOREIGN KEY (crm_account_id) REFERENCES pipeline_crm.prospect_accounts(crm_account_id);
ALTER TABLE pipeline_crm.deal_opportunities ADD CONSTRAINT fk_deal_opportunities_owner_rep_id FOREIGN KEY (owner_rep_id) REFERENCES pipeline_crm.quota_carriers(rep_id);
ALTER TABLE pipeline_crm.deal_opportunities ADD CONSTRAINT fk_deal_opportunities_forecast_cat_code FOREIGN KEY (forecast_cat_code) REFERENCES pipeline_crm.forecast_buckets(forecast_cat_code);
ALTER TABLE pipeline_crm.deal_opportunities ADD CONSTRAINT fk_deal_opportunities_linked_agreement_id FOREIGN KEY (linked_agreement_id) REFERENCES agreements.agreement_files(agreement_id);
ALTER TABLE pipeline_crm.deal_history_snapshots ADD CONSTRAINT fk_deal_history_snapshots_deal_id FOREIGN KEY (deal_id) REFERENCES pipeline_crm.deal_opportunities(deal_id);
ALTER TABLE pipeline_crm.deal_product_splits ADD CONSTRAINT fk_deal_product_splits_deal_id FOREIGN KEY (deal_id) REFERENCES pipeline_crm.deal_opportunities(deal_id);
ALTER TABLE pipeline_crm.deal_product_splits ADD CONSTRAINT fk_deal_product_splits_catalog_item_id FOREIGN KEY (catalog_item_id) REFERENCES ledger_erp.catalog_offerings(catalog_item_id);
ALTER TABLE pipeline_crm.quota_carriers ADD CONSTRAINT fk_quota_carriers_squad_id FOREIGN KEY (squad_id) REFERENCES pipeline_crm.territory_squads(squad_id);
ALTER TABLE fpna_plan.financial_targets ADD CONSTRAINT fk_financial_targets_region_node_id FOREIGN KEY (region_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE fpna_plan.financial_targets ADD CONSTRAINT fk_financial_targets_product_line_node_id FOREIGN KEY (product_line_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE fpna_plan.financial_targets ADD CONSTRAINT fk_financial_targets_industry_node_id FOREIGN KEY (industry_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE fpna_plan.financial_targets ADD CONSTRAINT fk_financial_targets_cost_node_id FOREIGN KEY (cost_node_id) REFERENCES ledger_erp.cost_taxonomy_nodes(cost_node_id);
ALTER TABLE exec_reporting.monthly_revenue_posture ADD CONSTRAINT fk_monthly_revenue_posture_region_node_id FOREIGN KEY (region_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE exec_reporting.monthly_revenue_posture ADD CONSTRAINT fk_monthly_revenue_posture_product_line_node_id FOREIGN KEY (product_line_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE exec_reporting.monthly_revenue_posture ADD CONSTRAINT fk_monthly_revenue_posture_industry_node_id FOREIGN KEY (industry_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE exec_reporting.horizon_revenue_outlook ADD CONSTRAINT fk_horizon_revenue_outlook_region_node_id FOREIGN KEY (region_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);
ALTER TABLE exec_reporting.horizon_revenue_outlook ADD CONSTRAINT fk_horizon_revenue_outlook_product_line_node_id FOREIGN KEY (product_line_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);

-- ============================================================================
-- Analytical & Join Indexes
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_repository_drives_owning_region_code ON agreements.repository_drives (owning_region_code);
CREATE INDEX IF NOT EXISTS idx_agreement_files_matched_group_id ON agreements.agreement_files (matched_group_id);
CREATE INDEX IF NOT EXISTS idx_agreement_files_execution_status ON agreements.agreement_files (execution_status);
CREATE INDEX IF NOT EXISTS idx_agreement_files_agreement_type ON agreements.agreement_files (agreement_type);
CREATE INDEX IF NOT EXISTS idx_agreement_citations_agreement_id ON agreements.agreement_citations (agreement_id);
CREATE INDEX IF NOT EXISTS idx_agreement_citations_ref_token ON agreements.agreement_citations (ref_token);
CREATE INDEX IF NOT EXISTS idx_counterparty_aliases_conglomerate_id ON agreements.counterparty_aliases (conglomerate_id);
CREATE INDEX IF NOT EXISTS idx_counterparty_aliases_alias_name ON agreements.counterparty_aliases (alias_name);
CREATE INDEX IF NOT EXISTS idx_platform_operators_tenant_org_id ON portal_app.platform_operators (tenant_org_id);
CREATE INDEX IF NOT EXISTS idx_platform_operators_security_role ON portal_app.platform_operators (security_role);
CREATE INDEX IF NOT EXISTS idx_inquiry_threads_operator_id ON portal_app.inquiry_threads (operator_id);
CREATE INDEX IF NOT EXISTS idx_inquiry_threads_tenant_org_id ON portal_app.inquiry_threads (tenant_org_id);
CREATE INDEX IF NOT EXISTS idx_thread_utterances_thread_id ON portal_app.thread_utterances (thread_id);
CREATE INDEX IF NOT EXISTS idx_client_conglomerates_primary_industry_code ON master_ref.client_conglomerates (primary_industry_code);
CREATE INDEX IF NOT EXISTS idx_client_conglomerates_primary_region_code ON master_ref.client_conglomerates (primary_region_code);
CREATE INDEX IF NOT EXISTS idx_entity_jurisdictions_region_code ON master_ref.entity_jurisdictions (region_code);
CREATE INDEX IF NOT EXISTS idx_taxonomy_nodes_facet_code ON master_ref.taxonomy_nodes (facet_code);
CREATE INDEX IF NOT EXISTS idx_taxonomy_nodes_canonical_code ON master_ref.taxonomy_nodes (canonical_code);
CREATE INDEX IF NOT EXISTS idx_taxonomy_crosswalks_source_system_code ON master_ref.taxonomy_crosswalks (source_system_code);
CREATE INDEX IF NOT EXISTS idx_taxonomy_crosswalks_facet_code ON master_ref.taxonomy_crosswalks (facet_code);
CREATE INDEX IF NOT EXISTS idx_taxonomy_crosswalks_raw_source_value ON master_ref.taxonomy_crosswalks (raw_source_value);
CREATE INDEX IF NOT EXISTS idx_receivable_debtors_conglomerate_id ON ledger_erp.receivable_debtors (conglomerate_id);
CREATE INDEX IF NOT EXISTS idx_receivable_debtors_legal_entity_code ON ledger_erp.receivable_debtors (legal_entity_code);
CREATE INDEX IF NOT EXISTS idx_sales_invoices_debtor_id ON ledger_erp.sales_invoices (debtor_id);
CREATE INDEX IF NOT EXISTS idx_sales_invoices_fiscal_period ON ledger_erp.sales_invoices (fiscal_period);
CREATE INDEX IF NOT EXISTS idx_sales_invoices_settlement_status ON ledger_erp.sales_invoices (settlement_status);
CREATE INDEX IF NOT EXISTS idx_sales_invoices_agreement_id ON ledger_erp.sales_invoices (agreement_id);
CREATE INDEX IF NOT EXISTS idx_sales_invoice_items_invoice_id ON ledger_erp.sales_invoice_items (invoice_id);
CREATE INDEX IF NOT EXISTS idx_sales_invoice_items_catalog_item_id ON ledger_erp.sales_invoice_items (catalog_item_id);
CREATE INDEX IF NOT EXISTS idx_sales_invoice_items_dim_bundle_id ON ledger_erp.sales_invoice_items (dim_bundle_id);
CREATE INDEX IF NOT EXISTS idx_receivable_ledger_events_debtor_id ON ledger_erp.receivable_ledger_events (debtor_id);
CREATE INDEX IF NOT EXISTS idx_receivable_ledger_events_applied_invoice_id ON ledger_erp.receivable_ledger_events (applied_invoice_id);
CREATE INDEX IF NOT EXISTS idx_receivable_ledger_events_event_type ON ledger_erp.receivable_ledger_events (event_type);
CREATE INDEX IF NOT EXISTS idx_receivable_ledger_events_fiscal_period ON ledger_erp.receivable_ledger_events (fiscal_period);
CREATE INDEX IF NOT EXISTS idx_catalog_offerings_product_line_raw ON ledger_erp.catalog_offerings (product_line_raw);
CREATE INDEX IF NOT EXISTS idx_catalog_offerings_revenue_stream_type ON ledger_erp.catalog_offerings (revenue_stream_type);
CREATE INDEX IF NOT EXISTS idx_analytic_axis_members_axis_id ON ledger_erp.analytic_axis_members (axis_id);
CREATE INDEX IF NOT EXISTS idx_analytic_axis_members_member_code ON ledger_erp.analytic_axis_members (member_code);
CREATE INDEX IF NOT EXISTS idx_tag_bundles_product_line_code ON ledger_erp.tag_bundles (product_line_code);
CREATE INDEX IF NOT EXISTS idx_tag_bundles_geo_unit_code ON ledger_erp.tag_bundles (geo_unit_code);
CREATE INDEX IF NOT EXISTS idx_tag_bundles_dept_code ON ledger_erp.tag_bundles (dept_code);
CREATE INDEX IF NOT EXISTS idx_tag_bundle_members_dim_bundle_id ON ledger_erp.tag_bundle_members (dim_bundle_id);
CREATE INDEX IF NOT EXISTS idx_tag_bundle_members_member_id ON ledger_erp.tag_bundle_members (member_id);
CREATE INDEX IF NOT EXISTS idx_chart_of_accounts_account_number ON ledger_erp.chart_of_accounts (account_number);
CREATE INDEX IF NOT EXISTS idx_chart_of_accounts_account_class ON ledger_erp.chart_of_accounts (account_class);
CREATE INDEX IF NOT EXISTS idx_chart_of_accounts_stmt_group_id ON ledger_erp.chart_of_accounts (stmt_group_id);
CREATE INDEX IF NOT EXISTS idx_journal_vouchers_fiscal_period ON ledger_erp.journal_vouchers (fiscal_period);
CREATE INDEX IF NOT EXISTS idx_journal_vouchers_journal_source ON ledger_erp.journal_vouchers (journal_source);
CREATE INDEX IF NOT EXISTS idx_journal_vouchers_posting_status ON ledger_erp.journal_vouchers (posting_status);
CREATE INDEX IF NOT EXISTS idx_journal_postings_voucher_id ON ledger_erp.journal_postings (voucher_id);
CREATE INDEX IF NOT EXISTS idx_journal_postings_account_id ON ledger_erp.journal_postings (account_id);
CREATE INDEX IF NOT EXISTS idx_journal_postings_dim_bundle_id ON ledger_erp.journal_postings (dim_bundle_id);
CREATE INDEX IF NOT EXISTS idx_legacy_coa_crosswalk_legacy_acct_code ON ledger_erp.legacy_coa_crosswalk (legacy_acct_code);
CREATE INDEX IF NOT EXISTS idx_legacy_coa_crosswalk_modern_account_id ON ledger_erp.legacy_coa_crosswalk (modern_account_id);
CREATE INDEX IF NOT EXISTS idx_cost_taxonomy_nodes_parent_cost_node_id ON ledger_erp.cost_taxonomy_nodes (parent_cost_node_id);
CREATE INDEX IF NOT EXISTS idx_cost_taxonomy_nodes_cost_category_code ON ledger_erp.cost_taxonomy_nodes (cost_category_code);
CREATE INDEX IF NOT EXISTS idx_cost_account_links_cost_node_id ON ledger_erp.cost_account_links (cost_node_id);
CREATE INDEX IF NOT EXISTS idx_cost_account_links_account_id ON ledger_erp.cost_account_links (account_id);
CREATE INDEX IF NOT EXISTS idx_prospect_accounts_conglomerate_id ON pipeline_crm.prospect_accounts (conglomerate_id);
CREATE INDEX IF NOT EXISTS idx_prospect_accounts_crm_region_raw ON pipeline_crm.prospect_accounts (crm_region_raw);
CREATE INDEX IF NOT EXISTS idx_prospect_accounts_crm_industry_raw ON pipeline_crm.prospect_accounts (crm_industry_raw);
CREATE INDEX IF NOT EXISTS idx_deal_opportunities_crm_account_id ON pipeline_crm.deal_opportunities (crm_account_id);
CREATE INDEX IF NOT EXISTS idx_deal_opportunities_pipeline_stage ON pipeline_crm.deal_opportunities (pipeline_stage);
CREATE INDEX IF NOT EXISTS idx_deal_opportunities_close_fiscal_period ON pipeline_crm.deal_opportunities (close_fiscal_period);
CREATE INDEX IF NOT EXISTS idx_deal_opportunities_forecast_cat_code ON pipeline_crm.deal_opportunities (forecast_cat_code);
CREATE INDEX IF NOT EXISTS idx_deal_history_snapshots_deal_id ON pipeline_crm.deal_history_snapshots (deal_id);
CREATE INDEX IF NOT EXISTS idx_deal_history_snapshots_as_of_month ON pipeline_crm.deal_history_snapshots (as_of_month);
CREATE INDEX IF NOT EXISTS idx_deal_product_splits_deal_id ON pipeline_crm.deal_product_splits (deal_id);
CREATE INDEX IF NOT EXISTS idx_deal_product_splits_catalog_item_id ON pipeline_crm.deal_product_splits (catalog_item_id);
CREATE INDEX IF NOT EXISTS idx_deal_product_splits_crm_product_family ON pipeline_crm.deal_product_splits (crm_product_family);
CREATE INDEX IF NOT EXISTS idx_territory_squads_parent_squad_id ON pipeline_crm.territory_squads (parent_squad_id);
CREATE INDEX IF NOT EXISTS idx_territory_squads_theater_region ON pipeline_crm.territory_squads (theater_region);
CREATE INDEX IF NOT EXISTS idx_quota_carriers_squad_id ON pipeline_crm.quota_carriers (squad_id);
CREATE INDEX IF NOT EXISTS idx_quota_carriers_manager_rep_id ON pipeline_crm.quota_carriers (manager_rep_id);
CREATE INDEX IF NOT EXISTS idx_financial_targets_plan_scenario ON fpna_plan.financial_targets (plan_scenario);
CREATE INDEX IF NOT EXISTS idx_financial_targets_fiscal_period ON fpna_plan.financial_targets (fiscal_period);
CREATE INDEX IF NOT EXISTS idx_financial_targets_metric_category ON fpna_plan.financial_targets (metric_category);
CREATE INDEX IF NOT EXISTS idx_financial_targets_region_node_id ON fpna_plan.financial_targets (region_node_id);
CREATE INDEX IF NOT EXISTS idx_financial_targets_product_line_node_id ON fpna_plan.financial_targets (product_line_node_id);
CREATE INDEX IF NOT EXISTS idx_monthly_revenue_posture_snapshot_period ON exec_reporting.monthly_revenue_posture (snapshot_period);
CREATE INDEX IF NOT EXISTS idx_monthly_revenue_posture_region_node_id ON exec_reporting.monthly_revenue_posture (region_node_id);
CREATE INDEX IF NOT EXISTS idx_monthly_revenue_posture_product_line_node_id ON exec_reporting.monthly_revenue_posture (product_line_node_id);
CREATE INDEX IF NOT EXISTS idx_monthly_revenue_posture_industry_node_id ON exec_reporting.monthly_revenue_posture (industry_node_id);
CREATE INDEX IF NOT EXISTS idx_horizon_revenue_outlook_as_of_period ON exec_reporting.horizon_revenue_outlook (as_of_period);
CREATE INDEX IF NOT EXISTS idx_horizon_revenue_outlook_forward_fiscal_year ON exec_reporting.horizon_revenue_outlook (forward_fiscal_year);
CREATE INDEX IF NOT EXISTS idx_horizon_revenue_outlook_region_node_id ON exec_reporting.horizon_revenue_outlook (region_node_id);
CREATE INDEX IF NOT EXISTS idx_horizon_revenue_outlook_product_line_node_id ON exec_reporting.horizon_revenue_outlook (product_line_node_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_batches_upstream_connector ON data_ops.ingestion_batches (upstream_connector);
CREATE INDEX IF NOT EXISTS idx_ingestion_batches_run_status ON data_ops.ingestion_batches (run_status);

-- ============================================================================
-- Ontology & Knowledge Graph Tables (PostgreSQL + pgvector)
-- ============================================================================
CREATE TABLE IF NOT EXISTS udre_kg.kg_nodes (
    node_id VARCHAR(128) PRIMARY KEY,
    node_type VARCHAR(40) NOT NULL,
    label VARCHAR(255) NOT NULL,
    source_id VARCHAR(64),
    properties_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance VARCHAR(80) NOT NULL,
    verification_status VARCHAR(40) NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    confidence NUMERIC(5,4) NOT NULL,
    evidence_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_validated_at TIMESTAMP NOT NULL,
    semantic_embedding vector(64)
);

CREATE TABLE IF NOT EXISTS udre_kg.kg_edges (
    edge_id VARCHAR(128) PRIMARY KEY,
    source_node_id VARCHAR(128) NOT NULL REFERENCES udre_kg.kg_nodes(node_id),
    target_node_id VARCHAR(128) NOT NULL REFERENCES udre_kg.kg_nodes(node_id),
    edge_type VARCHAR(40) NOT NULL,
    properties_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance VARCHAR(80) NOT NULL,
    evidence_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    verification_status VARCHAR(40) NOT NULL,
    schema_version VARCHAR(32) NOT NULL,
    confidence NUMERIC(5,4) NOT NULL,
    last_validated_at TIMESTAMP NOT NULL
);
