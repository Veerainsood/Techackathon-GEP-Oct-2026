"""Typed input/output schemas and data contracts for the Backend."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceType(str, Enum):
    POSTGRESQL = "postgresql"
    CSV = "csv"
    EXCEL = "excel"
    JSON = "json"
    PARQUET = "parquet"
    MONGODB = "mongodb"
    REST_API = "rest_api"


class VerificationStatus(str, Enum):
    INFERRED_CANDIDATE = "inferred_candidate"
    VERIFIED_FACT = "verified_fact"
    CONTRADICTED = "contradicted"
    STALE_SCHEMA = "stale_schema"


class NodeType(str, Enum):
    SOURCE = "Source"
    COLLECTION = "Collection"
    COLUMN = "Column"
    CONCEPT = "Concept"
    METRIC = "Metric"
    DIMENSION = "Dimension"
    VALUE_MAPPING = "ValueMapping"
    RELATIONSHIP = "Relationship"
    QUERY_PATTERN = "QueryPattern"
    EVIDENCE_RECORD = "EvidenceRecord"


class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"
    HAS_COLUMN = "HAS_COLUMN"
    REPRESENTS_CONCEPT = "REPRESENTS_CONCEPT"
    REFERENCES = "REFERENCES"
    DERIVED_FROM = "DERIVED_FROM"
    MAPS_TO = "MAPS_TO"
    MEASURES = "MEASURES"
    USES_DIMENSION = "USES_DIMENSION"
    VERIFIED_BY = "VERIFIED_BY"


class SourceCapabilities(BaseModel):
    supports_sql: bool = True
    supports_explain: bool = True
    supports_schemas: bool = True
    supports_transactions: bool = False
    supports_vector_search: bool = False
    supports_document_pipeline: bool = False
    read_only: bool = True
    max_rows_limit: int = 5000
    timeout_seconds: int = 30
    allowed_tenants: List[str] = Field(default_factory=lambda: ["default"])


class SourceDescriptor(BaseModel):
    source_id: str
    name: str
    source_type: SourceType
    description: str = ""
    schema_version: str = "1.0.0"
    capabilities: SourceCapabilities = Field(default_factory=SourceCapabilities)
    connection_metadata: Dict[str, Any] = Field(default_factory=dict)
    is_connected: bool = False


class ColumnSchema(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_key_target: Optional[str] = None
    default_value: Optional[str] = None
    description: str = ""


class CollectionSchema(BaseModel):
    source_id: str
    collection_name: str
    schema_name: Optional[str] = None
    full_name: str
    row_count: int = 0
    columns: List[ColumnSchema] = Field(default_factory=list)
    description: str = ""
    primary_keys: List[str] = Field(default_factory=list)
    foreign_keys: List[Dict[str, Any]] = Field(default_factory=list)  # {"column", "references", optional "properties"}
    indexes: List[str] = Field(default_factory=list)


class ColumnProfile(BaseModel):
    source_id: str
    collection_name: str
    column_name: str
    data_type: str
    total_count: int = 0
    null_count: int = 0
    null_ratio: float = 0.0
    distinct_count: int = 0
    cardinality_ratio: float = 0.0
    is_categorical: bool = False
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    mean_value: Optional[float] = None
    top_values: List[Dict[str, Any]] = Field(default_factory=list)
    sample_values: List[Any] = Field(default_factory=list)


class ValueMatch(BaseModel):
    source_id: str
    collection_name: str
    column_name: str
    matched_value: Any
    similarity_score: float
    match_type: str  # "exact", "case_insensitive", "fuzzy", "substring"
    occurrence_count: int = 0


class QueryExecutionResult(BaseModel):
    source_id: str
    query: str
    succeeded: bool
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    execution_time_ms: float = 0.0
    error_message: Optional[str] = None
    query_plan: Optional[str] = None
    column_types: Dict[str, str] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)  # {"mode": "live"|"snapshot", "synced_at": ..., ...}


class EvidenceReference(BaseModel):
    evidence_id: str
    source_id: str
    collection_name: Optional[str] = None
    column_name: Optional[str] = None
    evidence_type: str  # "query_result", "row_sample", "column_profile", "schema_constraint", "reconciliation_check"
    query_or_action: str
    observed_summary: str
    sample_data: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: str = Field(default_factory=utc_now_iso)


class GraphNode(BaseModel):
    node_id: str
    node_type: NodeType
    label: str
    source_id: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    provenance: str = "system_discovery"
    verification_status: VerificationStatus = VerificationStatus.INFERRED_CANDIDATE
    schema_version: str = "1.0.0"
    confidence: float = 0.8
    evidence_refs: List[str] = Field(default_factory=list)
    last_validated_at: str = Field(default_factory=utc_now_iso)


class GraphEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: EdgeType
    properties: Dict[str, Any] = Field(default_factory=dict)
    provenance: str = "schema_inference"
    evidence_refs: List[str] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.INFERRED_CANDIDATE
    schema_version: str = "1.0.0"
    confidence: float = 0.75
    last_validated_at: str = Field(default_factory=utc_now_iso)


class CollectionSkillDescriptor(BaseModel):
    skill_id: str
    source_id: str
    collection_name: str
    domain_tag: str
    summary: str
    key_columns: List[str]
    metric_columns: List[str]
    dimension_columns: List[str]
    joinable_with: List[str]
    row_count: int = 0


class CompressionStats(BaseModel):
    raw_bytes: int
    compressed_bytes: int
    compression_ratio: float
    raw_tokens: int
    compressed_tokens: int
    token_reduction_pct: float
    template_count: int
    dictionary_entries: int
    collection_count: int


class CompiledSemanticContext(BaseModel):
    context_id: str
    compiled_at: str = Field(default_factory=utc_now_iso)
    schema_version: str = "1.0.0"
    sources: List[str]
    shared_schema_templates: Dict[str, List[str]] = Field(default_factory=dict)
    value_dictionary: Dict[str, List[Any]] = Field(default_factory=dict)
    compact_collections: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_relationships: List[Dict[str, Any]] = Field(default_factory=list)
    business_metrics: List[Dict[str, Any]] = Field(default_factory=list)
    semantic_aliases: Dict[str, str] = Field(default_factory=dict)
    verified_query_patterns: List[Dict[str, Any]] = Field(default_factory=list)
    representative_samples: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    compression_stats: CompressionStats


class ToolObservation(BaseModel):
    step_index: int
    phase: str
    objective: str
    tool_name: str
    tool_args: Dict[str, Any]
    observation_summary: str
    raw_output_preview: Any = None
    latency_ms: float = 0.0


class ExplorerInvestigationState(BaseModel):
    user_requirements: List[str] = Field(default_factory=list)
    known_facts: List[str] = Field(default_factory=list)
    unknown_facts: List[str] = Field(default_factory=list)
    hypotheses: List[str] = Field(default_factory=list)
    evidence_references: List[EvidenceReference] = Field(default_factory=list)
    tool_observations: List[ToolObservation] = Field(default_factory=list)
    candidate_queries: List[str] = Field(default_factory=list)
    validation_outcomes: List[str] = Field(default_factory=list)
    remaining_ambiguities: List[str] = Field(default_factory=list)


class ValidationFinding(BaseModel):
    validator_name: str
    passed: bool
    severity: str  # "info", "warning", "error"
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class AgentReasoningCandidate(BaseModel):
    agent_name: str  # "Explorer" or "Hercules"
    interpreted_question: str
    resolved_entities_and_metrics: Dict[str, Any] = Field(default_factory=dict)
    applied_filters: List[Dict[str, Any]] = Field(default_factory=list)
    source_mappings: List[Dict[str, str]] = Field(default_factory=list)
    query_or_plan: str
    source_id: str
    observed_results: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_references: List[EvidenceReference] = Field(default_factory=list)
    verification_results: List[ValidationFinding] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    final_answer_candidate: str
    structured_value: Optional[Any] = None
    tool_calls_count: int = 0
    tokens_used: int = 0
    latency_ms: float = 0.0
    investigation_trace: Optional[ExplorerInvestigationState] = None
    reasoning_mode: str = "deterministic_offline"  # llm | deterministic_offline | deterministic_fallback
    llm_usage: Dict[str, Any] = Field(default_factory=dict)
    result_provenance: Dict[str, Any] = Field(default_factory=dict)
    failure: Optional[str] = None


class AdjudicationDecision(BaseModel):
    question: str
    selected_path: str  # "Explorer", "Hercules", "Reconciled_Consensus", "Followup_Resolved", "Abstained_With_Uncertainty"
    agreement_status: str  # "exact_match", "semantic_match", "disagreement_resolved", "material_uncertainty"
    disagreement_analysis: Optional[str] = None
    discriminating_check_query: Optional[str] = None
    discriminating_check_result: Optional[List[Dict[str, Any]]] = None
    final_answer: str
    final_structured_data: List[Dict[str, Any]] = Field(default_factory=list)
    winning_query: str
    winning_source_id: str
    supporting_evidence: List[EvidenceReference] = Field(default_factory=list)
    validation_findings: List[ValidationFinding] = Field(default_factory=list)
    unresolved_assumptions: List[str] = Field(default_factory=list)
    confidence_score: float = 0.0
    verification_status: VerificationStatus = VerificationStatus.VERIFIED_FACT
    query_lineage: List[str] = Field(default_factory=list)
    total_latency_ms: float = 0.0
    total_tokens_used: int = 0
    llm_verdict: Dict[str, Any] = Field(default_factory=dict)
    verification_actions: List[Dict[str, Any]] = Field(default_factory=list)
