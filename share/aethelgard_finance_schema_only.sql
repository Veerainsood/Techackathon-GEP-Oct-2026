--
-- PostgreSQL database dump
--

-- Dumped from database version 16.2
-- Dumped by pg_dump version 16.2

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: agreements; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA agreements;


--
-- Name: data_ops; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA data_ops;


--
-- Name: exec_reporting; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA exec_reporting;


--
-- Name: fpna_plan; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA fpna_plan;


--
-- Name: ledger_erp; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ledger_erp;


--
-- Name: master_ref; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA master_ref;


--
-- Name: pipeline_crm; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA pipeline_crm;


--
-- Name: portal_app; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA portal_app;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agreement_citations; Type: TABLE; Schema: agreements; Owner: -
--

CREATE TABLE agreements.agreement_citations (
    citation_id character varying(32) NOT NULL,
    agreement_id character varying(32) NOT NULL,
    ref_kind character varying(32) NOT NULL,
    ref_token character varying(80) NOT NULL,
    extraction_confidence numeric(5,4) NOT NULL,
    page_number integer NOT NULL
);


--
-- Name: TABLE agreement_citations; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON TABLE agreements.agreement_citations IS 'PO, SOW and contract numbers found inside a document.';


--
-- Name: COLUMN agreement_citations.citation_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_citations.citation_id IS 'Unique reference citation ID';


--
-- Name: COLUMN agreement_citations.agreement_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_citations.agreement_id IS 'Parent agreement document';


--
-- Name: COLUMN agreement_citations.ref_kind; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_citations.ref_kind IS 'Reference category (PO_NUMBER, SOW_CODE, MASTER_CONTRACT_NO, QUOTE_REF)';


--
-- Name: COLUMN agreement_citations.ref_token; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_citations.ref_token IS 'Extracted identifier token';


--
-- Name: COLUMN agreement_citations.extraction_confidence; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_citations.extraction_confidence IS 'OCR/NLP extraction confidence score';


--
-- Name: COLUMN agreement_citations.page_number; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_citations.page_number IS 'Page where reference was extracted';


--
-- Name: agreement_files; Type: TABLE; Schema: agreements; Owner: -
--

CREATE TABLE agreements.agreement_files (
    agreement_id character varying(32) NOT NULL,
    drive_id character varying(32) NOT NULL,
    file_title character varying(200) NOT NULL,
    agreement_type character varying(40) NOT NULL,
    execution_status character varying(40) NOT NULL,
    matched_group_id character varying(32),
    raw_counterparty_name character varying(160) NOT NULL,
    effective_start_date date NOT NULL,
    expiration_date date NOT NULL,
    contracted_tcv_amount numeric(18,2) NOT NULL,
    annual_committed_arr numeric(18,2) NOT NULL,
    doc_currency_code character varying(8) NOT NULL,
    fx_rate_to_usd numeric(12,6) NOT NULL,
    contracted_tcv_usd numeric(18,2) NOT NULL,
    signed_date date
);


--
-- Name: TABLE agreement_files; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON TABLE agreements.agreement_files IS 'One row per contract file, with its type, dates, value, signature status and matched customer.';


--
-- Name: COLUMN agreement_files.agreement_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.agreement_id IS 'Unique contract file identifier';


--
-- Name: COLUMN agreement_files.drive_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.drive_id IS 'Source repository drive';


--
-- Name: COLUMN agreement_files.file_title; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.file_title IS 'Contract document filename';


--
-- Name: COLUMN agreement_files.agreement_type; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.agreement_type IS 'Document type (MSA, SOW, Order_Form, Amendment, Renewal)';


--
-- Name: COLUMN agreement_files.execution_status; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.execution_status IS 'Signature status (Fully_Signed, Counterpart_Pending, Draft, Expired, Terminated)';


--
-- Name: COLUMN agreement_files.matched_group_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.matched_group_id IS 'Matched parent customer group (nullable when unmatched)';


--
-- Name: COLUMN agreement_files.raw_counterparty_name; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.raw_counterparty_name IS 'Customer name extracted from document text';


--
-- Name: COLUMN agreement_files.effective_start_date; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.effective_start_date IS 'Contract effective start date';


--
-- Name: COLUMN agreement_files.expiration_date; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.expiration_date IS 'Contract expiration or renewal date';


--
-- Name: COLUMN agreement_files.contracted_tcv_amount; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.contracted_tcv_amount IS 'Total contracted value in document currency';


--
-- Name: COLUMN agreement_files.annual_committed_arr; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.annual_committed_arr IS 'Annualized committed recurring value';


--
-- Name: COLUMN agreement_files.doc_currency_code; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.doc_currency_code IS 'ISO currency code on agreement';


--
-- Name: COLUMN agreement_files.fx_rate_to_usd; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.fx_rate_to_usd IS 'Conversion rate to USD reporting currency';


--
-- Name: COLUMN agreement_files.contracted_tcv_usd; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.contracted_tcv_usd IS 'Total contracted value normalized to USD';


--
-- Name: COLUMN agreement_files.signed_date; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.agreement_files.signed_date IS 'Date of final signature execution';


--
-- Name: counterparty_aliases; Type: TABLE; Schema: agreements; Owner: -
--

CREATE TABLE agreements.counterparty_aliases (
    alias_id character varying(32) NOT NULL,
    conglomerate_id character varying(32) NOT NULL,
    alias_name character varying(160) NOT NULL,
    alias_source character varying(40) NOT NULL,
    is_verified_match boolean NOT NULL
);


--
-- Name: TABLE counterparty_aliases; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON TABLE agreements.counterparty_aliases IS 'Other names for a customer, used to match documents to the right account.';


--
-- Name: COLUMN counterparty_aliases.alias_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.counterparty_aliases.alias_id IS 'Unique alias record ID';


--
-- Name: COLUMN counterparty_aliases.conglomerate_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.counterparty_aliases.conglomerate_id IS 'Canonical customer group';


--
-- Name: COLUMN counterparty_aliases.alias_name; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.counterparty_aliases.alias_name IS 'Alternative DBA, legal, or legacy name';


--
-- Name: COLUMN counterparty_aliases.alias_source; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.counterparty_aliases.alias_source IS 'Where alias originated (Legal_Filing, OCR_Variant, M&A_Legacy, CRM_Nickname)';


--
-- Name: COLUMN counterparty_aliases.is_verified_match; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.counterparty_aliases.is_verified_match IS 'Whether data steward verified the alias';


--
-- Name: repository_drives; Type: TABLE; Schema: agreements; Owner: -
--

CREATE TABLE agreements.repository_drives (
    drive_id character varying(32) NOT NULL,
    drive_label character varying(120) NOT NULL,
    storage_provider character varying(40) NOT NULL,
    root_uri character varying(255) NOT NULL,
    owning_region_code character varying(16),
    scan_enabled boolean NOT NULL,
    last_indexed_at timestamp without time zone
);


--
-- Name: TABLE repository_drives; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON TABLE agreements.repository_drives IS 'Folders or cloud repositories where customer agreement files are scanned from.';


--
-- Name: COLUMN repository_drives.drive_id; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.repository_drives.drive_id IS 'Unique repository drive identifier';


--
-- Name: COLUMN repository_drives.drive_label; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.repository_drives.drive_label IS 'Human-readable drive or bucket name';


--
-- Name: COLUMN repository_drives.storage_provider; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.repository_drives.storage_provider IS 'Provider type (ObjectStore, NetworkShare, DocVault)';


--
-- Name: COLUMN repository_drives.root_uri; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.repository_drives.root_uri IS 'Root path or URI scanned';


--
-- Name: COLUMN repository_drives.owning_region_code; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.repository_drives.owning_region_code IS 'Regional custodian code';


--
-- Name: COLUMN repository_drives.scan_enabled; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.repository_drives.scan_enabled IS 'Whether automated ingestion is active';


--
-- Name: COLUMN repository_drives.last_indexed_at; Type: COMMENT; Schema: agreements; Owner: -
--

COMMENT ON COLUMN agreements.repository_drives.last_indexed_at IS 'Timestamp of last repository scan';


--
-- Name: ingestion_batches; Type: TABLE; Schema: data_ops; Owner: -
--

CREATE TABLE data_ops.ingestion_batches (
    batch_id character varying(32) NOT NULL,
    upstream_connector character varying(40) NOT NULL,
    target_schema_name character varying(40) NOT NULL,
    run_status character varying(24) NOT NULL,
    records_extracted integer NOT NULL,
    records_loaded integer NOT NULL,
    rejected_rows_count integer NOT NULL,
    error_diagnostic text,
    started_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone NOT NULL
);


--
-- Name: TABLE ingestion_batches; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON TABLE data_ops.ingestion_batches IS 'A log of each data load from a source system, with status, row counts and errors.';


--
-- Name: COLUMN ingestion_batches.batch_id; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.batch_id IS 'Unique sync batch execution ID';


--
-- Name: COLUMN ingestion_batches.upstream_connector; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.upstream_connector IS 'Source connector (ERP_LEDGERCORE, CRM_PIPELINEHUB, DOC_VAULTSCAN, FPNA_PLANGRID)';


--
-- Name: COLUMN ingestion_batches.target_schema_name; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.target_schema_name IS 'Destination schema updated';


--
-- Name: COLUMN ingestion_batches.run_status; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.run_status IS 'Execution status (SUCCEEDED, PARTIAL_WARNING, FAILED)';


--
-- Name: COLUMN ingestion_batches.records_extracted; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.records_extracted IS 'Rows read from upstream';


--
-- Name: COLUMN ingestion_batches.records_loaded; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.records_loaded IS 'Rows committed to warehouse';


--
-- Name: COLUMN ingestion_batches.rejected_rows_count; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.rejected_rows_count IS 'Rows rejected due to validation or missing mapping';


--
-- Name: COLUMN ingestion_batches.error_diagnostic; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.error_diagnostic IS 'Error or warning diagnostic log';


--
-- Name: COLUMN ingestion_batches.started_at; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.started_at IS 'Sync start timestamp';


--
-- Name: COLUMN ingestion_batches.completed_at; Type: COMMENT; Schema: data_ops; Owner: -
--

COMMENT ON COLUMN data_ops.ingestion_batches.completed_at IS 'Sync completion timestamp';


--
-- Name: horizon_revenue_outlook; Type: TABLE; Schema: exec_reporting; Owner: -
--

CREATE TABLE exec_reporting.horizon_revenue_outlook (
    outlook_id character varying(32) NOT NULL,
    as_of_period character varying(16) NOT NULL,
    forward_fiscal_year integer NOT NULL,
    region_node_id character varying(32) NOT NULL,
    product_line_node_id character varying(32) NOT NULL,
    locked_contracted_rev_usd numeric(18,2) NOT NULL,
    weighted_pipeline_addition_usd numeric(18,2) NOT NULL,
    combined_outlook_usd numeric(18,2) NOT NULL,
    forward_baseline_plan_usd numeric(18,2) NOT NULL
);


--
-- Name: TABLE horizon_revenue_outlook; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON TABLE exec_reporting.horizon_revenue_outlook IS 'Monthly view of next year''s revenue: contracted, and contracted plus pipeline.';


--
-- Name: COLUMN horizon_revenue_outlook.outlook_id; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.outlook_id IS 'Unique forward outlook row ID';


--
-- Name: COLUMN horizon_revenue_outlook.as_of_period; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.as_of_period IS 'Snapshot observation month YYYY-MM';


--
-- Name: COLUMN horizon_revenue_outlook.forward_fiscal_year; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.forward_fiscal_year IS 'Projected future fiscal year (e.g., 2026)';


--
-- Name: COLUMN horizon_revenue_outlook.region_node_id; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.region_node_id IS 'Canonical region dimension ID';


--
-- Name: COLUMN horizon_revenue_outlook.product_line_node_id; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.product_line_node_id IS 'Canonical product line dimension ID';


--
-- Name: COLUMN horizon_revenue_outlook.locked_contracted_rev_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.locked_contracted_rev_usd IS 'Next-year revenue already locked under signed contracts';


--
-- Name: COLUMN horizon_revenue_outlook.weighted_pipeline_addition_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.weighted_pipeline_addition_usd IS 'Next-year expected contribution from weighted pipeline';


--
-- Name: COLUMN horizon_revenue_outlook.combined_outlook_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.combined_outlook_usd IS 'Contracted plus weighted pipeline outlook in USD';


--
-- Name: COLUMN horizon_revenue_outlook.forward_baseline_plan_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.horizon_revenue_outlook.forward_baseline_plan_usd IS 'Next-year baseline target in USD';


--
-- Name: monthly_revenue_posture; Type: TABLE; Schema: exec_reporting; Owner: -
--

CREATE TABLE exec_reporting.monthly_revenue_posture (
    posture_id character varying(32) NOT NULL,
    snapshot_period character varying(16) NOT NULL,
    region_node_id character varying(32) NOT NULL,
    product_line_node_id character varying(32) NOT NULL,
    industry_node_id character varying(32) NOT NULL,
    recognized_gl_revenue_usd numeric(18,2) NOT NULL,
    contracted_arr_usd numeric(18,2) NOT NULL,
    open_pipeline_unweighted_usd numeric(18,2) NOT NULL,
    open_pipeline_weighted_usd numeric(18,2) NOT NULL,
    budget_target_usd numeric(18,2) NOT NULL,
    variance_to_budget_usd numeric(18,2) NOT NULL
);


--
-- Name: TABLE monthly_revenue_posture; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON TABLE exec_reporting.monthly_revenue_posture IS 'Monthly summary of contracted revenue versus pipeline, by product line, industry and region.';


