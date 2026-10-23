"""Complete DBA-referenced 39-table synthetic financial schema specification and DDL generator.

Preserves all 8 logical domains and 39 tables from dba_ref.txt while renaming all
source-specific tables, columns, and identifiers using the fictional 'Aethelgard Enterprise'
financial architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ColumnDef:
    name: str
    sql_type: str
    nullable: bool = True
    primary_key: bool = False
    fk_target: Optional[str] = None  # "schema.table(column)"
    description: str = ""
    check_expr: Optional[str] = None


@dataclass(frozen=True)
class TableDef:
    original_domain: str
    original_table: str
    synthetic_schema: str
    synthetic_table: str
    description: str
    columns: List[ColumnDef] = field(default_factory=list)
    indexes: List[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.synthetic_schema}.{self.synthetic_table}"


SYNTHETIC_SCHEMAS: Dict[str, str] = {
    "contracts": "agreements",
    "app": "portal_app",
    "ref": "master_ref",
    "erp": "ledger_erp",
    "crm": "pipeline_crm",
    "plan": "fpna_plan",
    "reporting": "exec_reporting",
    "ops": "data_ops",
}


def get_all_39_table_definitions() -> List[TableDef]:
    """Return all 39 table definitions across the 8 documented domains."""
    return [
        # =====================================================================
        # DOMAIN 1: contracts -> agreements (4 tables)
        # =====================================================================
        TableDef(
            original_domain="contracts",
            original_table="document_sources",
            synthetic_schema="agreements",
            synthetic_table="repository_drives",
            description="Folders or cloud repositories where customer agreement files are scanned from.",
            columns=[
                ColumnDef("drive_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique repository drive identifier"),
                ColumnDef("drive_label", "VARCHAR(120)", nullable=False, description="Human-readable drive or bucket name"),
                ColumnDef("storage_provider", "VARCHAR(40)", nullable=False, description="Provider type (ObjectStore, NetworkShare, DocVault)"),
                ColumnDef("root_uri", "VARCHAR(255)", nullable=False, description="Root path or URI scanned"),
                ColumnDef("owning_region_code", "VARCHAR(16)", nullable=True, description="Regional custodian code"),
                ColumnDef("scan_enabled", "BOOLEAN", nullable=False, description="Whether automated ingestion is active"),
                ColumnDef("last_indexed_at", "TIMESTAMP", nullable=True, description="Timestamp of last repository scan"),
            ],
            indexes=["owning_region_code"],
        ),
        TableDef(
            original_domain="contracts",
            original_table="contract_documents",
            synthetic_schema="agreements",
            synthetic_table="agreement_files",
            description="One row per contract file, with its type, dates, value, signature status and matched customer.",
            columns=[
                ColumnDef("agreement_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique contract file identifier"),
                ColumnDef("drive_id", "VARCHAR(32)", nullable=False, fk_target="agreements.repository_drives(drive_id)", description="Source repository drive"),
                ColumnDef("file_title", "VARCHAR(200)", nullable=False, description="Contract document filename"),
                ColumnDef("agreement_type", "VARCHAR(40)", nullable=False, description="Document type (MSA, SOW, Order_Form, Amendment, Renewal)"),
                ColumnDef("execution_status", "VARCHAR(40)", nullable=False, description="Signature status (Fully_Signed, Counterpart_Pending, Draft, Expired, Terminated)"),
                ColumnDef("matched_group_id", "VARCHAR(32)", nullable=True, fk_target="master_ref.client_conglomerates(conglomerate_id)", description="Matched parent customer group (nullable when unmatched)"),
                ColumnDef("raw_counterparty_name", "VARCHAR(160)", nullable=False, description="Customer name extracted from document text"),
                ColumnDef("effective_start_date", "DATE", nullable=False, description="Contract effective start date"),
                ColumnDef("expiration_date", "DATE", nullable=False, description="Contract expiration or renewal date"),
                ColumnDef("contracted_tcv_amount", "NUMERIC(18,2)", nullable=False, description="Total contracted value in document currency"),
                ColumnDef("annual_committed_arr", "NUMERIC(18,2)", nullable=False, description="Annualized committed recurring value"),
                ColumnDef("doc_currency_code", "VARCHAR(8)", nullable=False, description="ISO currency code on agreement"),
                ColumnDef("fx_rate_to_usd", "NUMERIC(12,6)", nullable=False, description="Conversion rate to USD reporting currency"),
                ColumnDef("contracted_tcv_usd", "NUMERIC(18,2)", nullable=False, description="Total contracted value normalized to USD"),
                ColumnDef("signed_date", "DATE", nullable=True, description="Date of final signature execution"),
            ],
            indexes=["matched_group_id", "execution_status", "agreement_type"],
        ),
        TableDef(
            original_domain="contracts",
            original_table="document_references",
            synthetic_schema="agreements",
            synthetic_table="agreement_citations",
            description="PO, SOW and contract numbers found inside a document.",
            columns=[
                ColumnDef("citation_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique reference citation ID"),
                ColumnDef("agreement_id", "VARCHAR(32)", nullable=False, fk_target="agreements.agreement_files(agreement_id)", description="Parent agreement document"),
                ColumnDef("ref_kind", "VARCHAR(32)", nullable=False, description="Reference category (PO_NUMBER, SOW_CODE, MASTER_CONTRACT_NO, QUOTE_REF)"),
                ColumnDef("ref_token", "VARCHAR(80)", nullable=False, description="Extracted identifier token"),
                ColumnDef("extraction_confidence", "NUMERIC(5,4)", nullable=False, description="OCR/NLP extraction confidence score"),
                ColumnDef("page_number", "INTEGER", nullable=False, description="Page where reference was extracted"),
            ],
            indexes=["agreement_id", "ref_token"],
        ),
        TableDef(
            original_domain="contracts",
            original_table="account_aliases",
            synthetic_schema="agreements",
            synthetic_table="counterparty_aliases",
            description="Other names for a customer, used to match documents to the right account.",
            columns=[
                ColumnDef("alias_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique alias record ID"),
                ColumnDef("conglomerate_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.client_conglomerates(conglomerate_id)", description="Canonical customer group"),
                ColumnDef("alias_name", "VARCHAR(160)", nullable=False, description="Alternative DBA, legal, or legacy name"),
                ColumnDef("alias_source", "VARCHAR(40)", nullable=False, description="Where alias originated (Legal_Filing, OCR_Variant, M&A_Legacy, CRM_Nickname)"),
                ColumnDef("is_verified_match", "BOOLEAN", nullable=False, description="Whether data steward verified the alias"),
            ],
            indexes=["conglomerate_id", "alias_name"],
        ),

        # =====================================================================
        # DOMAIN 2: app -> portal_app (3 tables)
        # =====================================================================
        TableDef(
            original_domain="app",
            original_table="users",
            synthetic_schema="portal_app",
            synthetic_table="platform_operators",
            description="People who can log in, with their roles and tenant.",
            columns=[
                ColumnDef("operator_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique user operator ID"),
                ColumnDef("email_handle", "VARCHAR(150)", nullable=False, description="Corporate login email"),
                ColumnDef("display_name", "VARCHAR(120)", nullable=False, description="Full display name"),
                ColumnDef("security_role", "VARCHAR(40)", nullable=False, description="Access role (Finance_Controller, RevOps_Analyst, Executive, Auditor)"),
                ColumnDef("tenant_org_id", "VARCHAR(32)", nullable=False, description="Multi-tenant organization identifier"),
                ColumnDef("is_active", "BOOLEAN", nullable=False, description="Active account flag"),
                ColumnDef("created_at", "TIMESTAMP", nullable=False, description="Account provisioning timestamp"),
            ],
            indexes=["tenant_org_id", "security_role"],
        ),
        TableDef(
            original_domain="app",
            original_table="conversations",
            synthetic_schema="portal_app",
            synthetic_table="inquiry_threads",
            description="A user's chat threads with the assistant.",
            columns=[
                ColumnDef("thread_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique conversation thread ID"),
                ColumnDef("operator_id", "VARCHAR(32)", nullable=False, fk_target="portal_app.platform_operators(operator_id)", description="Owner user ID"),
                ColumnDef("tenant_org_id", "VARCHAR(32)", nullable=False, description="Tenant organization boundary"),
                ColumnDef("thread_topic", "VARCHAR(200)", nullable=False, description="Summary title of inquiry thread"),
                ColumnDef("started_at", "TIMESTAMP", nullable=False, description="Thread creation timestamp"),
                ColumnDef("last_activity_at", "TIMESTAMP", nullable=False, description="Most recent message timestamp"),
            ],
            indexes=["operator_id", "tenant_org_id"],
        ),
        TableDef(
            original_domain="app",
            original_table="messages",
            synthetic_schema="portal_app",
            synthetic_table="thread_utterances",
            description="Individual messages in a conversation, with citations and token usage.",
            columns=[
                ColumnDef("utterance_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique message utterance ID"),
                ColumnDef("thread_id", "VARCHAR(32)", nullable=False, fk_target="portal_app.inquiry_threads(thread_id)", description="Parent conversation thread"),
                ColumnDef("speaker_role", "VARCHAR(24)", nullable=False, description="Role (user, assistant, adjudicator)"),
                ColumnDef("message_body", "TEXT", nullable=False, description="Message text content"),
                ColumnDef("cited_objects_json", "TEXT", nullable=False, description="JSON array of cited tables, queries, and evidence IDs"),
                ColumnDef("prompt_tokens", "INTEGER", nullable=False, description="Input prompt token count"),
                ColumnDef("completion_tokens", "INTEGER", nullable=False, description="Output completion token count"),
                ColumnDef("emitted_at", "TIMESTAMP", nullable=False, description="Message timestamp"),
            ],
            indexes=["thread_id"],
        ),

        # =====================================================================
        # DOMAIN 3: ref -> master_ref (5 tables)
        # =====================================================================
        TableDef(
            original_domain="ref",
            original_table="customer_groups",
            synthetic_schema="master_ref",
            synthetic_table="client_conglomerates",
            description="Parent groups that tie a customer's ERP and CRM records together.",
            columns=[
                ColumnDef("conglomerate_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Global canonical customer parent group ID"),
                ColumnDef("conglomerate_name", "VARCHAR(160)", nullable=False, description="Canonical global enterprise name"),
                ColumnDef("hq_country_iso", "VARCHAR(8)", nullable=False, description="Headquarters ISO country code"),
                ColumnDef("global_tier", "VARCHAR(32)", nullable=False, description="Customer tier (Strategic_G100, Enterprise, Mid_Market)"),
                ColumnDef("primary_industry_code", "VARCHAR(32)", nullable=False, description="Standardized industry vertical code"),
                ColumnDef("primary_region_code", "VARCHAR(32)", nullable=False, description="Standardized global region code"),
                ColumnDef("credit_risk_rating", "VARCHAR(16)", nullable=False, description="Internal credit rating (AAA, AA, A, BBB, WATCH)"),
            ],
            indexes=["primary_industry_code", "primary_region_code"],
        ),
        TableDef(
            original_domain="ref",
            original_table="company_regions",
            synthetic_schema="master_ref",
            synthetic_table="entity_jurisdictions",
            description="Maps each legal entity (company code) to a region.",
            columns=[
                ColumnDef("legal_entity_code", "VARCHAR(16)", nullable=False, primary_key=True, description="ERP legal company code (e.g., AETH-US, AETH-UK)"),
                ColumnDef("legal_entity_name", "VARCHAR(120)", nullable=False, description="Registered legal subsidiary name"),
                ColumnDef("region_code", "VARCHAR(32)", nullable=False, description="Standard reporting region code (AMER, EMEA, APAC)"),
                ColumnDef("functional_currency", "VARCHAR(8)", nullable=False, description="Local statutory currency code"),
                ColumnDef("tax_regime", "VARCHAR(40)", nullable=False, description="Tax jurisdiction classification"),
            ],
            indexes=["region_code"],
        ),
        TableDef(
            original_domain="ref",
            original_table="reporting_dimension_types",
            synthetic_schema="master_ref",
            synthetic_table="taxonomy_facets",
            description="Kinds of reporting dimension, e.g. region, product line or industry.",
            columns=[
                ColumnDef("facet_code", "VARCHAR(32)", nullable=False, primary_key=True, description="Dimension type identifier (REGION, PRODUCT_LINE, INDUSTRY, SEGMENT)"),
                ColumnDef("facet_title", "VARCHAR(100)", nullable=False, description="Human-readable dimension type name"),
                ColumnDef("governance_owner", "VARCHAR(80)", nullable=False, description="Data governance team responsible"),
                ColumnDef("is_mandatory_reporting", "BOOLEAN", nullable=False, description="Whether required on executive snapshots"),
            ],
        ),
        TableDef(
            original_domain="ref",
            original_table="reporting_dimensions",
            synthetic_schema="master_ref",
            synthetic_table="taxonomy_nodes",
            description="The standard values for each dimension type.",
            columns=[
                ColumnDef("node_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique canonical dimension value ID"),
                ColumnDef("facet_code", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_facets(facet_code)", description="Dimension type code"),
                ColumnDef("canonical_code", "VARCHAR(40)", nullable=False, description="Standardized reporting code"),
                ColumnDef("canonical_label", "VARCHAR(120)", nullable=False, description="Standardized reporting display label"),
                ColumnDef("sort_ordinal", "INTEGER", nullable=False, description="Presentation sort order"),
                ColumnDef("is_active", "BOOLEAN", nullable=False, description="Whether active for current fiscal year"),
            ],
            indexes=["facet_code", "canonical_code"],
        ),
        TableDef(
            original_domain="ref",
            original_table="reporting_dimension_mappings",
            synthetic_schema="master_ref",
            synthetic_table="taxonomy_crosswalks",
            description="Converts raw values from each source system into a standard dimension value.",
            columns=[
                ColumnDef("crosswalk_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique mapping rule ID"),
                ColumnDef("source_system_code", "VARCHAR(32)", nullable=False, description="Originating system (ERP, CRM, CONTRACTS, LEGACY_PLAN)"),
                ColumnDef("facet_code", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_facets(facet_code)", description="Dimension facet being mapped"),
                ColumnDef("raw_source_value", "VARCHAR(100)", nullable=False, description="Raw unstandardized value in source system"),
                ColumnDef("target_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Mapped standard taxonomy node"),
                ColumnDef("mapping_confidence", "NUMERIC(5,4)", nullable=False, description="Confidence weight (1.0000 for verified deterministic rules)"),
            ],
            indexes=["source_system_code", "facet_code", "raw_source_value"],
        ),

        # =====================================================================
        # DOMAIN 4: erp -> ledger_erp (15 tables)
        # =====================================================================
        TableDef(
            original_domain="erp",
            original_table="ar_customers",
            synthetic_schema="ledger_erp",
            synthetic_table="receivable_debtors",
            description="Customers as billed in the ERP, with payment terms, currency and address.",
            columns=[
                ColumnDef("debtor_id", "VARCHAR(32)", nullable=False, primary_key=True, description="ERP billing customer account number"),
                ColumnDef("conglomerate_id", "VARCHAR(32)", nullable=True, fk_target="master_ref.client_conglomerates(conglomerate_id)", description="Parent customer group (nullable for unmapped subsidiary accounts)"),
                ColumnDef("legal_entity_code", "VARCHAR(16)", nullable=False, fk_target="master_ref.entity_jurisdictions(legal_entity_code)", description="Billing legal entity company code"),
                ColumnDef("billing_legal_name", "VARCHAR(160)", nullable=False, description="Billed customer legal entity name"),
                ColumnDef("payment_terms_code", "VARCHAR(24)", nullable=False, description="Payment terms (NET30, NET45, NET60, IMMED)"),
                ColumnDef("billing_currency", "VARCHAR(8)", nullable=False, description="Default invoice currency (USD, EUR, GBP)"),
                ColumnDef("billing_city", "VARCHAR(80)", nullable=False, description="Billing address city"),
                ColumnDef("billing_country_iso", "VARCHAR(8)", nullable=False, description="Billing address ISO country"),
                ColumnDef("credit_limit_usd", "NUMERIC(18,2)", nullable=False, description="Approved AR credit limit in USD"),
                ColumnDef("account_status", "VARCHAR(24)", nullable=False, description="ERP account status (ACTIVE, ON_HOLD, INACTIVE)"),
            ],
            indexes=["conglomerate_id", "legal_entity_code"],
        ),
        TableDef(
            original_domain="erp",
            original_table="ar_invoice_headers",
            synthetic_schema="ledger_erp",
            synthetic_table="sales_invoices",
            description="One row per customer invoice, with totals, tax, open balance and due date.",
            columns=[
                ColumnDef("invoice_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique ERP invoice header ID"),
                ColumnDef("invoice_number", "VARCHAR(40)", nullable=False, description="External printed invoice document number"),
                ColumnDef("debtor_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.receivable_debtors(debtor_id)", description="Billed ERP debtor customer"),
                ColumnDef("legal_entity_code", "VARCHAR(16)", nullable=False, fk_target="master_ref.entity_jurisdictions(legal_entity_code)", description="Issuing company code"),
                ColumnDef("agreement_id", "VARCHAR(32)", nullable=True, fk_target="agreements.agreement_files(agreement_id)", description="Linked contract document if matched"),
                ColumnDef("invoice_date", "DATE", nullable=False, description="Invoice posting date"),
                ColumnDef("due_date", "DATE", nullable=False, description="Payment due date"),
                ColumnDef("fiscal_period", "VARCHAR(16)", nullable=False, description="Accounting period YYYY-MM"),
                ColumnDef("doc_currency", "VARCHAR(8)", nullable=False, description="Transaction currency code"),
                ColumnDef("fx_rate_to_usd", "NUMERIC(12,6)", nullable=False, description="Spot FX rate to USD at invoice date"),
                ColumnDef("subtotal_amount_doc", "NUMERIC(18,2)", nullable=False, description="Pre-tax invoice subtotal in document currency"),
                ColumnDef("tax_amount_doc", "NUMERIC(18,2)", nullable=False, description="Tax amount in document currency"),
                ColumnDef("gross_total_doc", "NUMERIC(18,2)", nullable=False, description="Total invoice amount including tax in doc currency"),
                ColumnDef("subtotal_amount_usd", "NUMERIC(18,2)", nullable=False, description="Pre-tax invoice subtotal in USD"),
                ColumnDef("tax_amount_usd", "NUMERIC(18,2)", nullable=False, description="Tax amount in USD"),
                ColumnDef("gross_total_usd", "NUMERIC(18,2)", nullable=False, description="Total invoice amount in USD"),
                ColumnDef("open_balance_usd", "NUMERIC(18,2)", nullable=False, description="Remaining unpaid/uncredited balance in USD"),
                ColumnDef("settlement_status", "VARCHAR(32)", nullable=False, description="Status (PAID, PARTIALLY_PAID, OPEN, OVERDUE, CREDITED, VOID)"),
            ],
            indexes=["debtor_id", "fiscal_period", "settlement_status", "agreement_id"],
        ),
        TableDef(
            original_domain="erp",
            original_table="ar_invoice_lines",
            synthetic_schema="ledger_erp",
            synthetic_table="sales_invoice_items",
            description="Line items on each invoice, with quantity, price and service period.",
            columns=[
                ColumnDef("invoice_item_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique invoice line item ID"),
                ColumnDef("invoice_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.sales_invoices(invoice_id)", description="Parent invoice header ID"),
                ColumnDef("line_number", "INTEGER", nullable=False, description="Sequential line number on invoice"),
                ColumnDef("catalog_item_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.catalog_offerings(catalog_item_id)", description="Sold product or service item"),
                ColumnDef("dim_bundle_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.tag_bundles(dim_bundle_id)", description="Financial dimension set tag"),
                ColumnDef("quantity", "NUMERIC(12,2)", nullable=False, description="Billed quantity"),
                ColumnDef("unit_price_doc", "NUMERIC(18,4)", nullable=False, description="Unit price in document currency"),
                ColumnDef("line_net_amount_doc", "NUMERIC(18,2)", nullable=False, description="Net line amount in document currency"),
                ColumnDef("line_net_amount_usd", "NUMERIC(18,2)", nullable=False, description="Net line amount in USD"),
                ColumnDef("service_start_date", "DATE", nullable=False, description="Revenue recognition service period start"),
                ColumnDef("service_end_date", "DATE", nullable=False, description="Revenue recognition service period end"),
            ],
            indexes=["invoice_id", "catalog_item_id", "dim_bundle_id"],
        ),
        TableDef(
            original_domain="erp",
            original_table="ar_transactions",
            synthetic_schema="ledger_erp",
            synthetic_table="receivable_ledger_events",
            description="Customer ledger entries (invoices, payments, credits) and how they were settled.",
            columns=[
                ColumnDef("ar_event_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique AR subledger entry ID"),
                ColumnDef("debtor_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.receivable_debtors(debtor_id)", description="Customer debtor account"),
                ColumnDef("applied_invoice_id", "VARCHAR(32)", nullable=True, fk_target="ledger_erp.sales_invoices(invoice_id)", description="Invoice this entry originates from or settles against"),
                ColumnDef("event_type", "VARCHAR(32)", nullable=False, description="Entry type (INVOICE_POST, CASH_RECEIPT, CREDIT_MEMO, FX_ADJUSTMENT, WRITE_OFF)"),
                ColumnDef("event_date", "DATE", nullable=False, description="Subledger posting date"),
                ColumnDef("fiscal_period", "VARCHAR(16)", nullable=False, description="Accounting period YYYY-MM"),
                ColumnDef("signed_amount_doc", "NUMERIC(18,2)", nullable=False, description="Signed impact on AR in document currency (+ invoice, - payment/credit)"),
                ColumnDef("signed_amount_usd", "NUMERIC(18,2)", nullable=False, description="Signed impact on AR in USD (+ invoice, - payment/credit)"),
                ColumnDef("settlement_ref_code", "VARCHAR(64)", nullable=True, description="Bank wire, check, or credit memo reference code"),
                ColumnDef("gl_voucher_id", "VARCHAR(32)", nullable=True, fk_target="ledger_erp.journal_vouchers(voucher_id)", description="Posted General Ledger voucher ID"),
                ColumnDef("is_settled", "BOOLEAN", nullable=False, description="Whether fully cleared/reconciled"),
            ],
            indexes=["debtor_id", "applied_invoice_id", "event_type", "fiscal_period"],
        ),
        TableDef(
            original_domain="erp",
            original_table="products",
            synthetic_schema="ledger_erp",
            synthetic_table="catalog_offerings",
            description="Items and services that can be sold, with their revenue type and default account.",
            columns=[
                ColumnDef("catalog_item_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique SKU or product ID"),
                ColumnDef("sku_code", "VARCHAR(40)", nullable=False, description="Product SKU code"),
                ColumnDef("offering_name", "VARCHAR(140)", nullable=False, description="Product or service name"),
                ColumnDef("product_line_raw", "VARCHAR(60)", nullable=False, description="ERP product line classification"),
                ColumnDef("revenue_stream_type", "VARCHAR(40)", nullable=False, description="Revenue stream (SaaS_Subscription, Managed_Cloud, Advisory_Services, Usage_Overage)"),
                ColumnDef("default_rev_account_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.chart_of_accounts(account_id)", description="Default GL revenue account"),
                ColumnDef("list_unit_price_usd", "NUMERIC(18,2)", nullable=False, description="Standard list price in USD"),
            ],
            indexes=["product_line_raw", "revenue_stream_type"],
        ),
        TableDef(
            original_domain="erp",
            original_table="dimension_attributes",
            synthetic_schema="ledger_erp",
            synthetic_table="analytic_axes",
            description="The financial dimensions defined in the ERP, e.g. department or cost center.",
            columns=[
                ColumnDef("axis_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique ERP financial dimension attribute ID"),
                ColumnDef("axis_code", "VARCHAR(32)", nullable=False, description="Dimension code (DEPT, COST_CTR, PROD_LINE, GEO_UNIT)"),
                ColumnDef("axis_name", "VARCHAR(100)", nullable=False, description="Descriptive dimension name"),
                ColumnDef("is_required_on_gl", "BOOLEAN", nullable=False, description="Mandatory for GL postings"),
            ],
        ),
        TableDef(
            original_domain="erp",
            original_table="dimension_values",
            synthetic_schema="ledger_erp",
            synthetic_table="analytic_axis_members",
            description="The allowed values for each financial dimension.",
            columns=[
                ColumnDef("member_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique ERP dimension member ID"),
                ColumnDef("axis_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.analytic_axes(axis_id)", description="Parent dimension attribute"),
                ColumnDef("member_code", "VARCHAR(40)", nullable=False, description="ERP dimension value code"),
                ColumnDef("member_label", "VARCHAR(120)", nullable=False, description="ERP dimension value description"),
                ColumnDef("is_active", "BOOLEAN", nullable=False, description="Active flag"),
            ],
            indexes=["axis_id", "member_code"],
        ),
        TableDef(
            original_domain="erp",
            original_table="dimension_sets",
            synthetic_schema="ledger_erp",
            synthetic_table="tag_bundles",
            description="A saved combination of dimension values that a transaction is tagged with.",
            columns=[
                ColumnDef("dim_bundle_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique dimension combination set ID"),
                ColumnDef("bundle_signature", "VARCHAR(160)", nullable=False, description="Composite key string of dimension codes"),
                ColumnDef("dept_code", "VARCHAR(40)", nullable=False, description="Department code in bundle"),
                ColumnDef("cost_center_code", "VARCHAR(40)", nullable=False, description="Cost center code in bundle"),
                ColumnDef("product_line_code", "VARCHAR(40)", nullable=False, description="Product line code in bundle"),
                ColumnDef("geo_unit_code", "VARCHAR(40)", nullable=False, description="Geographic unit code in bundle"),
            ],
            indexes=["product_line_code", "geo_unit_code", "dept_code"],
        ),
        TableDef(
            original_domain="erp",
            original_table="dimension_set_values",
            synthetic_schema="ledger_erp",
            synthetic_table="tag_bundle_members",
            description="Breaks each dimension set into its individual values.",
            columns=[
                ColumnDef("bundle_member_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique bridge row ID"),
                ColumnDef("dim_bundle_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.tag_bundles(dim_bundle_id)", description="Parent dimension set ID"),
                ColumnDef("axis_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.analytic_axes(axis_id)", description="Dimension attribute ID"),
                ColumnDef("member_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.analytic_axis_members(member_id)", description="Allowed dimension member ID"),
            ],
            indexes=["dim_bundle_id", "member_id"],
        ),
        TableDef(
            original_domain="erp",
            original_table="gl_account_categories",
            synthetic_schema="ledger_erp",
            synthetic_table="ledger_statement_groups",
            description="Groups of general ledger accounts used for financial statements.",
            columns=[
                ColumnDef("stmt_group_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique statement category ID"),
                ColumnDef("group_code", "VARCHAR(32)", nullable=False, description="Category code (REV_REC, REV_SERV, COGS, OPEX_RD, OPEX_SM, OPEX_GA, ASSET_AR, ASSET_CASH, LIAB_DEF)"),
                ColumnDef("group_title", "VARCHAR(120)", nullable=False, description="Statement line title"),
                ColumnDef("financial_statement", "VARCHAR(32)", nullable=False, description="Statement type (INCOME_STATEMENT, BALANCE_SHEET)"),
                ColumnDef("rollup_section", "VARCHAR(40)", nullable=False, description="Section (REVENUE, COST_OF_SALES, OPERATING_EXPENSE, ASSETS, LIABILITIES)"),
            ],
        ),
        TableDef(
            original_domain="erp",
            original_table="gl_accounts",
            synthetic_schema="ledger_erp",
            synthetic_table="chart_of_accounts",
            description="The chart of accounts: account number, type and normal balance.",
            columns=[
                ColumnDef("account_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique GL account ID"),
                ColumnDef("account_number", "VARCHAR(24)", nullable=False, description="GL account number (e.g., 4010, 1100, 6210)"),
                ColumnDef("account_title", "VARCHAR(120)", nullable=False, description="Account name"),
                ColumnDef("stmt_group_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.ledger_statement_groups(stmt_group_id)", description="Financial statement category"),
                ColumnDef("account_class", "VARCHAR(24)", nullable=False, description="Class (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)"),
                ColumnDef("normal_balance_side", "VARCHAR(8)", nullable=False, description="Normal balance (DEBIT or CREDIT)"),
                ColumnDef("is_posting_allowed", "BOOLEAN", nullable=False, description="Whether direct journal posting is permitted"),
            ],
            indexes=["account_number", "account_class", "stmt_group_id"],
        ),
        TableDef(
            original_domain="erp",
            original_table="gl_journal_headers",
            synthetic_schema="ledger_erp",
            synthetic_table="journal_vouchers",
            description="One row per journal entry, with its fiscal period, dates and voucher.",
            columns=[
                ColumnDef("voucher_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique GL journal header ID"),
                ColumnDef("voucher_number", "VARCHAR(40)", nullable=False, description="Printed journal voucher code"),
                ColumnDef("legal_entity_code", "VARCHAR(16)", nullable=False, fk_target="master_ref.entity_jurisdictions(legal_entity_code)", description="Company code"),
                ColumnDef("journal_source", "VARCHAR(32)", nullable=False, description="Origin (AR_SUBLEDGER, REV_SCHED, PAYROLL, AP_EXPENSE, MANUAL_ADJ)"),
                ColumnDef("posting_date", "DATE", nullable=False, description="Effective GL posting date"),
                ColumnDef("fiscal_period", "VARCHAR(16)", nullable=False, description="Fiscal period YYYY-MM"),
                ColumnDef("fiscal_year", "INTEGER", nullable=False, description="Fiscal year"),
                ColumnDef("posting_status", "VARCHAR(24)", nullable=False, description="Status (POSTED, PENDING_APPROVAL, REVERSED)"),
                ColumnDef("header_memo", "VARCHAR(200)", nullable=False, description="Journal entry description"),
            ],
            indexes=["fiscal_period", "journal_source", "posting_status"],
        ),
        TableDef(
            original_domain="erp",
            original_table="gl_journal_lines",
            synthetic_schema="ledger_erp",
            synthetic_table="journal_postings",
            description="The debit and credit lines of each journal entry.",
            columns=[
                ColumnDef("posting_line_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique GL journal line ID"),
                ColumnDef("voucher_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.journal_vouchers(voucher_id)", description="Parent journal header"),
                ColumnDef("line_seq", "INTEGER", nullable=False, description="Line sequence number"),
                ColumnDef("account_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.chart_of_accounts(account_id)", description="GL account posted"),
                ColumnDef("dim_bundle_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.tag_bundles(dim_bundle_id)", description="Financial dimension bundle"),
                ColumnDef("debit_amount_usd", "NUMERIC(18,2)", nullable=False, description="Debit amount in USD"),
                ColumnDef("credit_amount_usd", "NUMERIC(18,2)", nullable=False, description="Credit amount in USD"),
                ColumnDef("net_signed_usd", "NUMERIC(18,2)", nullable=False, description="Debit minus Credit in USD"),
                ColumnDef("line_narrative", "VARCHAR(200)", nullable=False, description="Line item memo"),
            ],
            indexes=["voucher_id", "account_id", "dim_bundle_id"],
        ),
        TableDef(
            original_domain="erp",
            original_table="legacy_account_map",
            synthetic_schema="ledger_erp",
            synthetic_table="legacy_coa_crosswalk",
            description="Maps old account numbers to the current chart of accounts.",
            columns=[
                ColumnDef("legacy_map_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique legacy mapping ID"),
                ColumnDef("legacy_system_name", "VARCHAR(40)", nullable=False, description="Prior ERP name (LegacyLedger_EU, LegacyBooks_US)"),
                ColumnDef("legacy_acct_code", "VARCHAR(32)", nullable=False, description="Historical account number"),
                ColumnDef("legacy_acct_desc", "VARCHAR(120)", nullable=False, description="Historical account label"),
                ColumnDef("modern_account_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.chart_of_accounts(account_id)", description="Mapped current GL account ID"),
                ColumnDef("effective_cutover_date", "DATE", nullable=False, description="Date migration cutover took effect"),
            ],
            indexes=["legacy_acct_code", "modern_account_id"],
        ),
        TableDef(
            original_domain="erp",
            original_table="expense_categories",
            synthetic_schema="ledger_erp",
            synthetic_table="cost_taxonomy_nodes",
            description="The expense hierarchy (function down to expense line), each row pointing to its parent.",
            columns=[
                ColumnDef("cost_node_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique expense category node ID"),
                ColumnDef("parent_cost_node_id", "VARCHAR(32)", nullable=True, description="Self-referencing parent expense category ID"),
                ColumnDef("hierarchy_level", "INTEGER", nullable=False, description="Depth in hierarchy (1=Function, 2=Department_Group, 3=Expense_Line)"),
                ColumnDef("cost_category_code", "VARCHAR(40)", nullable=False, description="Code for cost category"),
                ColumnDef("cost_category_title", "VARCHAR(120)", nullable=False, description="Display name of expense category"),
            ],
            indexes=["parent_cost_node_id", "cost_category_code"],
        ),
        TableDef(
            original_domain="erp",
            original_table="expense_account_map",
            synthetic_schema="ledger_erp",
            synthetic_table="cost_account_links",
            description="Assigns GL accounts to expense categories.",
            columns=[
                ColumnDef("link_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique mapping link ID"),
                ColumnDef("cost_node_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.cost_taxonomy_nodes(cost_node_id)", description="Leaf expense category node"),
                ColumnDef("account_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.chart_of_accounts(account_id)", description="GL expense account"),
                ColumnDef("allocation_weight", "NUMERIC(5,4)", nullable=False, description="Allocation percentage (typically 1.0000)"),
            ],
            indexes=["cost_node_id", "account_id"],
        ),

        # =====================================================================
        # DOMAIN 5: crm -> pipeline_crm (7 tables)
        # =====================================================================
        TableDef(
            original_domain="crm",
            original_table="accounts",
            synthetic_schema="pipeline_crm",
            synthetic_table="prospect_accounts",
            description="Customer accounts in the CRM, with region, industry and segment.",
            columns=[
                ColumnDef("crm_account_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique CRM account ID"),
                ColumnDef("conglomerate_id", "VARCHAR(32)", nullable=True, fk_target="master_ref.client_conglomerates(conglomerate_id)", description="Parent customer group (nullable for unlinked prospects)"),
                ColumnDef("crm_account_name", "VARCHAR(160)", nullable=False, description="Account name in CRM"),
                ColumnDef("crm_region_raw", "VARCHAR(40)", nullable=False, description="CRM raw territory/region string"),
                ColumnDef("crm_industry_raw", "VARCHAR(60)", nullable=False, description="CRM raw industry classification"),
                ColumnDef("market_segment", "VARCHAR(32)", nullable=False, description="Segment (Enterprise, Commercial, Strategic)"),
                ColumnDef("account_owner_rep_id", "VARCHAR(32)", nullable=False, fk_target="pipeline_crm.quota_carriers(rep_id)", description="Assigned account executive"),
                ColumnDef("is_existing_customer", "BOOLEAN", nullable=False, description="Whether account already has active contracts"),
            ],
            indexes=["conglomerate_id", "crm_region_raw", "crm_industry_raw"],
        ),
        TableDef(
            original_domain="crm",
            original_table="opportunities",
            synthetic_schema="pipeline_crm",
            synthetic_table="deal_opportunities",
            description="Current deals, with stage, probability, contract value and close date.",
            columns=[
                ColumnDef("deal_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique CRM opportunity ID"),
                ColumnDef("crm_account_id", "VARCHAR(32)", nullable=False, fk_target="pipeline_crm.prospect_accounts(crm_account_id)", description="Customer CRM account"),
                ColumnDef("owner_rep_id", "VARCHAR(32)", nullable=False, fk_target="pipeline_crm.quota_carriers(rep_id)", description="Sales rep owning the deal"),
                ColumnDef("deal_title", "VARCHAR(180)", nullable=False, description="Opportunity name"),
                ColumnDef("deal_type", "VARCHAR(32)", nullable=False, description="Deal motion (New_Logo, Expansion, Renewal)"),
                ColumnDef("pipeline_stage", "VARCHAR(40)", nullable=False, description="Current stage (Closed_Won, Commit, Best_Case, Negotiation, Qualification, Closed_Lost)"),
                ColumnDef("forecast_cat_code", "VARCHAR(32)", nullable=False, fk_target="pipeline_crm.forecast_buckets(forecast_cat_code)", description="Forecast category code"),
                ColumnDef("win_probability_pct", "NUMERIC(5,2)", nullable=False, description="Win probability percentage (0 to 100)"),
                ColumnDef("deal_tcv_usd", "NUMERIC(18,2)", nullable=False, description="Total contract value in USD"),
                ColumnDef("deal_arr_usd", "NUMERIC(18,2)", nullable=False, description="Annual recurring revenue value in USD"),
                ColumnDef("weighted_arr_usd", "NUMERIC(18,2)", nullable=False, description="Probability-weighted ARR in USD"),
                ColumnDef("target_close_date", "DATE", nullable=False, description="Expected or actual close date"),
                ColumnDef("close_fiscal_period", "VARCHAR(16)", nullable=False, description="Close period YYYY-MM"),
                ColumnDef("linked_agreement_id", "VARCHAR(32)", nullable=True, fk_target="agreements.agreement_files(agreement_id)", description="Executed contract file if Closed Won"),
            ],
            indexes=["crm_account_id", "pipeline_stage", "close_fiscal_period", "forecast_cat_code"],
        ),
        TableDef(
            original_domain="crm",
            original_table="opportunity_snapshots",
            synthetic_schema="pipeline_crm",
            synthetic_table="deal_history_snapshots",
            description="Dated copies of each deal, used to track how the pipeline changes over time.",
            columns=[
                ColumnDef("snapshot_row_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique snapshot row ID"),
                ColumnDef("deal_id", "VARCHAR(32)", nullable=False, fk_target="pipeline_crm.deal_opportunities(deal_id)", description="Opportunity ID"),
                ColumnDef("as_of_month", "VARCHAR(16)", nullable=False, description="Snapshot month YYYY-MM"),
                ColumnDef("snapshot_date", "DATE", nullable=False, description="Exact snapshot capture date"),
                ColumnDef("stage_at_snapshot", "VARCHAR(40)", nullable=False, description="Stage as of snapshot date"),
                ColumnDef("forecast_cat_at_snapshot", "VARCHAR(32)", nullable=False, description="Forecast category at snapshot date"),
                ColumnDef("probability_at_snapshot", "NUMERIC(5,2)", nullable=False, description="Probability % at snapshot date"),
                ColumnDef("arr_at_snapshot_usd", "NUMERIC(18,2)", nullable=False, description="Unweighted ARR in USD at snapshot date"),
                ColumnDef("weighted_arr_at_snapshot_usd", "NUMERIC(18,2)", nullable=False, description="Weighted ARR in USD at snapshot date"),
            ],
            indexes=["deal_id", "as_of_month"],
        ),
        TableDef(
            original_domain="crm",
            original_table="opportunity_line_items",
            synthetic_schema="pipeline_crm",
            synthetic_table="deal_product_splits",
            description="How each deal's value splits across product categories.",
            columns=[
                ColumnDef("deal_split_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique opportunity line item ID"),
                ColumnDef("deal_id", "VARCHAR(32)", nullable=False, fk_target="pipeline_crm.deal_opportunities(deal_id)", description="Parent opportunity ID"),
                ColumnDef("catalog_item_id", "VARCHAR(32)", nullable=False, fk_target="ledger_erp.catalog_offerings(catalog_item_id)", description="Product catalog offering"),
                ColumnDef("crm_product_family", "VARCHAR(60)", nullable=False, description="CRM product category label"),
                ColumnDef("split_arr_usd", "NUMERIC(18,2)", nullable=False, description="Annual recurring value allocated to product line"),
                ColumnDef("split_tcv_usd", "NUMERIC(18,2)", nullable=False, description="Total contract value allocated to product line"),
                ColumnDef("term_months", "INTEGER", nullable=False, description="Subscription term in months"),
            ],
            indexes=["deal_id", "catalog_item_id", "crm_product_family"],
        ),
        TableDef(
            original_domain="crm",
            original_table="sales_teams",
            synthetic_schema="pipeline_crm",
            synthetic_table="territory_squads",
            description="Sales teams, nested under parent teams.",
            columns=[
                ColumnDef("squad_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique sales team ID"),
                ColumnDef("parent_squad_id", "VARCHAR(32)", nullable=True, description="Parent sales team ID in hierarchy"),
                ColumnDef("squad_name", "VARCHAR(120)", nullable=False, description="Sales team name"),
                ColumnDef("theater_region", "VARCHAR(32)", nullable=False, description="Assigned geographic theater"),
                ColumnDef("hierarchy_depth", "INTEGER", nullable=False, description="Team level (1=Global, 2=Theater, 3=Pod)"),
            ],
            indexes=["parent_squad_id", "theater_region"],
        ),
        TableDef(
            original_domain="crm",
            original_table="sales_reps",
            synthetic_schema="pipeline_crm",
            synthetic_table="quota_carriers",
            description="Salespeople, with their team, manager and quota.",
            columns=[
                ColumnDef("rep_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique sales representative ID"),
                ColumnDef("rep_full_name", "VARCHAR(120)", nullable=False, description="Salesperson full name"),
                ColumnDef("squad_id", "VARCHAR(32)", nullable=False, fk_target="pipeline_crm.territory_squads(squad_id)", description="Assigned sales team squad"),
                ColumnDef("manager_rep_id", "VARCHAR(32)", nullable=True, description="Reporting manager ID"),
                ColumnDef("role_title", "VARCHAR(60)", nullable=False, description="Role (VP_Sales, Regional_Director, Enterprise_AE)"),
                ColumnDef("annual_quota_usd", "NUMERIC(18,2)", nullable=False, description="Annual ARR quota target in USD"),
                ColumnDef("active_status", "BOOLEAN", nullable=False, description="Currently employed/active flag"),
            ],
            indexes=["squad_id", "manager_rep_id"],
        ),
        TableDef(
            original_domain="crm",
            original_table="forecast_categories",
            synthetic_schema="pipeline_crm",
            synthetic_table="forecast_buckets",
            description="Forecast categories and the default win probability for each.",
            columns=[
                ColumnDef("forecast_cat_code", "VARCHAR(32)", nullable=False, primary_key=True, description="Forecast category code (CLOSED, COMMIT, BEST_CASE, PIPELINE, OMIT)"),
                ColumnDef("bucket_label", "VARCHAR(80)", nullable=False, description="Display label"),
                ColumnDef("default_probability_pct", "NUMERIC(5,2)", nullable=False, description="Default win weight percentage"),
                ColumnDef("included_in_weighted_pipe", "BOOLEAN", nullable=False, description="Whether included in standard weighted pipeline"),
            ],
        ),

        # =====================================================================
        # DOMAIN 6: plan -> fpna_plan (1 table)
        # =====================================================================
        TableDef(
            original_domain="plan",
            original_table="budget_lines",
            synthetic_schema="fpna_plan",
            synthetic_table="financial_targets",
            description="Budget or forecast amounts by month and reporting dimension.",
            columns=[
                ColumnDef("target_line_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique budget/forecast line ID"),
                ColumnDef("plan_scenario", "VARCHAR(32)", nullable=False, description="Scenario (FY2024_APPROVED_BUDGET, FY2025_APPROVED_BUDGET, FY2025_Q2_REFORECAST, FY2026_BASELINE_PLAN)"),
                ColumnDef("fiscal_period", "VARCHAR(16)", nullable=False, description="Month period YYYY-MM"),
                ColumnDef("fiscal_year", "INTEGER", nullable=False, description="Fiscal year"),
                ColumnDef("metric_category", "VARCHAR(40)", nullable=False, description="Metric (REVENUE_TARGET, OPEX_BUDGET, NEW_ARR_QUOTA)"),
                ColumnDef("region_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Standard region dimension node"),
                ColumnDef("product_line_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Standard product line dimension node"),
                ColumnDef("industry_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Standard industry dimension node"),
                ColumnDef("cost_node_id", "VARCHAR(32)", nullable=True, fk_target="ledger_erp.cost_taxonomy_nodes(cost_node_id)", description="Optional expense category node for OPEX lines"),
                ColumnDef("target_amount_usd", "NUMERIC(18,2)", nullable=False, description="Planned budget or forecast amount in USD"),
            ],
            indexes=["plan_scenario", "fiscal_period", "metric_category", "region_node_id", "product_line_node_id"],
        ),

        # =====================================================================
        # DOMAIN 7: reporting -> exec_reporting (2 tables)
        # =====================================================================
        TableDef(
            original_domain="reporting",
            original_table="revenue_position_snapshots",
            synthetic_schema="exec_reporting",
            synthetic_table="monthly_revenue_posture",
            description="Monthly summary of contracted revenue versus pipeline, by product line, industry and region.",
            columns=[
                ColumnDef("posture_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique snapshot row ID"),
                ColumnDef("snapshot_period", "VARCHAR(16)", nullable=False, description="Reporting month YYYY-MM"),
                ColumnDef("region_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Canonical region dimension ID"),
                ColumnDef("product_line_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Canonical product line dimension ID"),
                ColumnDef("industry_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Canonical industry dimension ID"),
                ColumnDef("recognized_gl_revenue_usd", "NUMERIC(18,2)", nullable=False, description="Actual recognized GL revenue for period in USD"),
                ColumnDef("contracted_arr_usd", "NUMERIC(18,2)", nullable=False, description="Active signed contracted ARR in USD"),
                ColumnDef("open_pipeline_unweighted_usd", "NUMERIC(18,2)", nullable=False, description="Open unweighted CRM pipeline in USD"),
                ColumnDef("open_pipeline_weighted_usd", "NUMERIC(18,2)", nullable=False, description="Open probability-weighted CRM pipeline in USD"),
                ColumnDef("budget_target_usd", "NUMERIC(18,2)", nullable=False, description="Approved budget revenue target in USD"),
                ColumnDef("variance_to_budget_usd", "NUMERIC(18,2)", nullable=False, description="Recognized revenue minus budget target in USD"),
            ],
            indexes=["snapshot_period", "region_node_id", "product_line_node_id", "industry_node_id"],
        ),
        TableDef(
            original_domain="reporting",
            original_table="forward_year_snapshots",
            synthetic_schema="exec_reporting",
            synthetic_table="horizon_revenue_outlook",
            description="Monthly view of next year's revenue: contracted, and contracted plus pipeline.",
            columns=[
                ColumnDef("outlook_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique forward outlook row ID"),
                ColumnDef("as_of_period", "VARCHAR(16)", nullable=False, description="Snapshot observation month YYYY-MM"),
                ColumnDef("forward_fiscal_year", "INTEGER", nullable=False, description="Projected future fiscal year (e.g., 2026)"),
                ColumnDef("region_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Canonical region dimension ID"),
                ColumnDef("product_line_node_id", "VARCHAR(32)", nullable=False, fk_target="master_ref.taxonomy_nodes(node_id)", description="Canonical product line dimension ID"),
                ColumnDef("locked_contracted_rev_usd", "NUMERIC(18,2)", nullable=False, description="Next-year revenue already locked under signed contracts"),
                ColumnDef("weighted_pipeline_addition_usd", "NUMERIC(18,2)", nullable=False, description="Next-year expected contribution from weighted pipeline"),
                ColumnDef("combined_outlook_usd", "NUMERIC(18,2)", nullable=False, description="Contracted plus weighted pipeline outlook in USD"),
                ColumnDef("forward_baseline_plan_usd", "NUMERIC(18,2)", nullable=False, description="Next-year baseline target in USD"),
            ],
            indexes=["as_of_period", "forward_fiscal_year", "region_node_id", "product_line_node_id"],
        ),

        # =====================================================================
        # DOMAIN 8: ops -> data_ops (1 table)
        # =====================================================================
        TableDef(
            original_domain="ops",
            original_table="sync_runs",
            synthetic_schema="data_ops",
            synthetic_table="ingestion_batches",
            description="A log of each data load from a source system, with status, row counts and errors.",
            columns=[
                ColumnDef("batch_id", "VARCHAR(32)", nullable=False, primary_key=True, description="Unique sync batch execution ID"),
                ColumnDef("upstream_connector", "VARCHAR(40)", nullable=False, description="Source connector (ERP_LEDGERCORE, CRM_PIPELINEHUB, DOC_VAULTSCAN, FPNA_PLANGRID)"),
                ColumnDef("target_schema_name", "VARCHAR(40)", nullable=False, description="Destination schema updated"),
                ColumnDef("run_status", "VARCHAR(24)", nullable=False, description="Execution status (SUCCEEDED, PARTIAL_WARNING, FAILED)"),
                ColumnDef("records_extracted", "INTEGER", nullable=False, description="Rows read from upstream"),
                ColumnDef("records_loaded", "INTEGER", nullable=False, description="Rows committed to warehouse"),
                ColumnDef("rejected_rows_count", "INTEGER", nullable=False, description="Rows rejected due to validation or missing mapping"),
                ColumnDef("error_diagnostic", "TEXT", nullable=True, description="Error or warning diagnostic log"),
                ColumnDef("started_at", "TIMESTAMP", nullable=False, description="Sync start timestamp"),
                ColumnDef("completed_at", "TIMESTAMP", nullable=False, description="Sync completion timestamp"),
            ],
            indexes=["upstream_connector", "run_status"],
        ),
    ]


def generate_postgresql_ddl(include_pgvector: bool = True, include_kg: bool = True) -> str:
    """Generate complete production PostgreSQL DDL for all 39 tables (+ optional knowledge graph tables)."""
    tables = get_all_39_table_definitions()
    lines: List[str] = [
        "-- ============================================================================",
        "-- Aethelgard Enterprise Synthetic Financial Database - Complete PostgreSQL DDL",
        "-- 39 Tables across 8 Domains + Ontology/Knowledge Graph Storage",
        "-- ============================================================================",
        "",
    ]
    if include_pgvector:
        lines.append("CREATE EXTENSION IF NOT EXISTS vector;")
        lines.append("")

    for orig_dom, syn_schema in SYNTHETIC_SCHEMAS.items():
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {syn_schema}; -- Maps DBA domain: {orig_dom}")
    if include_kg:
        lines.append("CREATE SCHEMA IF NOT EXISTS udre_kg; -- Persistent Ontology & Knowledge Graph")
    lines.append("")

    # First create tables with primary keys and column definitions
    fk_statements: List[str] = []
    idx_statements: List[str] = []

    for t in tables:
        lines.append(f"-- Domain: {t.original_domain}.{t.original_table} -> {t.full_name}")
        lines.append(f"-- Purpose: {t.description}")
        lines.append(f"CREATE TABLE IF NOT EXISTS {t.full_name} (")
        col_defs: List[str] = []
        pks: List[str] = []
        for idx, c in enumerate(t.columns):
            null_str = "NOT NULL" if not c.nullable else "NULL"
            col_line = f"    {c.name.lower()} {c.sql_type} {null_str}"
            if c.check_expr:
                col_line += f" CHECK ({c.check_expr})"
            col_defs.append(col_line)
            if c.primary_key:
                pks.append(c.name.lower())
            if c.fk_target:
                fk_name = f"fk_{t.synthetic_table}_{c.name.lower()}"[:60]
                fk_statements.append(
                    f"ALTER TABLE {t.full_name} ADD CONSTRAINT {fk_name} "
                    f"FOREIGN KEY ({c.name.lower()}) REFERENCES {c.fk_target};"
                )
        if pks:
            col_defs.append(f"    PRIMARY KEY ({', '.join(pks)})")
        lines.append(",\n".join(col_defs))
        lines.append(");")
        q = lambda s: s.replace("'", "''")  # noqa: E731
        lines.append(f"COMMENT ON TABLE {t.full_name} IS '{q(t.description)}';")
        for c in t.columns:
            if c.description:
                lines.append(f"COMMENT ON COLUMN {t.full_name}.{c.name.lower()} IS '{q(c.description)}';")
        lines.append("")

        for idx_col in t.indexes:
            idx_name = f"idx_{t.synthetic_table}_{idx_col.lower()}"[:60]
            idx_statements.append(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {t.full_name} ({idx_col.lower()});"
            )

    lines.append("-- ============================================================================")
    lines.append("-- Foreign Key Constraints")
    lines.append("-- ============================================================================")
    lines.extend(fk_statements)
    lines.append("")
    lines.append("-- ============================================================================")
    lines.append("-- Analytical & Join Indexes")
    lines.append("-- ============================================================================")
    lines.extend(idx_statements)
    lines.append("")

    if not include_kg:
        return "\n".join(lines)

    # Add Knowledge Graph + pgvector DDL
    lines.extend([
        "-- ============================================================================",
        "-- Ontology & Knowledge Graph Tables (PostgreSQL + pgvector)",
        "-- ============================================================================",
        "CREATE TABLE IF NOT EXISTS udre_kg.kg_nodes (",
        "    node_id VARCHAR(128) PRIMARY KEY,",
        "    node_type VARCHAR(40) NOT NULL,",
        "    label VARCHAR(255) NOT NULL,",
        "    source_id VARCHAR(64),",
        "    properties_json JSONB NOT NULL DEFAULT '{}'::jsonb,",
        "    provenance VARCHAR(80) NOT NULL,",
        "    verification_status VARCHAR(40) NOT NULL,",
        "    schema_version VARCHAR(32) NOT NULL,",
        "    confidence NUMERIC(5,4) NOT NULL,",
        "    evidence_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,",
        "    last_validated_at TIMESTAMP NOT NULL,",
        "    semantic_embedding vector(64)",
        ");",
        "",
        "CREATE TABLE IF NOT EXISTS udre_kg.kg_edges (",
        "    edge_id VARCHAR(128) PRIMARY KEY,",
        "    source_node_id VARCHAR(128) NOT NULL REFERENCES udre_kg.kg_nodes(node_id),",
        "    target_node_id VARCHAR(128) NOT NULL REFERENCES udre_kg.kg_nodes(node_id),",
        "    edge_type VARCHAR(40) NOT NULL,",
        "    properties_json JSONB NOT NULL DEFAULT '{}'::jsonb,",
        "    provenance VARCHAR(80) NOT NULL,",
        "    evidence_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,",
        "    verification_status VARCHAR(40) NOT NULL,",
        "    schema_version VARCHAR(32) NOT NULL,",
        "    confidence NUMERIC(5,4) NOT NULL,",
        "    last_validated_at TIMESTAMP NOT NULL",
        ");",
        "",
    ])

    return "\n".join(lines)


def generate_schema_mapping_markdown() -> str:
    """Produce a complete schema mapping document showing how all 39 DBA tables map to the synthetic schema."""
    tables = get_all_39_table_definitions()
    md: List[str] = [
        "# DBA Reference to Synthetic Financial Schema Mapping",
        "",
        "This document provides the complete 1-to-1 structural mapping between the 39 tables across 8 domains in `dba_ref.txt` and the fictional **Aethelgard Enterprise** synthetic financial schema.",
        "",
        "## 1. Domain-Level Schema Mapping",
        "",
        "| Original DBA Domain | Synthetic PostgreSQL Schema | Domain Responsibility | Table Count |",
        "| :--- | :--- | :--- | :---: |",
        "| `contracts` | `agreements` | Customer contract files, repository drives, PO/SOW citations, and counterparty aliases | 4 |",
        "| `app` | `portal_app` | Assistant application users, multi-tenant conversations, and cited message logs | 3 |",
        "| `ref` | `master_ref` | Global parent customer groups, legal entity jurisdictions, and cross-system dimension mappings | 5 |",
        "| `erp` | `ledger_erp` | Accounts receivable, invoices, subledger settlements, products, dimensions, and General Ledger | 15 |",
        "| `crm` | `pipeline_crm` | Prospect/customer accounts, sales opportunities, historical pipeline snapshots, teams, and quotas | 7 |",
        "| `plan` | `fpna_plan` | Monthly budget and reforecast targets across standard reporting dimensions | 1 |",
        "| `reporting` | `exec_reporting` | Precomputed monthly revenue position and forward-year revenue outlook snapshots | 2 |",
        "| `ops` | `data_ops` | Upstream connector synchronization batch logs, row counts, and rejection diagnostics | 1 |",
        "| **Total** | **8 Schemas** | **Complete Cross-System Enterprise Financial Architecture** | **39** |",
        "",
        "## 2. Complete 39-Table Mapping Matrix",
        "",
        "| # | Original Domain | Original DBA Table | Synthetic Schema.Table | Primary Key | Key Foreign Keys & Cross-Domain Relationships | Logical Responsibility |",
        "| :-: | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for idx, t in enumerate(tables, start=1):
        pks = ", ".join(f"`{c.name.lower()}`" for c in t.columns if c.primary_key)
        fks = ", ".join(f"`{c.name.lower()}` -> `{c.fk_target}`" for c in t.columns if c.fk_target) or "*(Root / Self-Referencing)*"
        md.append(
            f"| {idx} | `{t.original_domain}` | `{t.original_table}` | `{t.full_name}` | {pks} | {fks} | {t.description} |"
        )

    md.extend([
        "",
        "## 3. Built-in Financial Invariants & Deliberate Evaluation Edge Cases",
        "",
        "1. **Invoice Header vs Line Item Reconciliation**: For every invoice in `ledger_erp.sales_invoices`, `subtotal_amount_doc = SUM(sales_invoice_items.line_net_amount_doc)` and `subtotal_amount_usd = SUM(sales_invoice_items.line_net_amount_usd)`.",
        "2. **AR Subledger Open Balance Invariant**: For every active (non-VOID) invoice, `open_balance_usd = gross_total_usd + SUM(receivable_ledger_events.signed_amount_usd WHERE event_type != 'INVOICE_POST')`.",
        "3. **Double-Entry General Ledger Balance**: Every posted journal voucher in `ledger_erp.journal_vouchers` satisfies `SUM(debit_amount_usd) = SUM(credit_amount_usd)` across `ledger_erp.journal_postings`.",
        "4. **Deliberate Edge Cases for Agent Verification**:",
        "   - **Multi-Currency Transactions**: Invoices billed in `USD`, `EUR`, and `GBP` with explicit `fx_rate_to_usd`. Naive summation of `gross_total_doc` across currencies produces an invalid mixed-currency number; agents must use `*_usd` or group by currency.",
        "   - **Partial Payments & Credit Memos**: Invoices with `PARTIALLY_PAID` and `CREDITED` statuses where `gross_total_usd != open_balance_usd`.",
        "   - **Ambiguous / Non-Revenue Statuses**: Includes `VOID` invoices, `Draft` / `Expired` contracts in `agreements.agreement_files`, and `PENDING_APPROVAL` / `REVERSED` journal vouchers in `ledger_erp.journal_vouchers`.",
        "   - **Multiple Customer Identities & Missing Mappings**: Some ERP debtors (`receivable_debtors.conglomerate_id IS NULL`) and contract documents (`matched_group_id IS NULL`) lack direct parent links and can only be linked via `agreements.counterparty_aliases` or source-specific names.",
        "   - **Duplicate-Prone Joins (1-to-Many Fanout)**: Joining `sales_invoices` to `sales_invoice_items` or `receivable_ledger_events` and summing header-level `gross_total_usd` or `open_balance_usd` causes fanout inflation unless aggregated at the header grain or using `COUNT(DISTINCT invoice_id)`.",
        "   - **Cross-System Dimension Terminology**: ERP uses codes like `GEO-NA`, `GEO-EU`, `GEO-AP` while CRM uses `North America`, `Europe`, `Asia Pacific`, reconciled through `master_ref.taxonomy_crosswalks` to standard nodes (`AMER`, `EMEA`, `APAC`).",
    ])
    return "\n".join(md)