--
-- Name: COLUMN monthly_revenue_posture.posture_id; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.posture_id IS 'Unique snapshot row ID';


--
-- Name: COLUMN monthly_revenue_posture.snapshot_period; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.snapshot_period IS 'Reporting month YYYY-MM';


--
-- Name: COLUMN monthly_revenue_posture.region_node_id; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.region_node_id IS 'Canonical region dimension ID';


--
-- Name: COLUMN monthly_revenue_posture.product_line_node_id; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.product_line_node_id IS 'Canonical product line dimension ID';


--
-- Name: COLUMN monthly_revenue_posture.industry_node_id; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.industry_node_id IS 'Canonical industry dimension ID';


--
-- Name: COLUMN monthly_revenue_posture.recognized_gl_revenue_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.recognized_gl_revenue_usd IS 'Actual recognized GL revenue for period in USD';


--
-- Name: COLUMN monthly_revenue_posture.contracted_arr_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.contracted_arr_usd IS 'Active signed contracted ARR in USD';


--
-- Name: COLUMN monthly_revenue_posture.open_pipeline_unweighted_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.open_pipeline_unweighted_usd IS 'Open unweighted CRM pipeline in USD';


--
-- Name: COLUMN monthly_revenue_posture.open_pipeline_weighted_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.open_pipeline_weighted_usd IS 'Open probability-weighted CRM pipeline in USD';


--
-- Name: COLUMN monthly_revenue_posture.budget_target_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.budget_target_usd IS 'Approved budget revenue target in USD';


--
-- Name: COLUMN monthly_revenue_posture.variance_to_budget_usd; Type: COMMENT; Schema: exec_reporting; Owner: -
--

COMMENT ON COLUMN exec_reporting.monthly_revenue_posture.variance_to_budget_usd IS 'Recognized revenue minus budget target in USD';


--
-- Name: financial_targets; Type: TABLE; Schema: fpna_plan; Owner: -
--

CREATE TABLE fpna_plan.financial_targets (
    target_line_id character varying(32) NOT NULL,
    plan_scenario character varying(32) NOT NULL,
    fiscal_period character varying(16) NOT NULL,
    fiscal_year integer NOT NULL,
    metric_category character varying(40) NOT NULL,
    region_node_id character varying(32) NOT NULL,
    product_line_node_id character varying(32) NOT NULL,
    industry_node_id character varying(32) NOT NULL,
    cost_node_id character varying(32),
    target_amount_usd numeric(18,2) NOT NULL
);


--
-- Name: TABLE financial_targets; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON TABLE fpna_plan.financial_targets IS 'Budget or forecast amounts by month and reporting dimension.';


--
-- Name: COLUMN financial_targets.target_line_id; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.target_line_id IS 'Unique budget/forecast line ID';


--
-- Name: COLUMN financial_targets.plan_scenario; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.plan_scenario IS 'Scenario (FY2024_APPROVED_BUDGET, FY2025_APPROVED_BUDGET, FY2025_Q2_REFORECAST, FY2026_BASELINE_PLAN)';


--
-- Name: COLUMN financial_targets.fiscal_period; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.fiscal_period IS 'Month period YYYY-MM';


--
-- Name: COLUMN financial_targets.fiscal_year; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.fiscal_year IS 'Fiscal year';


--
-- Name: COLUMN financial_targets.metric_category; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.metric_category IS 'Metric (REVENUE_TARGET, OPEX_BUDGET, NEW_ARR_QUOTA)';


--
-- Name: COLUMN financial_targets.region_node_id; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.region_node_id IS 'Standard region dimension node';


--
-- Name: COLUMN financial_targets.product_line_node_id; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.product_line_node_id IS 'Standard product line dimension node';


--
-- Name: COLUMN financial_targets.industry_node_id; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.industry_node_id IS 'Standard industry dimension node';


--
-- Name: COLUMN financial_targets.cost_node_id; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.cost_node_id IS 'Optional expense category node for OPEX lines';


--
-- Name: COLUMN financial_targets.target_amount_usd; Type: COMMENT; Schema: fpna_plan; Owner: -
--

COMMENT ON COLUMN fpna_plan.financial_targets.target_amount_usd IS 'Planned budget or forecast amount in USD';


--
-- Name: analytic_axes; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.analytic_axes (
    axis_id character varying(32) NOT NULL,
    axis_code character varying(32) NOT NULL,
    axis_name character varying(100) NOT NULL,
    is_required_on_gl boolean NOT NULL
);


--
-- Name: TABLE analytic_axes; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.analytic_axes IS 'The financial dimensions defined in the ERP, e.g. department or cost center.';


--
-- Name: COLUMN analytic_axes.axis_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axes.axis_id IS 'Unique ERP financial dimension attribute ID';


--
-- Name: COLUMN analytic_axes.axis_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axes.axis_code IS 'Dimension code (DEPT, COST_CTR, PROD_LINE, GEO_UNIT)';


--
-- Name: COLUMN analytic_axes.axis_name; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axes.axis_name IS 'Descriptive dimension name';


--
-- Name: COLUMN analytic_axes.is_required_on_gl; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axes.is_required_on_gl IS 'Mandatory for GL postings';


--
-- Name: analytic_axis_members; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.analytic_axis_members (
    member_id character varying(32) NOT NULL,
    axis_id character varying(32) NOT NULL,
    member_code character varying(40) NOT NULL,
    member_label character varying(120) NOT NULL,
    is_active boolean NOT NULL
);


--
-- Name: TABLE analytic_axis_members; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.analytic_axis_members IS 'The allowed values for each financial dimension.';


--
-- Name: COLUMN analytic_axis_members.member_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axis_members.member_id IS 'Unique ERP dimension member ID';


--
-- Name: COLUMN analytic_axis_members.axis_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axis_members.axis_id IS 'Parent dimension attribute';


--
-- Name: COLUMN analytic_axis_members.member_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axis_members.member_code IS 'ERP dimension value code';


--
-- Name: COLUMN analytic_axis_members.member_label; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axis_members.member_label IS 'ERP dimension value description';


--
-- Name: COLUMN analytic_axis_members.is_active; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.analytic_axis_members.is_active IS 'Active flag';


--
-- Name: catalog_offerings; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.catalog_offerings (
    catalog_item_id character varying(32) NOT NULL,
    sku_code character varying(40) NOT NULL,
    offering_name character varying(140) NOT NULL,
    product_line_raw character varying(60) NOT NULL,
    revenue_stream_type character varying(40) NOT NULL,
    default_rev_account_id character varying(32) NOT NULL,
    list_unit_price_usd numeric(18,2) NOT NULL
);


--
-- Name: TABLE catalog_offerings; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.catalog_offerings IS 'Items and services that can be sold, with their revenue type and default account.';


--
-- Name: COLUMN catalog_offerings.catalog_item_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.catalog_offerings.catalog_item_id IS 'Unique SKU or product ID';


--
-- Name: COLUMN catalog_offerings.sku_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.catalog_offerings.sku_code IS 'Product SKU code';


--
-- Name: COLUMN catalog_offerings.offering_name; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.catalog_offerings.offering_name IS 'Product or service name';


--
-- Name: COLUMN catalog_offerings.product_line_raw; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.catalog_offerings.product_line_raw IS 'ERP product line classification';


--
-- Name: COLUMN catalog_offerings.revenue_stream_type; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.catalog_offerings.revenue_stream_type IS 'Revenue stream (SaaS_Subscription, Managed_Cloud, Advisory_Services, Usage_Overage)';


--
-- Name: COLUMN catalog_offerings.default_rev_account_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.catalog_offerings.default_rev_account_id IS 'Default GL revenue account';


--
-- Name: COLUMN catalog_offerings.list_unit_price_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.catalog_offerings.list_unit_price_usd IS 'Standard list price in USD';


--
-- Name: chart_of_accounts; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.chart_of_accounts (
    account_id character varying(32) NOT NULL,
    account_number character varying(24) NOT NULL,
    account_title character varying(120) NOT NULL,
    stmt_group_id character varying(32) NOT NULL,
    account_class character varying(24) NOT NULL,
    normal_balance_side character varying(8) NOT NULL,
    is_posting_allowed boolean NOT NULL
);


--
-- Name: TABLE chart_of_accounts; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.chart_of_accounts IS 'The chart of accounts: account number, type and normal balance.';


--
-- Name: COLUMN chart_of_accounts.account_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_id IS 'Unique GL account ID';


--
-- Name: COLUMN chart_of_accounts.account_number; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_number IS 'GL account number (e.g., 4010, 1100, 6210)';


--
-- Name: COLUMN chart_of_accounts.account_title; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_title IS 'Account name';


--
-- Name: COLUMN chart_of_accounts.stmt_group_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.chart_of_accounts.stmt_group_id IS 'Financial statement category';


--
-- Name: COLUMN chart_of_accounts.account_class; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.chart_of_accounts.account_class IS 'Class (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)';


--
-- Name: COLUMN chart_of_accounts.normal_balance_side; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.chart_of_accounts.normal_balance_side IS 'Normal balance (DEBIT or CREDIT)';


--
-- Name: COLUMN chart_of_accounts.is_posting_allowed; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.chart_of_accounts.is_posting_allowed IS 'Whether direct journal posting is permitted';


--
-- Name: cost_account_links; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.cost_account_links (
    link_id character varying(32) NOT NULL,
    cost_node_id character varying(32) NOT NULL,
    account_id character varying(32) NOT NULL,
    allocation_weight numeric(5,4) NOT NULL
);


--
-- Name: TABLE cost_account_links; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.cost_account_links IS 'Assigns GL accounts to expense categories.';


--
-- Name: COLUMN cost_account_links.link_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_account_links.link_id IS 'Unique mapping link ID';


--
-- Name: COLUMN cost_account_links.cost_node_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_account_links.cost_node_id IS 'Leaf expense category node';


--
-- Name: COLUMN cost_account_links.account_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_account_links.account_id IS 'GL expense account';


--
-- Name: COLUMN cost_account_links.allocation_weight; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_account_links.allocation_weight IS 'Allocation percentage (typically 1.0000)';


--
-- Name: cost_taxonomy_nodes; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.cost_taxonomy_nodes (
    cost_node_id character varying(32) NOT NULL,
    parent_cost_node_id character varying(32),
    hierarchy_level integer NOT NULL,
    cost_category_code character varying(40) NOT NULL,
    cost_category_title character varying(120) NOT NULL
);


--
-- Name: TABLE cost_taxonomy_nodes; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.cost_taxonomy_nodes IS 'The expense hierarchy (function down to expense line), each row pointing to its parent.';


--
-- Name: COLUMN cost_taxonomy_nodes.cost_node_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.cost_node_id IS 'Unique expense category node ID';


--
-- Name: COLUMN cost_taxonomy_nodes.parent_cost_node_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.parent_cost_node_id IS 'Self-referencing parent expense category ID';


--
-- Name: COLUMN cost_taxonomy_nodes.hierarchy_level; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.hierarchy_level IS 'Depth in hierarchy (1=Function, 2=Department_Group, 3=Expense_Line)';


--
-- Name: COLUMN cost_taxonomy_nodes.cost_category_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.cost_category_code IS 'Code for cost category';


--
-- Name: COLUMN cost_taxonomy_nodes.cost_category_title; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.cost_taxonomy_nodes.cost_category_title IS 'Display name of expense category';


--
-- Name: journal_postings; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.journal_postings (
    posting_line_id character varying(32) NOT NULL,
    voucher_id character varying(32) NOT NULL,
    line_seq integer NOT NULL,
    account_id character varying(32) NOT NULL,
    dim_bundle_id character varying(32) NOT NULL,
    debit_amount_usd numeric(18,2) NOT NULL,
    credit_amount_usd numeric(18,2) NOT NULL,
    net_signed_usd numeric(18,2) NOT NULL,
    line_narrative character varying(200) NOT NULL
);


--
-- Name: TABLE journal_postings; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.journal_postings IS 'The debit and credit lines of each journal entry.';


--
-- Name: COLUMN journal_postings.posting_line_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.posting_line_id IS 'Unique GL journal line ID';


--
-- Name: COLUMN journal_postings.voucher_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.voucher_id IS 'Parent journal header';


--
-- Name: COLUMN journal_postings.line_seq; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.line_seq IS 'Line sequence number';


--
-- Name: COLUMN journal_postings.account_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.account_id IS 'GL account posted';


--
-- Name: COLUMN journal_postings.dim_bundle_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.dim_bundle_id IS 'Financial dimension bundle';


--
-- Name: COLUMN journal_postings.debit_amount_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.debit_amount_usd IS 'Debit amount in USD';


--
-- Name: COLUMN journal_postings.credit_amount_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.credit_amount_usd IS 'Credit amount in USD';


--
-- Name: COLUMN journal_postings.net_signed_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.net_signed_usd IS 'Debit minus Credit in USD';


--
-- Name: COLUMN journal_postings.line_narrative; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_postings.line_narrative IS 'Line item memo';


--
-- Name: journal_vouchers; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.journal_vouchers (
    voucher_id character varying(32) NOT NULL,
    voucher_number character varying(40) NOT NULL,
    legal_entity_code character varying(16) NOT NULL,
    journal_source character varying(32) NOT NULL,
    posting_date date NOT NULL,
    fiscal_period character varying(16) NOT NULL,
    fiscal_year integer NOT NULL,
    posting_status character varying(24) NOT NULL,
    header_memo character varying(200) NOT NULL
);


--
-- Name: TABLE journal_vouchers; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.journal_vouchers IS 'One row per journal entry, with its fiscal period, dates and voucher.';


--
-- Name: COLUMN journal_vouchers.voucher_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.voucher_id IS 'Unique GL journal header ID';


--
-- Name: COLUMN journal_vouchers.voucher_number; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.voucher_number IS 'Printed journal voucher code';


--
-- Name: COLUMN journal_vouchers.legal_entity_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.legal_entity_code IS 'Company code';


--
-- Name: COLUMN journal_vouchers.journal_source; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.journal_source IS 'Origin (AR_SUBLEDGER, REV_SCHED, PAYROLL, AP_EXPENSE, MANUAL_ADJ)';


--
-- Name: COLUMN journal_vouchers.posting_date; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.posting_date IS 'Effective GL posting date';


--
-- Name: COLUMN journal_vouchers.fiscal_period; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.fiscal_period IS 'Fiscal period YYYY-MM';


--
-- Name: COLUMN journal_vouchers.fiscal_year; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.fiscal_year IS 'Fiscal year';


--
-- Name: COLUMN journal_vouchers.posting_status; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.posting_status IS 'Status (POSTED, PENDING_APPROVAL, REVERSED)';


--
-- Name: COLUMN journal_vouchers.header_memo; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.journal_vouchers.header_memo IS 'Journal entry description';


--
-- Name: ledger_statement_groups; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.ledger_statement_groups (
    stmt_group_id character varying(32) NOT NULL,
    group_code character varying(32) NOT NULL,
    group_title character varying(120) NOT NULL,
    financial_statement character varying(32) NOT NULL,
    rollup_section character varying(40) NOT NULL
);


--
-- Name: TABLE ledger_statement_groups; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.ledger_statement_groups IS 'Groups of general ledger accounts used for financial statements.';


--
-- Name: COLUMN ledger_statement_groups.stmt_group_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.ledger_statement_groups.stmt_group_id IS 'Unique statement category ID';


--
-- Name: COLUMN ledger_statement_groups.group_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.ledger_statement_groups.group_code IS 'Category code (REV_REC, REV_SERV, COGS, OPEX_RD, OPEX_SM, OPEX_GA, ASSET_AR, ASSET_CASH, LIAB_DEF)';


--
-- Name: COLUMN ledger_statement_groups.group_title; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.ledger_statement_groups.group_title IS 'Statement line title';


--
-- Name: COLUMN ledger_statement_groups.financial_statement; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.ledger_statement_groups.financial_statement IS 'Statement type (INCOME_STATEMENT, BALANCE_SHEET)';


--
-- Name: COLUMN ledger_statement_groups.rollup_section; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.ledger_statement_groups.rollup_section IS 'Section (REVENUE, COST_OF_SALES, OPERATING_EXPENSE, ASSETS, LIABILITIES)';


--
-- Name: legacy_coa_crosswalk; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.legacy_coa_crosswalk (
    legacy_map_id character varying(32) NOT NULL,
    legacy_system_name character varying(40) NOT NULL,
    legacy_acct_code character varying(32) NOT NULL,
    legacy_acct_desc character varying(120) NOT NULL,
    modern_account_id character varying(32) NOT NULL,
    effective_cutover_date date NOT NULL
);


--
-- Name: TABLE legacy_coa_crosswalk; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.legacy_coa_crosswalk IS 'Maps old account numbers to the current chart of accounts.';


--
-- Name: COLUMN legacy_coa_crosswalk.legacy_map_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_map_id IS 'Unique legacy mapping ID';


--
-- Name: COLUMN legacy_coa_crosswalk.legacy_system_name; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_system_name IS 'Prior ERP name (LegacyLedger_EU, LegacyBooks_US)';


--
-- Name: COLUMN legacy_coa_crosswalk.legacy_acct_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_acct_code IS 'Historical account number';


--
-- Name: COLUMN legacy_coa_crosswalk.legacy_acct_desc; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.legacy_acct_desc IS 'Historical account label';


--
-- Name: COLUMN legacy_coa_crosswalk.modern_account_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.modern_account_id IS 'Mapped current GL account ID';


--
-- Name: COLUMN legacy_coa_crosswalk.effective_cutover_date; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.legacy_coa_crosswalk.effective_cutover_date IS 'Date migration cutover took effect';


--
-- Name: receivable_debtors; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.receivable_debtors (
    debtor_id character varying(32) NOT NULL,
    conglomerate_id character varying(32),
    legal_entity_code character varying(16) NOT NULL,
    billing_legal_name character varying(160) NOT NULL,
    payment_terms_code character varying(24) NOT NULL,
    billing_currency character varying(8) NOT NULL,
    billing_city character varying(80) NOT NULL,
    billing_country_iso character varying(8) NOT NULL,
    credit_limit_usd numeric(18,2) NOT NULL,
    account_status character varying(24) NOT NULL
);


--
-- Name: TABLE receivable_debtors; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.receivable_debtors IS 'Customers as billed in the ERP, with payment terms, currency and address.';


--
-- Name: COLUMN receivable_debtors.debtor_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.debtor_id IS 'ERP billing customer account number';


--
-- Name: COLUMN receivable_debtors.conglomerate_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.conglomerate_id IS 'Parent customer group (nullable for unmapped subsidiary accounts)';


--
-- Name: COLUMN receivable_debtors.legal_entity_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.legal_entity_code IS 'Billing legal entity company code';


--
-- Name: COLUMN receivable_debtors.billing_legal_name; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_legal_name IS 'Billed customer legal entity name';


--
-- Name: COLUMN receivable_debtors.payment_terms_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.payment_terms_code IS 'Payment terms (NET30, NET45, NET60, IMMED)';


--
-- Name: COLUMN receivable_debtors.billing_currency; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_currency IS 'Default invoice currency (USD, EUR, GBP)';


--
-- Name: COLUMN receivable_debtors.billing_city; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_city IS 'Billing address city';


--
-- Name: COLUMN receivable_debtors.billing_country_iso; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.billing_country_iso IS 'Billing address ISO country';


--
-- Name: COLUMN receivable_debtors.credit_limit_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.credit_limit_usd IS 'Approved AR credit limit in USD';


--
-- Name: COLUMN receivable_debtors.account_status; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_debtors.account_status IS 'ERP account status (ACTIVE, ON_HOLD, INACTIVE)';


--
-- Name: receivable_ledger_events; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.receivable_ledger_events (
    ar_event_id character varying(32) NOT NULL,
    debtor_id character varying(32) NOT NULL,
    applied_invoice_id character varying(32),
    event_type character varying(32) NOT NULL,
    event_date date NOT NULL,
    fiscal_period character varying(16) NOT NULL,
    signed_amount_doc numeric(18,2) NOT NULL,
    signed_amount_usd numeric(18,2) NOT NULL,
    settlement_ref_code character varying(64),
    gl_voucher_id character varying(32),
    is_settled boolean NOT NULL
);


--
-- Name: TABLE receivable_ledger_events; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.receivable_ledger_events IS 'Customer ledger entries (invoices, payments, credits) and how they were settled.';


--
-- Name: COLUMN receivable_ledger_events.ar_event_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.ar_event_id IS 'Unique AR subledger entry ID';


--
-- Name: COLUMN receivable_ledger_events.debtor_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.debtor_id IS 'Customer debtor account';


--
-- Name: COLUMN receivable_ledger_events.applied_invoice_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.applied_invoice_id IS 'Invoice this entry originates from or settles against';


--
-- Name: COLUMN receivable_ledger_events.event_type; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.event_type IS 'Entry type (INVOICE_POST, CASH_RECEIPT, CREDIT_MEMO, FX_ADJUSTMENT, WRITE_OFF)';


--
-- Name: COLUMN receivable_ledger_events.event_date; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.event_date IS 'Subledger posting date';


--
-- Name: COLUMN receivable_ledger_events.fiscal_period; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.fiscal_period IS 'Accounting period YYYY-MM';


--
-- Name: COLUMN receivable_ledger_events.signed_amount_doc; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.signed_amount_doc IS 'Signed impact on AR in document currency (+ invoice, - payment/credit)';


--
-- Name: COLUMN receivable_ledger_events.signed_amount_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.signed_amount_usd IS 'Signed impact on AR in USD (+ invoice, - payment/credit)';


--
-- Name: COLUMN receivable_ledger_events.settlement_ref_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.settlement_ref_code IS 'Bank wire, check, or credit memo reference code';


--
-- Name: COLUMN receivable_ledger_events.gl_voucher_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.gl_voucher_id IS 'Posted General Ledger voucher ID';


--
-- Name: COLUMN receivable_ledger_events.is_settled; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.receivable_ledger_events.is_settled IS 'Whether fully cleared/reconciled';


--
-- Name: sales_invoice_items; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.sales_invoice_items (
    invoice_item_id character varying(32) NOT NULL,
    invoice_id character varying(32) NOT NULL,
    line_number integer NOT NULL,
    catalog_item_id character varying(32) NOT NULL,
    dim_bundle_id character varying(32) NOT NULL,
    quantity numeric(12,2) NOT NULL,
    unit_price_doc numeric(18,4) NOT NULL,
    line_net_amount_doc numeric(18,2) NOT NULL,
    line_net_amount_usd numeric(18,2) NOT NULL,
    service_start_date date NOT NULL,
    service_end_date date NOT NULL
);


--
-- Name: TABLE sales_invoice_items; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.sales_invoice_items IS 'Line items on each invoice, with quantity, price and service period.';


--
-- Name: COLUMN sales_invoice_items.invoice_item_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.invoice_item_id IS 'Unique invoice line item ID';


--
-- Name: COLUMN sales_invoice_items.invoice_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.invoice_id IS 'Parent invoice header ID';


--
-- Name: COLUMN sales_invoice_items.line_number; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.line_number IS 'Sequential line number on invoice';


--
-- Name: COLUMN sales_invoice_items.catalog_item_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.catalog_item_id IS 'Sold product or service item';


--
-- Name: COLUMN sales_invoice_items.dim_bundle_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.dim_bundle_id IS 'Financial dimension set tag';


--
-- Name: COLUMN sales_invoice_items.quantity; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.quantity IS 'Billed quantity';


--
-- Name: COLUMN sales_invoice_items.unit_price_doc; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.unit_price_doc IS 'Unit price in document currency';


--
-- Name: COLUMN sales_invoice_items.line_net_amount_doc; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.line_net_amount_doc IS 'Net line amount in document currency';


--
-- Name: COLUMN sales_invoice_items.line_net_amount_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.line_net_amount_usd IS 'Net line amount in USD';


--
-- Name: COLUMN sales_invoice_items.service_start_date; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.service_start_date IS 'Revenue recognition service period start';


--
-- Name: COLUMN sales_invoice_items.service_end_date; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoice_items.service_end_date IS 'Revenue recognition service period end';


--
-- Name: sales_invoices; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.sales_invoices (
    invoice_id character varying(32) NOT NULL,
    invoice_number character varying(40) NOT NULL,
    debtor_id character varying(32) NOT NULL,
    legal_entity_code character varying(16) NOT NULL,
    agreement_id character varying(32),
    invoice_date date NOT NULL,
    due_date date NOT NULL,
    fiscal_period character varying(16) NOT NULL,
    doc_currency character varying(8) NOT NULL,
    fx_rate_to_usd numeric(12,6) NOT NULL,
    subtotal_amount_doc numeric(18,2) NOT NULL,
    tax_amount_doc numeric(18,2) NOT NULL,
    gross_total_doc numeric(18,2) NOT NULL,
    subtotal_amount_usd numeric(18,2) NOT NULL,
    tax_amount_usd numeric(18,2) NOT NULL,
    gross_total_usd numeric(18,2) NOT NULL,
    open_balance_usd numeric(18,2) NOT NULL,
    settlement_status character varying(32) NOT NULL
);


--
-- Name: TABLE sales_invoices; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.sales_invoices IS 'One row per customer invoice, with totals, tax, open balance and due date.';


--
-- Name: COLUMN sales_invoices.invoice_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.invoice_id IS 'Unique ERP invoice header ID';


--
-- Name: COLUMN sales_invoices.invoice_number; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.invoice_number IS 'External printed invoice document number';


--
-- Name: COLUMN sales_invoices.debtor_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.debtor_id IS 'Billed ERP debtor customer';


--
-- Name: COLUMN sales_invoices.legal_entity_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.legal_entity_code IS 'Issuing company code';


--
-- Name: COLUMN sales_invoices.agreement_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.agreement_id IS 'Linked contract document if matched';


--
-- Name: COLUMN sales_invoices.invoice_date; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.invoice_date IS 'Invoice posting date';


--
-- Name: COLUMN sales_invoices.due_date; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.due_date IS 'Payment due date';


--
-- Name: COLUMN sales_invoices.fiscal_period; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.fiscal_period IS 'Accounting period YYYY-MM';


--
-- Name: COLUMN sales_invoices.doc_currency; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.doc_currency IS 'Transaction currency code';


--
-- Name: COLUMN sales_invoices.fx_rate_to_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.fx_rate_to_usd IS 'Spot FX rate to USD at invoice date';


--
-- Name: COLUMN sales_invoices.subtotal_amount_doc; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.subtotal_amount_doc IS 'Pre-tax invoice subtotal in document currency';


--
-- Name: COLUMN sales_invoices.tax_amount_doc; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.tax_amount_doc IS 'Tax amount in document currency';


--
-- Name: COLUMN sales_invoices.gross_total_doc; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.gross_total_doc IS 'Total invoice amount including tax in doc currency';


--
-- Name: COLUMN sales_invoices.subtotal_amount_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.subtotal_amount_usd IS 'Pre-tax invoice subtotal in USD';


--
-- Name: COLUMN sales_invoices.tax_amount_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.tax_amount_usd IS 'Tax amount in USD';


--
-- Name: COLUMN sales_invoices.gross_total_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.gross_total_usd IS 'Total invoice amount in USD';


--
-- Name: COLUMN sales_invoices.open_balance_usd; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.open_balance_usd IS 'Remaining unpaid/uncredited balance in USD';


--
-- Name: COLUMN sales_invoices.settlement_status; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.sales_invoices.settlement_status IS 'Status (PAID, PARTIALLY_PAID, OPEN, OVERDUE, CREDITED, VOID)';


--
-- Name: tag_bundle_members; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.tag_bundle_members (
    bundle_member_id character varying(32) NOT NULL,
    dim_bundle_id character varying(32) NOT NULL,
    axis_id character varying(32) NOT NULL,
    member_id character varying(32) NOT NULL
);


--
-- Name: TABLE tag_bundle_members; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.tag_bundle_members IS 'Breaks each dimension set into its individual values.';


--
-- Name: COLUMN tag_bundle_members.bundle_member_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundle_members.bundle_member_id IS 'Unique bridge row ID';


--
-- Name: COLUMN tag_bundle_members.dim_bundle_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundle_members.dim_bundle_id IS 'Parent dimension set ID';


--
-- Name: COLUMN tag_bundle_members.axis_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundle_members.axis_id IS 'Dimension attribute ID';


--
-- Name: COLUMN tag_bundle_members.member_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundle_members.member_id IS 'Allowed dimension member ID';


--
-- Name: tag_bundles; Type: TABLE; Schema: ledger_erp; Owner: -
--

CREATE TABLE ledger_erp.tag_bundles (
    dim_bundle_id character varying(32) NOT NULL,
    bundle_signature character varying(160) NOT NULL,
    dept_code character varying(40) NOT NULL,
    cost_center_code character varying(40) NOT NULL,
    product_line_code character varying(40) NOT NULL,
    geo_unit_code character varying(40) NOT NULL
);


--
-- Name: TABLE tag_bundles; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON TABLE ledger_erp.tag_bundles IS 'A saved combination of dimension values that a transaction is tagged with.';


--
-- Name: COLUMN tag_bundles.dim_bundle_id; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundles.dim_bundle_id IS 'Unique dimension combination set ID';


--
-- Name: COLUMN tag_bundles.bundle_signature; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundles.bundle_signature IS 'Composite key string of dimension codes';


--
-- Name: COLUMN tag_bundles.dept_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundles.dept_code IS 'Department code in bundle';


--
-- Name: COLUMN tag_bundles.cost_center_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundles.cost_center_code IS 'Cost center code in bundle';


--
-- Name: COLUMN tag_bundles.product_line_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundles.product_line_code IS 'Product line code in bundle';


--
-- Name: COLUMN tag_bundles.geo_unit_code; Type: COMMENT; Schema: ledger_erp; Owner: -
--

COMMENT ON COLUMN ledger_erp.tag_bundles.geo_unit_code IS 'Geographic unit code in bundle';


--
-- Name: client_conglomerates; Type: TABLE; Schema: master_ref; Owner: -
--

CREATE TABLE master_ref.client_conglomerates (
    conglomerate_id character varying(32) NOT NULL,
    conglomerate_name character varying(160) NOT NULL,
    hq_country_iso character varying(8) NOT NULL,
    global_tier character varying(32) NOT NULL,
    primary_industry_code character varying(32) NOT NULL,
    primary_region_code character varying(32) NOT NULL,
    credit_risk_rating character varying(16) NOT NULL
);


--
-- Name: TABLE client_conglomerates; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON TABLE master_ref.client_conglomerates IS 'Parent groups that tie a customer''s ERP and CRM records together.';


--
-- Name: COLUMN client_conglomerates.conglomerate_id; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.client_conglomerates.conglomerate_id IS 'Global canonical customer parent group ID';


--
-- Name: COLUMN client_conglomerates.conglomerate_name; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.client_conglomerates.conglomerate_name IS 'Canonical global enterprise name';


--
-- Name: COLUMN client_conglomerates.hq_country_iso; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.client_conglomerates.hq_country_iso IS 'Headquarters ISO country code';


--
-- Name: COLUMN client_conglomerates.global_tier; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.client_conglomerates.global_tier IS 'Customer tier (Strategic_G100, Enterprise, Mid_Market)';


--
-- Name: COLUMN client_conglomerates.primary_industry_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.client_conglomerates.primary_industry_code IS 'Standardized industry vertical code';


--
-- Name: COLUMN client_conglomerates.primary_region_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.client_conglomerates.primary_region_code IS 'Standardized global region code';


--
-- Name: COLUMN client_conglomerates.credit_risk_rating; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.client_conglomerates.credit_risk_rating IS 'Internal credit rating (AAA, AA, A, BBB, WATCH)';


--
-- Name: entity_jurisdictions; Type: TABLE; Schema: master_ref; Owner: -
--

CREATE TABLE master_ref.entity_jurisdictions (
    legal_entity_code character varying(16) NOT NULL,
    legal_entity_name character varying(120) NOT NULL,
    region_code character varying(32) NOT NULL,
    functional_currency character varying(8) NOT NULL,
    tax_regime character varying(40) NOT NULL
);


--
-- Name: TABLE entity_jurisdictions; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON TABLE master_ref.entity_jurisdictions IS 'Maps each legal entity (company code) to a region.';


--
-- Name: COLUMN entity_jurisdictions.legal_entity_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.entity_jurisdictions.legal_entity_code IS 'ERP legal company code (e.g., AETH-US, AETH-UK)';


--
-- Name: COLUMN entity_jurisdictions.legal_entity_name; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.entity_jurisdictions.legal_entity_name IS 'Registered legal subsidiary name';


--
-- Name: COLUMN entity_jurisdictions.region_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.entity_jurisdictions.region_code IS 'Standard reporting region code (AMER, EMEA, APAC)';


--
-- Name: COLUMN entity_jurisdictions.functional_currency; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.entity_jurisdictions.functional_currency IS 'Local statutory currency code';


--
-- Name: COLUMN entity_jurisdictions.tax_regime; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.entity_jurisdictions.tax_regime IS 'Tax jurisdiction classification';


--
-- Name: taxonomy_crosswalks; Type: TABLE; Schema: master_ref; Owner: -
--

CREATE TABLE master_ref.taxonomy_crosswalks (
    crosswalk_id character varying(32) NOT NULL,
    source_system_code character varying(32) NOT NULL,
    facet_code character varying(32) NOT NULL,
    raw_source_value character varying(100) NOT NULL,
    target_node_id character varying(32) NOT NULL,
    mapping_confidence numeric(5,4) NOT NULL
);


--
-- Name: TABLE taxonomy_crosswalks; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON TABLE master_ref.taxonomy_crosswalks IS 'Converts raw values from each source system into a standard dimension value.';


--
-- Name: COLUMN taxonomy_crosswalks.crosswalk_id; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_crosswalks.crosswalk_id IS 'Unique mapping rule ID';


--
-- Name: COLUMN taxonomy_crosswalks.source_system_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_crosswalks.source_system_code IS 'Originating system (ERP, CRM, CONTRACTS, LEGACY_PLAN)';


--
-- Name: COLUMN taxonomy_crosswalks.facet_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_crosswalks.facet_code IS 'Dimension facet being mapped';


--
-- Name: COLUMN taxonomy_crosswalks.raw_source_value; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_crosswalks.raw_source_value IS 'Raw unstandardized value in source system';


--
-- Name: COLUMN taxonomy_crosswalks.target_node_id; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_crosswalks.target_node_id IS 'Mapped standard taxonomy node';


--
-- Name: COLUMN taxonomy_crosswalks.mapping_confidence; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_crosswalks.mapping_confidence IS 'Confidence weight (1.0000 for verified deterministic rules)';


--
-- Name: taxonomy_facets; Type: TABLE; Schema: master_ref; Owner: -
--

CREATE TABLE master_ref.taxonomy_facets (
    facet_code character varying(32) NOT NULL,
    facet_title character varying(100) NOT NULL,
    governance_owner character varying(80) NOT NULL,
    is_mandatory_reporting boolean NOT NULL
);


--
-- Name: TABLE taxonomy_facets; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON TABLE master_ref.taxonomy_facets IS 'Kinds of reporting dimension, e.g. region, product line or industry.';


--
-- Name: COLUMN taxonomy_facets.facet_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_facets.facet_code IS 'Dimension type identifier (REGION, PRODUCT_LINE, INDUSTRY, SEGMENT)';


--
-- Name: COLUMN taxonomy_facets.facet_title; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_facets.facet_title IS 'Human-readable dimension type name';


--
-- Name: COLUMN taxonomy_facets.governance_owner; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_facets.governance_owner IS 'Data governance team responsible';


--
-- Name: COLUMN taxonomy_facets.is_mandatory_reporting; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_facets.is_mandatory_reporting IS 'Whether required on executive snapshots';


--
-- Name: taxonomy_nodes; Type: TABLE; Schema: master_ref; Owner: -
--

CREATE TABLE master_ref.taxonomy_nodes (
    node_id character varying(32) NOT NULL,
    facet_code character varying(32) NOT NULL,
    canonical_code character varying(40) NOT NULL,
    canonical_label character varying(120) NOT NULL,
    sort_ordinal integer NOT NULL,
    is_active boolean NOT NULL
);


--
-- Name: TABLE taxonomy_nodes; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON TABLE master_ref.taxonomy_nodes IS 'The standard values for each dimension type.';


--
-- Name: COLUMN taxonomy_nodes.node_id; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_nodes.node_id IS 'Unique canonical dimension value ID';


--
-- Name: COLUMN taxonomy_nodes.facet_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_nodes.facet_code IS 'Dimension type code';


--
-- Name: COLUMN taxonomy_nodes.canonical_code; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_nodes.canonical_code IS 'Standardized reporting code';


--
-- Name: COLUMN taxonomy_nodes.canonical_label; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_nodes.canonical_label IS 'Standardized reporting display label';


--
-- Name: COLUMN taxonomy_nodes.sort_ordinal; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_nodes.sort_ordinal IS 'Presentation sort order';


--
-- Name: COLUMN taxonomy_nodes.is_active; Type: COMMENT; Schema: master_ref; Owner: -
--

COMMENT ON COLUMN master_ref.taxonomy_nodes.is_active IS 'Whether active for current fiscal year';


--
-- Name: deal_history_snapshots; Type: TABLE; Schema: pipeline_crm; Owner: -
--

CREATE TABLE pipeline_crm.deal_history_snapshots (
    snapshot_row_id character varying(32) NOT NULL,
    deal_id character varying(32) NOT NULL,
    as_of_month character varying(16) NOT NULL,
    snapshot_date date NOT NULL,
    stage_at_snapshot character varying(40) NOT NULL,
    forecast_cat_at_snapshot character varying(32) NOT NULL,
    probability_at_snapshot numeric(5,2) NOT NULL,
    arr_at_snapshot_usd numeric(18,2) NOT NULL,
    weighted_arr_at_snapshot_usd numeric(18,2) NOT NULL
);


--
-- Name: TABLE deal_history_snapshots; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON TABLE pipeline_crm.deal_history_snapshots IS 'Dated copies of each deal, used to track how the pipeline changes over time.';


--
-- Name: COLUMN deal_history_snapshots.snapshot_row_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.snapshot_row_id IS 'Unique snapshot row ID';


--
-- Name: COLUMN deal_history_snapshots.deal_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.deal_id IS 'Opportunity ID';


--
-- Name: COLUMN deal_history_snapshots.as_of_month; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.as_of_month IS 'Snapshot month YYYY-MM';


--
-- Name: COLUMN deal_history_snapshots.snapshot_date; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.snapshot_date IS 'Exact snapshot capture date';


--
-- Name: COLUMN deal_history_snapshots.stage_at_snapshot; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.stage_at_snapshot IS 'Stage as of snapshot date';


--
-- Name: COLUMN deal_history_snapshots.forecast_cat_at_snapshot; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.forecast_cat_at_snapshot IS 'Forecast category at snapshot date';


--
-- Name: COLUMN deal_history_snapshots.probability_at_snapshot; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.probability_at_snapshot IS 'Probability % at snapshot date';


--
-- Name: COLUMN deal_history_snapshots.arr_at_snapshot_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.arr_at_snapshot_usd IS 'Unweighted ARR in USD at snapshot date';


--
-- Name: COLUMN deal_history_snapshots.weighted_arr_at_snapshot_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_history_snapshots.weighted_arr_at_snapshot_usd IS 'Weighted ARR in USD at snapshot date';


--
-- Name: deal_opportunities; Type: TABLE; Schema: pipeline_crm; Owner: -
--

CREATE TABLE pipeline_crm.deal_opportunities (
    deal_id character varying(32) NOT NULL,
    crm_account_id character varying(32) NOT NULL,
    owner_rep_id character varying(32) NOT NULL,
    deal_title character varying(180) NOT NULL,
    deal_type character varying(32) NOT NULL,
    pipeline_stage character varying(40) NOT NULL,
    forecast_cat_code character varying(32) NOT NULL,
    win_probability_pct numeric(5,2) NOT NULL,
    deal_tcv_usd numeric(18,2) NOT NULL,
    deal_arr_usd numeric(18,2) NOT NULL,
    weighted_arr_usd numeric(18,2) NOT NULL,
    target_close_date date NOT NULL,
    close_fiscal_period character varying(16) NOT NULL,
    linked_agreement_id character varying(32)
);


--
-- Name: TABLE deal_opportunities; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON TABLE pipeline_crm.deal_opportunities IS 'Current deals, with stage, probability, contract value and close date.';


--
-- Name: COLUMN deal_opportunities.deal_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_id IS 'Unique CRM opportunity ID';


--
-- Name: COLUMN deal_opportunities.crm_account_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.crm_account_id IS 'Customer CRM account';


--
-- Name: COLUMN deal_opportunities.owner_rep_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.owner_rep_id IS 'Sales rep owning the deal';


--
-- Name: COLUMN deal_opportunities.deal_title; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_title IS 'Opportunity name';


--
-- Name: COLUMN deal_opportunities.deal_type; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_type IS 'Deal motion (New_Logo, Expansion, Renewal)';


--
-- Name: COLUMN deal_opportunities.pipeline_stage; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.pipeline_stage IS 'Current stage (Closed_Won, Commit, Best_Case, Negotiation, Qualification, Closed_Lost)';


--
-- Name: COLUMN deal_opportunities.forecast_cat_code; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.forecast_cat_code IS 'Forecast category code';


--
-- Name: COLUMN deal_opportunities.win_probability_pct; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.win_probability_pct IS 'Win probability percentage (0 to 100)';


--
-- Name: COLUMN deal_opportunities.deal_tcv_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_tcv_usd IS 'Total contract value in USD';


--
-- Name: COLUMN deal_opportunities.deal_arr_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.deal_arr_usd IS 'Annual recurring revenue value in USD';


--
-- Name: COLUMN deal_opportunities.weighted_arr_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.weighted_arr_usd IS 'Probability-weighted ARR in USD';


--
-- Name: COLUMN deal_opportunities.target_close_date; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.target_close_date IS 'Expected or actual close date';


--
-- Name: COLUMN deal_opportunities.close_fiscal_period; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.close_fiscal_period IS 'Close period YYYY-MM';


--
-- Name: COLUMN deal_opportunities.linked_agreement_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_opportunities.linked_agreement_id IS 'Executed contract file if Closed Won';


--
-- Name: deal_product_splits; Type: TABLE; Schema: pipeline_crm; Owner: -
--

CREATE TABLE pipeline_crm.deal_product_splits (
    deal_split_id character varying(32) NOT NULL,
    deal_id character varying(32) NOT NULL,
    catalog_item_id character varying(32) NOT NULL,
    crm_product_family character varying(60) NOT NULL,
    split_arr_usd numeric(18,2) NOT NULL,
    split_tcv_usd numeric(18,2) NOT NULL,
    term_months integer NOT NULL
);


--
-- Name: TABLE deal_product_splits; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON TABLE pipeline_crm.deal_product_splits IS 'How each deal''s value splits across product categories.';


--
-- Name: COLUMN deal_product_splits.deal_split_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_product_splits.deal_split_id IS 'Unique opportunity line item ID';


--
-- Name: COLUMN deal_product_splits.deal_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_product_splits.deal_id IS 'Parent opportunity ID';


--
-- Name: COLUMN deal_product_splits.catalog_item_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_product_splits.catalog_item_id IS 'Product catalog offering';


--
-- Name: COLUMN deal_product_splits.crm_product_family; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_product_splits.crm_product_family IS 'CRM product category label';


--
-- Name: COLUMN deal_product_splits.split_arr_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_product_splits.split_arr_usd IS 'Annual recurring value allocated to product line';


--
-- Name: COLUMN deal_product_splits.split_tcv_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_product_splits.split_tcv_usd IS 'Total contract value allocated to product line';


--
-- Name: COLUMN deal_product_splits.term_months; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.deal_product_splits.term_months IS 'Subscription term in months';


--
-- Name: forecast_buckets; Type: TABLE; Schema: pipeline_crm; Owner: -
--

CREATE TABLE pipeline_crm.forecast_buckets (
    forecast_cat_code character varying(32) NOT NULL,
    bucket_label character varying(80) NOT NULL,
    default_probability_pct numeric(5,2) NOT NULL,
    included_in_weighted_pipe boolean NOT NULL
);


--
-- Name: TABLE forecast_buckets; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON TABLE pipeline_crm.forecast_buckets IS 'Forecast categories and the default win probability for each.';


--
-- Name: COLUMN forecast_buckets.forecast_cat_code; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.forecast_buckets.forecast_cat_code IS 'Forecast category code (CLOSED, COMMIT, BEST_CASE, PIPELINE, OMIT)';


--
-- Name: COLUMN forecast_buckets.bucket_label; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.forecast_buckets.bucket_label IS 'Display label';


--
-- Name: COLUMN forecast_buckets.default_probability_pct; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.forecast_buckets.default_probability_pct IS 'Default win weight percentage';


--
-- Name: COLUMN forecast_buckets.included_in_weighted_pipe; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.forecast_buckets.included_in_weighted_pipe IS 'Whether included in standard weighted pipeline';


--
-- Name: prospect_accounts; Type: TABLE; Schema: pipeline_crm; Owner: -
--

CREATE TABLE pipeline_crm.prospect_accounts (
    crm_account_id character varying(32) NOT NULL,
    conglomerate_id character varying(32),
    crm_account_name character varying(160) NOT NULL,
    crm_region_raw character varying(40) NOT NULL,
    crm_industry_raw character varying(60) NOT NULL,
    market_segment character varying(32) NOT NULL,
    account_owner_rep_id character varying(32) NOT NULL,
    is_existing_customer boolean NOT NULL
);


--
-- Name: TABLE prospect_accounts; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON TABLE pipeline_crm.prospect_accounts IS 'Customer accounts in the CRM, with region, industry and segment.';


--
-- Name: COLUMN prospect_accounts.crm_account_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_account_id IS 'Unique CRM account ID';


--
-- Name: COLUMN prospect_accounts.conglomerate_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.conglomerate_id IS 'Parent customer group (nullable for unlinked prospects)';


--
-- Name: COLUMN prospect_accounts.crm_account_name; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_account_name IS 'Account name in CRM';


--
-- Name: COLUMN prospect_accounts.crm_region_raw; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_region_raw IS 'CRM raw territory/region string';


--
-- Name: COLUMN prospect_accounts.crm_industry_raw; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.crm_industry_raw IS 'CRM raw industry classification';


--
-- Name: COLUMN prospect_accounts.market_segment; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.market_segment IS 'Segment (Enterprise, Commercial, Strategic)';


--
-- Name: COLUMN prospect_accounts.account_owner_rep_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.account_owner_rep_id IS 'Assigned account executive';


--
-- Name: COLUMN prospect_accounts.is_existing_customer; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.prospect_accounts.is_existing_customer IS 'Whether account already has active contracts';


--
-- Name: quota_carriers; Type: TABLE; Schema: pipeline_crm; Owner: -
--

CREATE TABLE pipeline_crm.quota_carriers (
    rep_id character varying(32) NOT NULL,
    rep_full_name character varying(120) NOT NULL,
    squad_id character varying(32) NOT NULL,
    manager_rep_id character varying(32),
    role_title character varying(60) NOT NULL,
    annual_quota_usd numeric(18,2) NOT NULL,
    active_status boolean NOT NULL
);


--
-- Name: TABLE quota_carriers; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON TABLE pipeline_crm.quota_carriers IS 'Salespeople, with their team, manager and quota.';


--
-- Name: COLUMN quota_carriers.rep_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.quota_carriers.rep_id IS 'Unique sales representative ID';


--
-- Name: COLUMN quota_carriers.rep_full_name; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.quota_carriers.rep_full_name IS 'Salesperson full name';


--
-- Name: COLUMN quota_carriers.squad_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.quota_carriers.squad_id IS 'Assigned sales team squad';


--
-- Name: COLUMN quota_carriers.manager_rep_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.quota_carriers.manager_rep_id IS 'Reporting manager ID';


--
-- Name: COLUMN quota_carriers.role_title; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.quota_carriers.role_title IS 'Role (VP_Sales, Regional_Director, Enterprise_AE)';


--
-- Name: COLUMN quota_carriers.annual_quota_usd; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.quota_carriers.annual_quota_usd IS 'Annual ARR quota target in USD';


--
-- Name: COLUMN quota_carriers.active_status; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.quota_carriers.active_status IS 'Currently employed/active flag';


--
-- Name: territory_squads; Type: TABLE; Schema: pipeline_crm; Owner: -
--

CREATE TABLE pipeline_crm.territory_squads (
    squad_id character varying(32) NOT NULL,
    parent_squad_id character varying(32),
    squad_name character varying(120) NOT NULL,
    theater_region character varying(32) NOT NULL,
    hierarchy_depth integer NOT NULL
);


--
-- Name: TABLE territory_squads; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON TABLE pipeline_crm.territory_squads IS 'Sales teams, nested under parent teams.';


--
-- Name: COLUMN territory_squads.squad_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.territory_squads.squad_id IS 'Unique sales team ID';


--
-- Name: COLUMN territory_squads.parent_squad_id; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.territory_squads.parent_squad_id IS 'Parent sales team ID in hierarchy';


--
-- Name: COLUMN territory_squads.squad_name; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.territory_squads.squad_name IS 'Sales team name';


--
-- Name: COLUMN territory_squads.theater_region; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.territory_squads.theater_region IS 'Assigned geographic theater';


--
-- Name: COLUMN territory_squads.hierarchy_depth; Type: COMMENT; Schema: pipeline_crm; Owner: -
--

COMMENT ON COLUMN pipeline_crm.territory_squads.hierarchy_depth IS 'Team level (1=Global, 2=Theater, 3=Pod)';


--
-- Name: inquiry_threads; Type: TABLE; Schema: portal_app; Owner: -
--

CREATE TABLE portal_app.inquiry_threads (
    thread_id character varying(32) NOT NULL,
    operator_id character varying(32) NOT NULL,
    tenant_org_id character varying(32) NOT NULL,
    thread_topic character varying(200) NOT NULL,
    started_at timestamp without time zone NOT NULL,
    last_activity_at timestamp without time zone NOT NULL
);


--
-- Name: TABLE inquiry_threads; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON TABLE portal_app.inquiry_threads IS 'A user''s chat threads with the assistant.';


--
-- Name: COLUMN inquiry_threads.thread_id; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.inquiry_threads.thread_id IS 'Unique conversation thread ID';


--
-- Name: COLUMN inquiry_threads.operator_id; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.inquiry_threads.operator_id IS 'Owner user ID';


--
-- Name: COLUMN inquiry_threads.tenant_org_id; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.inquiry_threads.tenant_org_id IS 'Tenant organization boundary';


--
-- Name: COLUMN inquiry_threads.thread_topic; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.inquiry_threads.thread_topic IS 'Summary title of inquiry thread';


--
-- Name: COLUMN inquiry_threads.started_at; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.inquiry_threads.started_at IS 'Thread creation timestamp';


--
-- Name: COLUMN inquiry_threads.last_activity_at; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.inquiry_threads.last_activity_at IS 'Most recent message timestamp';


--
-- Name: platform_operators; Type: TABLE; Schema: portal_app; Owner: -
--

CREATE TABLE portal_app.platform_operators (
    operator_id character varying(32) NOT NULL,
    email_handle character varying(150) NOT NULL,
    display_name character varying(120) NOT NULL,
    security_role character varying(40) NOT NULL,
    tenant_org_id character varying(32) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: TABLE platform_operators; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON TABLE portal_app.platform_operators IS 'People who can log in, with their roles and tenant.';


--
-- Name: COLUMN platform_operators.operator_id; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.platform_operators.operator_id IS 'Unique user operator ID';


--
-- Name: COLUMN platform_operators.email_handle; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.platform_operators.email_handle IS 'Corporate login email';


--
-- Name: COLUMN platform_operators.display_name; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.platform_operators.display_name IS 'Full display name';


--
-- Name: COLUMN platform_operators.security_role; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.platform_operators.security_role IS 'Access role (Finance_Controller, RevOps_Analyst, Executive, Auditor)';


--
-- Name: COLUMN platform_operators.tenant_org_id; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.platform_operators.tenant_org_id IS 'Multi-tenant organization identifier';


--
-- Name: COLUMN platform_operators.is_active; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.platform_operators.is_active IS 'Active account flag';


--
-- Name: COLUMN platform_operators.created_at; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.platform_operators.created_at IS 'Account provisioning timestamp';


--
-- Name: thread_utterances; Type: TABLE; Schema: portal_app; Owner: -
--

CREATE TABLE portal_app.thread_utterances (
    utterance_id character varying(32) NOT NULL,
    thread_id character varying(32) NOT NULL,
    speaker_role character varying(24) NOT NULL,
    message_body text NOT NULL,
    cited_objects_json text NOT NULL,
    prompt_tokens integer NOT NULL,
    completion_tokens integer NOT NULL,
    emitted_at timestamp without time zone NOT NULL
);


--
-- Name: TABLE thread_utterances; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON TABLE portal_app.thread_utterances IS 'Individual messages in a conversation, with citations and token usage.';


--
-- Name: COLUMN thread_utterances.utterance_id; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.utterance_id IS 'Unique message utterance ID';


--
-- Name: COLUMN thread_utterances.thread_id; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.thread_id IS 'Parent conversation thread';


--
-- Name: COLUMN thread_utterances.speaker_role; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.speaker_role IS 'Role (user, assistant, adjudicator)';


--
-- Name: COLUMN thread_utterances.message_body; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.message_body IS 'Message text content';


--
-- Name: COLUMN thread_utterances.cited_objects_json; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.cited_objects_json IS 'JSON array of cited tables, queries, and evidence IDs';


--
-- Name: COLUMN thread_utterances.prompt_tokens; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.prompt_tokens IS 'Input prompt token count';


--
-- Name: COLUMN thread_utterances.completion_tokens; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.completion_tokens IS 'Output completion token count';


--
-- Name: COLUMN thread_utterances.emitted_at; Type: COMMENT; Schema: portal_app; Owner: -
--

COMMENT ON COLUMN portal_app.thread_utterances.emitted_at IS 'Message timestamp';


--
-- Name: agreement_citations agreement_citations_pkey; Type: CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.agreement_citations
    ADD CONSTRAINT agreement_citations_pkey PRIMARY KEY (citation_id);


--
-- Name: agreement_files agreement_files_pkey; Type: CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.agreement_files
    ADD CONSTRAINT agreement_files_pkey PRIMARY KEY (agreement_id);


--
-- Name: counterparty_aliases counterparty_aliases_pkey; Type: CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.counterparty_aliases
    ADD CONSTRAINT counterparty_aliases_pkey PRIMARY KEY (alias_id);


--
-- Name: repository_drives repository_drives_pkey; Type: CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.repository_drives
    ADD CONSTRAINT repository_drives_pkey PRIMARY KEY (drive_id);


--
-- Name: ingestion_batches ingestion_batches_pkey; Type: CONSTRAINT; Schema: data_ops; Owner: -
--

ALTER TABLE ONLY data_ops.ingestion_batches
    ADD CONSTRAINT ingestion_batches_pkey PRIMARY KEY (batch_id);


--
-- Name: horizon_revenue_outlook horizon_revenue_outlook_pkey; Type: CONSTRAINT; Schema: exec_reporting; Owner: -
--

ALTER TABLE ONLY exec_reporting.horizon_revenue_outlook
    ADD CONSTRAINT horizon_revenue_outlook_pkey PRIMARY KEY (outlook_id);


--
-- Name: monthly_revenue_posture monthly_revenue_posture_pkey; Type: CONSTRAINT; Schema: exec_reporting; Owner: -
--

ALTER TABLE ONLY exec_reporting.monthly_revenue_posture
    ADD CONSTRAINT monthly_revenue_posture_pkey PRIMARY KEY (posture_id);


--
-- Name: financial_targets financial_targets_pkey; Type: CONSTRAINT; Schema: fpna_plan; Owner: -
--

ALTER TABLE ONLY fpna_plan.financial_targets
    ADD CONSTRAINT financial_targets_pkey PRIMARY KEY (target_line_id);


--
-- Name: analytic_axes analytic_axes_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.analytic_axes
    ADD CONSTRAINT analytic_axes_pkey PRIMARY KEY (axis_id);


--
-- Name: analytic_axis_members analytic_axis_members_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.analytic_axis_members
    ADD CONSTRAINT analytic_axis_members_pkey PRIMARY KEY (member_id);


--
-- Name: catalog_offerings catalog_offerings_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.catalog_offerings
    ADD CONSTRAINT catalog_offerings_pkey PRIMARY KEY (catalog_item_id);


--
-- Name: chart_of_accounts chart_of_accounts_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.chart_of_accounts
    ADD CONSTRAINT chart_of_accounts_pkey PRIMARY KEY (account_id);


--
-- Name: cost_account_links cost_account_links_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.cost_account_links
    ADD CONSTRAINT cost_account_links_pkey PRIMARY KEY (link_id);


--
-- Name: cost_taxonomy_nodes cost_taxonomy_nodes_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.cost_taxonomy_nodes
    ADD CONSTRAINT cost_taxonomy_nodes_pkey PRIMARY KEY (cost_node_id);


--
-- Name: journal_postings journal_postings_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.journal_postings
    ADD CONSTRAINT journal_postings_pkey PRIMARY KEY (posting_line_id);


--
-- Name: journal_vouchers journal_vouchers_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.journal_vouchers
    ADD CONSTRAINT journal_vouchers_pkey PRIMARY KEY (voucher_id);


--
-- Name: ledger_statement_groups ledger_statement_groups_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.ledger_statement_groups
    ADD CONSTRAINT ledger_statement_groups_pkey PRIMARY KEY (stmt_group_id);


--
-- Name: legacy_coa_crosswalk legacy_coa_crosswalk_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.legacy_coa_crosswalk
    ADD CONSTRAINT legacy_coa_crosswalk_pkey PRIMARY KEY (legacy_map_id);


--
-- Name: receivable_debtors receivable_debtors_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.receivable_debtors
    ADD CONSTRAINT receivable_debtors_pkey PRIMARY KEY (debtor_id);


--
-- Name: receivable_ledger_events receivable_ledger_events_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.receivable_ledger_events
    ADD CONSTRAINT receivable_ledger_events_pkey PRIMARY KEY (ar_event_id);


--
-- Name: sales_invoice_items sales_invoice_items_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoice_items
    ADD CONSTRAINT sales_invoice_items_pkey PRIMARY KEY (invoice_item_id);


--
-- Name: sales_invoices sales_invoices_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoices
    ADD CONSTRAINT sales_invoices_pkey PRIMARY KEY (invoice_id);


--
-- Name: tag_bundle_members tag_bundle_members_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.tag_bundle_members
    ADD CONSTRAINT tag_bundle_members_pkey PRIMARY KEY (bundle_member_id);


--
-- Name: tag_bundles tag_bundles_pkey; Type: CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.tag_bundles
    ADD CONSTRAINT tag_bundles_pkey PRIMARY KEY (dim_bundle_id);


--
-- Name: client_conglomerates client_conglomerates_pkey; Type: CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.client_conglomerates
    ADD CONSTRAINT client_conglomerates_pkey PRIMARY KEY (conglomerate_id);


--
-- Name: entity_jurisdictions entity_jurisdictions_pkey; Type: CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.entity_jurisdictions
    ADD CONSTRAINT entity_jurisdictions_pkey PRIMARY KEY (legal_entity_code);


--
-- Name: taxonomy_crosswalks taxonomy_crosswalks_pkey; Type: CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.taxonomy_crosswalks
    ADD CONSTRAINT taxonomy_crosswalks_pkey PRIMARY KEY (crosswalk_id);


--
-- Name: taxonomy_facets taxonomy_facets_pkey; Type: CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.taxonomy_facets
    ADD CONSTRAINT taxonomy_facets_pkey PRIMARY KEY (facet_code);


--
-- Name: taxonomy_nodes taxonomy_nodes_pkey; Type: CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.taxonomy_nodes
    ADD CONSTRAINT taxonomy_nodes_pkey PRIMARY KEY (node_id);


--
-- Name: deal_history_snapshots deal_history_snapshots_pkey; Type: CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_history_snapshots
    ADD CONSTRAINT deal_history_snapshots_pkey PRIMARY KEY (snapshot_row_id);


--
-- Name: deal_opportunities deal_opportunities_pkey; Type: CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_opportunities
    ADD CONSTRAINT deal_opportunities_pkey PRIMARY KEY (deal_id);


--
-- Name: deal_product_splits deal_product_splits_pkey; Type: CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_product_splits
    ADD CONSTRAINT deal_product_splits_pkey PRIMARY KEY (deal_split_id);


--
-- Name: forecast_buckets forecast_buckets_pkey; Type: CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.forecast_buckets
    ADD CONSTRAINT forecast_buckets_pkey PRIMARY KEY (forecast_cat_code);


--
-- Name: prospect_accounts prospect_accounts_pkey; Type: CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.prospect_accounts
    ADD CONSTRAINT prospect_accounts_pkey PRIMARY KEY (crm_account_id);


--
-- Name: quota_carriers quota_carriers_pkey; Type: CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.quota_carriers
    ADD CONSTRAINT quota_carriers_pkey PRIMARY KEY (rep_id);


--
-- Name: territory_squads territory_squads_pkey; Type: CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.territory_squads
    ADD CONSTRAINT territory_squads_pkey PRIMARY KEY (squad_id);


--
-- Name: inquiry_threads inquiry_threads_pkey; Type: CONSTRAINT; Schema: portal_app; Owner: -
--

ALTER TABLE ONLY portal_app.inquiry_threads
    ADD CONSTRAINT inquiry_threads_pkey PRIMARY KEY (thread_id);


--
-- Name: platform_operators platform_operators_pkey; Type: CONSTRAINT; Schema: portal_app; Owner: -
--

ALTER TABLE ONLY portal_app.platform_operators
    ADD CONSTRAINT platform_operators_pkey PRIMARY KEY (operator_id);


--
-- Name: thread_utterances thread_utterances_pkey; Type: CONSTRAINT; Schema: portal_app; Owner: -
--

ALTER TABLE ONLY portal_app.thread_utterances
    ADD CONSTRAINT thread_utterances_pkey PRIMARY KEY (utterance_id);


--
-- Name: idx_agreement_citations_agreement_id; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_agreement_citations_agreement_id ON agreements.agreement_citations USING btree (agreement_id);


--
-- Name: idx_agreement_citations_ref_token; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_agreement_citations_ref_token ON agreements.agreement_citations USING btree (ref_token);


--
-- Name: idx_agreement_files_agreement_type; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_agreement_files_agreement_type ON agreements.agreement_files USING btree (agreement_type);


--
-- Name: idx_agreement_files_execution_status; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_agreement_files_execution_status ON agreements.agreement_files USING btree (execution_status);


--
-- Name: idx_agreement_files_matched_group_id; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_agreement_files_matched_group_id ON agreements.agreement_files USING btree (matched_group_id);


--
-- Name: idx_counterparty_aliases_alias_name; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_counterparty_aliases_alias_name ON agreements.counterparty_aliases USING btree (alias_name);


--
-- Name: idx_counterparty_aliases_conglomerate_id; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_counterparty_aliases_conglomerate_id ON agreements.counterparty_aliases USING btree (conglomerate_id);


--
-- Name: idx_repository_drives_owning_region_code; Type: INDEX; Schema: agreements; Owner: -
--

CREATE INDEX idx_repository_drives_owning_region_code ON agreements.repository_drives USING btree (owning_region_code);


--
-- Name: idx_ingestion_batches_run_status; Type: INDEX; Schema: data_ops; Owner: -
--

CREATE INDEX idx_ingestion_batches_run_status ON data_ops.ingestion_batches USING btree (run_status);


--
-- Name: idx_ingestion_batches_upstream_connector; Type: INDEX; Schema: data_ops; Owner: -
--

CREATE INDEX idx_ingestion_batches_upstream_connector ON data_ops.ingestion_batches USING btree (upstream_connector);


--
-- Name: idx_horizon_revenue_outlook_as_of_period; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_horizon_revenue_outlook_as_of_period ON exec_reporting.horizon_revenue_outlook USING btree (as_of_period);


--
-- Name: idx_horizon_revenue_outlook_forward_fiscal_year; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_horizon_revenue_outlook_forward_fiscal_year ON exec_reporting.horizon_revenue_outlook USING btree (forward_fiscal_year);


--
-- Name: idx_horizon_revenue_outlook_product_line_node_id; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_horizon_revenue_outlook_product_line_node_id ON exec_reporting.horizon_revenue_outlook USING btree (product_line_node_id);


--
-- Name: idx_horizon_revenue_outlook_region_node_id; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_horizon_revenue_outlook_region_node_id ON exec_reporting.horizon_revenue_outlook USING btree (region_node_id);


--
-- Name: idx_monthly_revenue_posture_industry_node_id; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_monthly_revenue_posture_industry_node_id ON exec_reporting.monthly_revenue_posture USING btree (industry_node_id);


--
-- Name: idx_monthly_revenue_posture_product_line_node_id; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_monthly_revenue_posture_product_line_node_id ON exec_reporting.monthly_revenue_posture USING btree (product_line_node_id);


--
-- Name: idx_monthly_revenue_posture_region_node_id; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_monthly_revenue_posture_region_node_id ON exec_reporting.monthly_revenue_posture USING btree (region_node_id);


--
-- Name: idx_monthly_revenue_posture_snapshot_period; Type: INDEX; Schema: exec_reporting; Owner: -
--

CREATE INDEX idx_monthly_revenue_posture_snapshot_period ON exec_reporting.monthly_revenue_posture USING btree (snapshot_period);


--
-- Name: idx_financial_targets_fiscal_period; Type: INDEX; Schema: fpna_plan; Owner: -
--

CREATE INDEX idx_financial_targets_fiscal_period ON fpna_plan.financial_targets USING btree (fiscal_period);


--
-- Name: idx_financial_targets_metric_category; Type: INDEX; Schema: fpna_plan; Owner: -
--

CREATE INDEX idx_financial_targets_metric_category ON fpna_plan.financial_targets USING btree (metric_category);


--
-- Name: idx_financial_targets_plan_scenario; Type: INDEX; Schema: fpna_plan; Owner: -
--

CREATE INDEX idx_financial_targets_plan_scenario ON fpna_plan.financial_targets USING btree (plan_scenario);


--
-- Name: idx_financial_targets_product_line_node_id; Type: INDEX; Schema: fpna_plan; Owner: -
--

CREATE INDEX idx_financial_targets_product_line_node_id ON fpna_plan.financial_targets USING btree (product_line_node_id);


--
-- Name: idx_financial_targets_region_node_id; Type: INDEX; Schema: fpna_plan; Owner: -
--

CREATE INDEX idx_financial_targets_region_node_id ON fpna_plan.financial_targets USING btree (region_node_id);


--
-- Name: idx_analytic_axis_members_axis_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_analytic_axis_members_axis_id ON ledger_erp.analytic_axis_members USING btree (axis_id);


--
-- Name: idx_analytic_axis_members_member_code; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_analytic_axis_members_member_code ON ledger_erp.analytic_axis_members USING btree (member_code);


--
-- Name: idx_catalog_offerings_product_line_raw; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_catalog_offerings_product_line_raw ON ledger_erp.catalog_offerings USING btree (product_line_raw);


--
-- Name: idx_catalog_offerings_revenue_stream_type; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_catalog_offerings_revenue_stream_type ON ledger_erp.catalog_offerings USING btree (revenue_stream_type);


--
-- Name: idx_chart_of_accounts_account_class; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_chart_of_accounts_account_class ON ledger_erp.chart_of_accounts USING btree (account_class);


--
-- Name: idx_chart_of_accounts_account_number; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_chart_of_accounts_account_number ON ledger_erp.chart_of_accounts USING btree (account_number);


--
-- Name: idx_chart_of_accounts_stmt_group_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_chart_of_accounts_stmt_group_id ON ledger_erp.chart_of_accounts USING btree (stmt_group_id);


--
-- Name: idx_cost_account_links_account_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_cost_account_links_account_id ON ledger_erp.cost_account_links USING btree (account_id);


--
-- Name: idx_cost_account_links_cost_node_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_cost_account_links_cost_node_id ON ledger_erp.cost_account_links USING btree (cost_node_id);


--
-- Name: idx_cost_taxonomy_nodes_cost_category_code; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_cost_taxonomy_nodes_cost_category_code ON ledger_erp.cost_taxonomy_nodes USING btree (cost_category_code);


--
-- Name: idx_cost_taxonomy_nodes_parent_cost_node_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_cost_taxonomy_nodes_parent_cost_node_id ON ledger_erp.cost_taxonomy_nodes USING btree (parent_cost_node_id);


--
-- Name: idx_journal_postings_account_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_journal_postings_account_id ON ledger_erp.journal_postings USING btree (account_id);


--
-- Name: idx_journal_postings_dim_bundle_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_journal_postings_dim_bundle_id ON ledger_erp.journal_postings USING btree (dim_bundle_id);


--
-- Name: idx_journal_postings_voucher_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_journal_postings_voucher_id ON ledger_erp.journal_postings USING btree (voucher_id);


--
-- Name: idx_journal_vouchers_fiscal_period; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_journal_vouchers_fiscal_period ON ledger_erp.journal_vouchers USING btree (fiscal_period);


--
-- Name: idx_journal_vouchers_journal_source; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_journal_vouchers_journal_source ON ledger_erp.journal_vouchers USING btree (journal_source);


--
-- Name: idx_journal_vouchers_posting_status; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_journal_vouchers_posting_status ON ledger_erp.journal_vouchers USING btree (posting_status);


--
-- Name: idx_legacy_coa_crosswalk_legacy_acct_code; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_legacy_coa_crosswalk_legacy_acct_code ON ledger_erp.legacy_coa_crosswalk USING btree (legacy_acct_code);


--
-- Name: idx_legacy_coa_crosswalk_modern_account_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_legacy_coa_crosswalk_modern_account_id ON ledger_erp.legacy_coa_crosswalk USING btree (modern_account_id);


--
-- Name: idx_receivable_debtors_conglomerate_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_receivable_debtors_conglomerate_id ON ledger_erp.receivable_debtors USING btree (conglomerate_id);


--
-- Name: idx_receivable_debtors_legal_entity_code; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_receivable_debtors_legal_entity_code ON ledger_erp.receivable_debtors USING btree (legal_entity_code);


--
-- Name: idx_receivable_ledger_events_applied_invoice_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_receivable_ledger_events_applied_invoice_id ON ledger_erp.receivable_ledger_events USING btree (applied_invoice_id);


--
-- Name: idx_receivable_ledger_events_debtor_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_receivable_ledger_events_debtor_id ON ledger_erp.receivable_ledger_events USING btree (debtor_id);


--
-- Name: idx_receivable_ledger_events_event_type; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_receivable_ledger_events_event_type ON ledger_erp.receivable_ledger_events USING btree (event_type);


--
-- Name: idx_receivable_ledger_events_fiscal_period; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_receivable_ledger_events_fiscal_period ON ledger_erp.receivable_ledger_events USING btree (fiscal_period);


--
-- Name: idx_sales_invoice_items_catalog_item_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_sales_invoice_items_catalog_item_id ON ledger_erp.sales_invoice_items USING btree (catalog_item_id);


--
-- Name: idx_sales_invoice_items_dim_bundle_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_sales_invoice_items_dim_bundle_id ON ledger_erp.sales_invoice_items USING btree (dim_bundle_id);


--
-- Name: idx_sales_invoice_items_invoice_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_sales_invoice_items_invoice_id ON ledger_erp.sales_invoice_items USING btree (invoice_id);


--
-- Name: idx_sales_invoices_agreement_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_sales_invoices_agreement_id ON ledger_erp.sales_invoices USING btree (agreement_id);


--
-- Name: idx_sales_invoices_debtor_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_sales_invoices_debtor_id ON ledger_erp.sales_invoices USING btree (debtor_id);


--
-- Name: idx_sales_invoices_fiscal_period; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_sales_invoices_fiscal_period ON ledger_erp.sales_invoices USING btree (fiscal_period);


--
-- Name: idx_sales_invoices_settlement_status; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_sales_invoices_settlement_status ON ledger_erp.sales_invoices USING btree (settlement_status);


--
-- Name: idx_tag_bundle_members_dim_bundle_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_tag_bundle_members_dim_bundle_id ON ledger_erp.tag_bundle_members USING btree (dim_bundle_id);


--
-- Name: idx_tag_bundle_members_member_id; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_tag_bundle_members_member_id ON ledger_erp.tag_bundle_members USING btree (member_id);


--
-- Name: idx_tag_bundles_dept_code; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_tag_bundles_dept_code ON ledger_erp.tag_bundles USING btree (dept_code);


--
-- Name: idx_tag_bundles_geo_unit_code; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_tag_bundles_geo_unit_code ON ledger_erp.tag_bundles USING btree (geo_unit_code);


--
-- Name: idx_tag_bundles_product_line_code; Type: INDEX; Schema: ledger_erp; Owner: -
--

CREATE INDEX idx_tag_bundles_product_line_code ON ledger_erp.tag_bundles USING btree (product_line_code);


--
-- Name: idx_client_conglomerates_primary_industry_code; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_client_conglomerates_primary_industry_code ON master_ref.client_conglomerates USING btree (primary_industry_code);


--
-- Name: idx_client_conglomerates_primary_region_code; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_client_conglomerates_primary_region_code ON master_ref.client_conglomerates USING btree (primary_region_code);


--
-- Name: idx_entity_jurisdictions_region_code; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_entity_jurisdictions_region_code ON master_ref.entity_jurisdictions USING btree (region_code);


--
-- Name: idx_taxonomy_crosswalks_facet_code; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_taxonomy_crosswalks_facet_code ON master_ref.taxonomy_crosswalks USING btree (facet_code);


--
-- Name: idx_taxonomy_crosswalks_raw_source_value; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_taxonomy_crosswalks_raw_source_value ON master_ref.taxonomy_crosswalks USING btree (raw_source_value);


--
-- Name: idx_taxonomy_crosswalks_source_system_code; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_taxonomy_crosswalks_source_system_code ON master_ref.taxonomy_crosswalks USING btree (source_system_code);


--
-- Name: idx_taxonomy_nodes_canonical_code; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_taxonomy_nodes_canonical_code ON master_ref.taxonomy_nodes USING btree (canonical_code);


--
-- Name: idx_taxonomy_nodes_facet_code; Type: INDEX; Schema: master_ref; Owner: -
--

CREATE INDEX idx_taxonomy_nodes_facet_code ON master_ref.taxonomy_nodes USING btree (facet_code);


--
-- Name: idx_deal_history_snapshots_as_of_month; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_history_snapshots_as_of_month ON pipeline_crm.deal_history_snapshots USING btree (as_of_month);


--
-- Name: idx_deal_history_snapshots_deal_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_history_snapshots_deal_id ON pipeline_crm.deal_history_snapshots USING btree (deal_id);


--
-- Name: idx_deal_opportunities_close_fiscal_period; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_opportunities_close_fiscal_period ON pipeline_crm.deal_opportunities USING btree (close_fiscal_period);


--
-- Name: idx_deal_opportunities_crm_account_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_opportunities_crm_account_id ON pipeline_crm.deal_opportunities USING btree (crm_account_id);


--
-- Name: idx_deal_opportunities_forecast_cat_code; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_opportunities_forecast_cat_code ON pipeline_crm.deal_opportunities USING btree (forecast_cat_code);


--
-- Name: idx_deal_opportunities_pipeline_stage; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_opportunities_pipeline_stage ON pipeline_crm.deal_opportunities USING btree (pipeline_stage);


--
-- Name: idx_deal_product_splits_catalog_item_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_product_splits_catalog_item_id ON pipeline_crm.deal_product_splits USING btree (catalog_item_id);


--
-- Name: idx_deal_product_splits_crm_product_family; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_product_splits_crm_product_family ON pipeline_crm.deal_product_splits USING btree (crm_product_family);


--
-- Name: idx_deal_product_splits_deal_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_deal_product_splits_deal_id ON pipeline_crm.deal_product_splits USING btree (deal_id);


--
-- Name: idx_prospect_accounts_conglomerate_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_prospect_accounts_conglomerate_id ON pipeline_crm.prospect_accounts USING btree (conglomerate_id);


--
-- Name: idx_prospect_accounts_crm_industry_raw; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_prospect_accounts_crm_industry_raw ON pipeline_crm.prospect_accounts USING btree (crm_industry_raw);


--
-- Name: idx_prospect_accounts_crm_region_raw; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_prospect_accounts_crm_region_raw ON pipeline_crm.prospect_accounts USING btree (crm_region_raw);


--
-- Name: idx_quota_carriers_manager_rep_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_quota_carriers_manager_rep_id ON pipeline_crm.quota_carriers USING btree (manager_rep_id);


--
-- Name: idx_quota_carriers_squad_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_quota_carriers_squad_id ON pipeline_crm.quota_carriers USING btree (squad_id);


--
-- Name: idx_territory_squads_parent_squad_id; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_territory_squads_parent_squad_id ON pipeline_crm.territory_squads USING btree (parent_squad_id);


--
-- Name: idx_territory_squads_theater_region; Type: INDEX; Schema: pipeline_crm; Owner: -
--

CREATE INDEX idx_territory_squads_theater_region ON pipeline_crm.territory_squads USING btree (theater_region);


--
-- Name: idx_inquiry_threads_operator_id; Type: INDEX; Schema: portal_app; Owner: -
--

CREATE INDEX idx_inquiry_threads_operator_id ON portal_app.inquiry_threads USING btree (operator_id);


--
-- Name: idx_inquiry_threads_tenant_org_id; Type: INDEX; Schema: portal_app; Owner: -
--

CREATE INDEX idx_inquiry_threads_tenant_org_id ON portal_app.inquiry_threads USING btree (tenant_org_id);


--
-- Name: idx_platform_operators_security_role; Type: INDEX; Schema: portal_app; Owner: -
--

CREATE INDEX idx_platform_operators_security_role ON portal_app.platform_operators USING btree (security_role);


--
-- Name: idx_platform_operators_tenant_org_id; Type: INDEX; Schema: portal_app; Owner: -
--

CREATE INDEX idx_platform_operators_tenant_org_id ON portal_app.platform_operators USING btree (tenant_org_id);


--
-- Name: idx_thread_utterances_thread_id; Type: INDEX; Schema: portal_app; Owner: -
--

CREATE INDEX idx_thread_utterances_thread_id ON portal_app.thread_utterances USING btree (thread_id);


--
-- Name: agreement_citations fk_agreement_citations_agreement_id; Type: FK CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.agreement_citations
    ADD CONSTRAINT fk_agreement_citations_agreement_id FOREIGN KEY (agreement_id) REFERENCES agreements.agreement_files(agreement_id);


--
-- Name: agreement_files fk_agreement_files_drive_id; Type: FK CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.agreement_files
    ADD CONSTRAINT fk_agreement_files_drive_id FOREIGN KEY (drive_id) REFERENCES agreements.repository_drives(drive_id);


--
-- Name: agreement_files fk_agreement_files_matched_group_id; Type: FK CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.agreement_files
    ADD CONSTRAINT fk_agreement_files_matched_group_id FOREIGN KEY (matched_group_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);


--
-- Name: counterparty_aliases fk_counterparty_aliases_conglomerate_id; Type: FK CONSTRAINT; Schema: agreements; Owner: -
--

ALTER TABLE ONLY agreements.counterparty_aliases
    ADD CONSTRAINT fk_counterparty_aliases_conglomerate_id FOREIGN KEY (conglomerate_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);


--
-- Name: horizon_revenue_outlook fk_horizon_revenue_outlook_product_line_node_id; Type: FK CONSTRAINT; Schema: exec_reporting; Owner: -
--

ALTER TABLE ONLY exec_reporting.horizon_revenue_outlook
    ADD CONSTRAINT fk_horizon_revenue_outlook_product_line_node_id FOREIGN KEY (product_line_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: horizon_revenue_outlook fk_horizon_revenue_outlook_region_node_id; Type: FK CONSTRAINT; Schema: exec_reporting; Owner: -
--

ALTER TABLE ONLY exec_reporting.horizon_revenue_outlook
    ADD CONSTRAINT fk_horizon_revenue_outlook_region_node_id FOREIGN KEY (region_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: monthly_revenue_posture fk_monthly_revenue_posture_industry_node_id; Type: FK CONSTRAINT; Schema: exec_reporting; Owner: -
--

ALTER TABLE ONLY exec_reporting.monthly_revenue_posture
    ADD CONSTRAINT fk_monthly_revenue_posture_industry_node_id FOREIGN KEY (industry_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: monthly_revenue_posture fk_monthly_revenue_posture_product_line_node_id; Type: FK CONSTRAINT; Schema: exec_reporting; Owner: -
--

ALTER TABLE ONLY exec_reporting.monthly_revenue_posture
    ADD CONSTRAINT fk_monthly_revenue_posture_product_line_node_id FOREIGN KEY (product_line_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: monthly_revenue_posture fk_monthly_revenue_posture_region_node_id; Type: FK CONSTRAINT; Schema: exec_reporting; Owner: -
--

ALTER TABLE ONLY exec_reporting.monthly_revenue_posture
    ADD CONSTRAINT fk_monthly_revenue_posture_region_node_id FOREIGN KEY (region_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: financial_targets fk_financial_targets_cost_node_id; Type: FK CONSTRAINT; Schema: fpna_plan; Owner: -
--

ALTER TABLE ONLY fpna_plan.financial_targets
    ADD CONSTRAINT fk_financial_targets_cost_node_id FOREIGN KEY (cost_node_id) REFERENCES ledger_erp.cost_taxonomy_nodes(cost_node_id);


--
-- Name: financial_targets fk_financial_targets_industry_node_id; Type: FK CONSTRAINT; Schema: fpna_plan; Owner: -
--

ALTER TABLE ONLY fpna_plan.financial_targets
    ADD CONSTRAINT fk_financial_targets_industry_node_id FOREIGN KEY (industry_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: financial_targets fk_financial_targets_product_line_node_id; Type: FK CONSTRAINT; Schema: fpna_plan; Owner: -
--

ALTER TABLE ONLY fpna_plan.financial_targets
    ADD CONSTRAINT fk_financial_targets_product_line_node_id FOREIGN KEY (product_line_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: financial_targets fk_financial_targets_region_node_id; Type: FK CONSTRAINT; Schema: fpna_plan; Owner: -
--

ALTER TABLE ONLY fpna_plan.financial_targets
    ADD CONSTRAINT fk_financial_targets_region_node_id FOREIGN KEY (region_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: analytic_axis_members fk_analytic_axis_members_axis_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.analytic_axis_members
    ADD CONSTRAINT fk_analytic_axis_members_axis_id FOREIGN KEY (axis_id) REFERENCES ledger_erp.analytic_axes(axis_id);


--
-- Name: catalog_offerings fk_catalog_offerings_default_rev_account_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.catalog_offerings
    ADD CONSTRAINT fk_catalog_offerings_default_rev_account_id FOREIGN KEY (default_rev_account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);


--
-- Name: chart_of_accounts fk_chart_of_accounts_stmt_group_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.chart_of_accounts
    ADD CONSTRAINT fk_chart_of_accounts_stmt_group_id FOREIGN KEY (stmt_group_id) REFERENCES ledger_erp.ledger_statement_groups(stmt_group_id);


--
-- Name: cost_account_links fk_cost_account_links_account_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.cost_account_links
    ADD CONSTRAINT fk_cost_account_links_account_id FOREIGN KEY (account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);


--
-- Name: cost_account_links fk_cost_account_links_cost_node_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.cost_account_links
    ADD CONSTRAINT fk_cost_account_links_cost_node_id FOREIGN KEY (cost_node_id) REFERENCES ledger_erp.cost_taxonomy_nodes(cost_node_id);


--
-- Name: journal_postings fk_journal_postings_account_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.journal_postings
    ADD CONSTRAINT fk_journal_postings_account_id FOREIGN KEY (account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);


--
-- Name: journal_postings fk_journal_postings_dim_bundle_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.journal_postings
    ADD CONSTRAINT fk_journal_postings_dim_bundle_id FOREIGN KEY (dim_bundle_id) REFERENCES ledger_erp.tag_bundles(dim_bundle_id);


--
-- Name: journal_postings fk_journal_postings_voucher_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.journal_postings
    ADD CONSTRAINT fk_journal_postings_voucher_id FOREIGN KEY (voucher_id) REFERENCES ledger_erp.journal_vouchers(voucher_id);


--
-- Name: journal_vouchers fk_journal_vouchers_legal_entity_code; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.journal_vouchers
    ADD CONSTRAINT fk_journal_vouchers_legal_entity_code FOREIGN KEY (legal_entity_code) REFERENCES master_ref.entity_jurisdictions(legal_entity_code);


--
-- Name: legacy_coa_crosswalk fk_legacy_coa_crosswalk_modern_account_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.legacy_coa_crosswalk
    ADD CONSTRAINT fk_legacy_coa_crosswalk_modern_account_id FOREIGN KEY (modern_account_id) REFERENCES ledger_erp.chart_of_accounts(account_id);


--
-- Name: receivable_debtors fk_receivable_debtors_conglomerate_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.receivable_debtors
    ADD CONSTRAINT fk_receivable_debtors_conglomerate_id FOREIGN KEY (conglomerate_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);


--
-- Name: receivable_debtors fk_receivable_debtors_legal_entity_code; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.receivable_debtors
    ADD CONSTRAINT fk_receivable_debtors_legal_entity_code FOREIGN KEY (legal_entity_code) REFERENCES master_ref.entity_jurisdictions(legal_entity_code);


--
-- Name: receivable_ledger_events fk_receivable_ledger_events_applied_invoice_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.receivable_ledger_events
    ADD CONSTRAINT fk_receivable_ledger_events_applied_invoice_id FOREIGN KEY (applied_invoice_id) REFERENCES ledger_erp.sales_invoices(invoice_id);


--
-- Name: receivable_ledger_events fk_receivable_ledger_events_debtor_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.receivable_ledger_events
    ADD CONSTRAINT fk_receivable_ledger_events_debtor_id FOREIGN KEY (debtor_id) REFERENCES ledger_erp.receivable_debtors(debtor_id);


--
-- Name: receivable_ledger_events fk_receivable_ledger_events_gl_voucher_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.receivable_ledger_events
    ADD CONSTRAINT fk_receivable_ledger_events_gl_voucher_id FOREIGN KEY (gl_voucher_id) REFERENCES ledger_erp.journal_vouchers(voucher_id);


--
-- Name: sales_invoice_items fk_sales_invoice_items_catalog_item_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoice_items
    ADD CONSTRAINT fk_sales_invoice_items_catalog_item_id FOREIGN KEY (catalog_item_id) REFERENCES ledger_erp.catalog_offerings(catalog_item_id);


--
-- Name: sales_invoice_items fk_sales_invoice_items_dim_bundle_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoice_items
    ADD CONSTRAINT fk_sales_invoice_items_dim_bundle_id FOREIGN KEY (dim_bundle_id) REFERENCES ledger_erp.tag_bundles(dim_bundle_id);


--
-- Name: sales_invoice_items fk_sales_invoice_items_invoice_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoice_items
    ADD CONSTRAINT fk_sales_invoice_items_invoice_id FOREIGN KEY (invoice_id) REFERENCES ledger_erp.sales_invoices(invoice_id);


--
-- Name: sales_invoices fk_sales_invoices_agreement_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoices
    ADD CONSTRAINT fk_sales_invoices_agreement_id FOREIGN KEY (agreement_id) REFERENCES agreements.agreement_files(agreement_id);


--
-- Name: sales_invoices fk_sales_invoices_debtor_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoices
    ADD CONSTRAINT fk_sales_invoices_debtor_id FOREIGN KEY (debtor_id) REFERENCES ledger_erp.receivable_debtors(debtor_id);


--
-- Name: sales_invoices fk_sales_invoices_legal_entity_code; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.sales_invoices
    ADD CONSTRAINT fk_sales_invoices_legal_entity_code FOREIGN KEY (legal_entity_code) REFERENCES master_ref.entity_jurisdictions(legal_entity_code);


--
-- Name: tag_bundle_members fk_tag_bundle_members_axis_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.tag_bundle_members
    ADD CONSTRAINT fk_tag_bundle_members_axis_id FOREIGN KEY (axis_id) REFERENCES ledger_erp.analytic_axes(axis_id);


--
-- Name: tag_bundle_members fk_tag_bundle_members_dim_bundle_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.tag_bundle_members
    ADD CONSTRAINT fk_tag_bundle_members_dim_bundle_id FOREIGN KEY (dim_bundle_id) REFERENCES ledger_erp.tag_bundles(dim_bundle_id);


--
-- Name: tag_bundle_members fk_tag_bundle_members_member_id; Type: FK CONSTRAINT; Schema: ledger_erp; Owner: -
--

ALTER TABLE ONLY ledger_erp.tag_bundle_members
    ADD CONSTRAINT fk_tag_bundle_members_member_id FOREIGN KEY (member_id) REFERENCES ledger_erp.analytic_axis_members(member_id);


--
-- Name: taxonomy_crosswalks fk_taxonomy_crosswalks_facet_code; Type: FK CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.taxonomy_crosswalks
    ADD CONSTRAINT fk_taxonomy_crosswalks_facet_code FOREIGN KEY (facet_code) REFERENCES master_ref.taxonomy_facets(facet_code);


--
-- Name: taxonomy_crosswalks fk_taxonomy_crosswalks_target_node_id; Type: FK CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.taxonomy_crosswalks
    ADD CONSTRAINT fk_taxonomy_crosswalks_target_node_id FOREIGN KEY (target_node_id) REFERENCES master_ref.taxonomy_nodes(node_id);


--
-- Name: taxonomy_nodes fk_taxonomy_nodes_facet_code; Type: FK CONSTRAINT; Schema: master_ref; Owner: -
--

ALTER TABLE ONLY master_ref.taxonomy_nodes
    ADD CONSTRAINT fk_taxonomy_nodes_facet_code FOREIGN KEY (facet_code) REFERENCES master_ref.taxonomy_facets(facet_code);


--
-- Name: deal_history_snapshots fk_deal_history_snapshots_deal_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_history_snapshots
    ADD CONSTRAINT fk_deal_history_snapshots_deal_id FOREIGN KEY (deal_id) REFERENCES pipeline_crm.deal_opportunities(deal_id);


--
-- Name: deal_opportunities fk_deal_opportunities_crm_account_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_opportunities
    ADD CONSTRAINT fk_deal_opportunities_crm_account_id FOREIGN KEY (crm_account_id) REFERENCES pipeline_crm.prospect_accounts(crm_account_id);


--
-- Name: deal_opportunities fk_deal_opportunities_forecast_cat_code; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_opportunities
    ADD CONSTRAINT fk_deal_opportunities_forecast_cat_code FOREIGN KEY (forecast_cat_code) REFERENCES pipeline_crm.forecast_buckets(forecast_cat_code);


--
-- Name: deal_opportunities fk_deal_opportunities_linked_agreement_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_opportunities
    ADD CONSTRAINT fk_deal_opportunities_linked_agreement_id FOREIGN KEY (linked_agreement_id) REFERENCES agreements.agreement_files(agreement_id);


--
-- Name: deal_opportunities fk_deal_opportunities_owner_rep_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_opportunities
    ADD CONSTRAINT fk_deal_opportunities_owner_rep_id FOREIGN KEY (owner_rep_id) REFERENCES pipeline_crm.quota_carriers(rep_id);


--
-- Name: deal_product_splits fk_deal_product_splits_catalog_item_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_product_splits
    ADD CONSTRAINT fk_deal_product_splits_catalog_item_id FOREIGN KEY (catalog_item_id) REFERENCES ledger_erp.catalog_offerings(catalog_item_id);


--
-- Name: deal_product_splits fk_deal_product_splits_deal_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.deal_product_splits
    ADD CONSTRAINT fk_deal_product_splits_deal_id FOREIGN KEY (deal_id) REFERENCES pipeline_crm.deal_opportunities(deal_id);


--
-- Name: prospect_accounts fk_prospect_accounts_account_owner_rep_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.prospect_accounts
    ADD CONSTRAINT fk_prospect_accounts_account_owner_rep_id FOREIGN KEY (account_owner_rep_id) REFERENCES pipeline_crm.quota_carriers(rep_id);


--
-- Name: prospect_accounts fk_prospect_accounts_conglomerate_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.prospect_accounts
    ADD CONSTRAINT fk_prospect_accounts_conglomerate_id FOREIGN KEY (conglomerate_id) REFERENCES master_ref.client_conglomerates(conglomerate_id);


--
-- Name: quota_carriers fk_quota_carriers_squad_id; Type: FK CONSTRAINT; Schema: pipeline_crm; Owner: -
--

ALTER TABLE ONLY pipeline_crm.quota_carriers
    ADD CONSTRAINT fk_quota_carriers_squad_id FOREIGN KEY (squad_id) REFERENCES pipeline_crm.territory_squads(squad_id);


--
-- Name: inquiry_threads fk_inquiry_threads_operator_id; Type: FK CONSTRAINT; Schema: portal_app; Owner: -
--

ALTER TABLE ONLY portal_app.inquiry_threads
    ADD CONSTRAINT fk_inquiry_threads_operator_id FOREIGN KEY (operator_id) REFERENCES portal_app.platform_operators(operator_id);


--
-- Name: thread_utterances fk_thread_utterances_thread_id; Type: FK CONSTRAINT; Schema: portal_app; Owner: -
--

ALTER TABLE ONLY portal_app.thread_utterances
    ADD CONSTRAINT fk_thread_utterances_thread_id FOREIGN KEY (thread_id) REFERENCES portal_app.inquiry_threads(thread_id);


--
-- PostgreSQL database dump complete
--

