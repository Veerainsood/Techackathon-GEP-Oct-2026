"""Engine facade: wires gateway, knowledge graph, compiler, memory, tools, agents and workflow."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.agents.explorer import ExplorerAgent
from backend.agents.hercules import HerculesAgent
from backend.compiler.semantic_compiler import SemanticContextCompiler
from backend.config import Settings, pg_uri
from backend.contracts.models import CompiledSemanticContext
from backend.gateway import AccessContext, SourceGateway
from backend.gateway.file_adapter import FileSourceAdapter
from backend.gateway.postgres_adapter import PostgresSourceAdapter
from backend.memory.learning_store import LearningStore
from backend.ontology.builder import OntologyBuilder, load_packs
from backend.ontology.graph import KnowledgeGraph
from backend.ingestion.pipeline import IngestionPipeline
from backend.ontology.cross_source import CrossSourceMatcher
from backend.skills.context_generator import CollectionContextGenerator
from backend.skills.registry import SkillRegistry
from backend.skills.tools import ToolBox
from backend.verification.adjudicator import Adjudicator
from backend.verification.validators import Verifier


@dataclass
class Engine:
    settings: Settings
    gateway: SourceGateway
    kg: KnowledgeGraph
    builder: OntologyBuilder
    default_ctx: AccessContext
    memory: Optional[LearningStore] = None
    packs: List[Dict[str, Any]] = field(default_factory=list)
    compiler: Optional[SemanticContextCompiler] = None
    explorer: Optional[ExplorerAgent] = None
    hercules: Optional[HerculesAgent] = None
    verifier: Optional[Verifier] = None
    adjudicator: Optional[Adjudicator] = None
    workflow: Any = None
    contexts: Any = None
    skills: Any = None
    llm: Any = None
    cross_source: Any = None
    ingestion: Any = None
    runtime_store: Any = None
    governance: Any = None
    investigator: Any = None
    consumer: Any = None
    files: Any = None
    sandbox: Any = None
    workspaces: Any = None
    llm_adjudicator: Any = None
    intent: Any = None
    conversations: Any = None
    prep: Any = None
    leakage: Any = None
    _ctx_cache: Dict[Tuple[str, str, bool], CompiledSemanticContext] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def compiled_context(self, ctx: AccessContext, use_memory: bool = True) -> CompiledSemanticContext:
        versions = ToolBox(self.gateway, self.kg, None, self.settings.exports_dir).source_versions(ctx)
        patterns = []
        if use_memory and self.memory:
            patterns = [{"question": i["question"], "sql": i["payload"]["sql"], "source_id": i["source_id"]}
                        for i in self.memory.by_kind(ctx, "query_template")
                        if versions.get(i["source_id"]) == i["schema_version"]][:20]
        key = (ctx.tenant_id, "|".join(f"{k}@{v}" for k, v in sorted(versions.items())) + f"#{len(patterns)}"
               + ",".join(sorted(ctx.denied_collections)), use_memory)
        with self._lock:
            if key not in self._ctx_cache:
                self._ctx_cache[key] = self.compiler.compile(ctx, verified_patterns=patterns)
            return self._ctx_cache[key]

    def ask(self, question: str, ctx: Optional[AccessContext] = None, mode: str = "dual", use_memory: bool = True,
            export_formats: Optional[List[str]] = None, workspace_id: Optional[str] = None, on_event=None,
            history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        ctx = ctx or self.default_ctx
        if workspace_id:
            ctx = self.workspaces.context(ctx, workspace_id)
        return self.workflow.run(question, ctx, mode=mode, use_memory=use_memory, export_formats=export_formats, on_event=on_event,
                                 history=history)

    def model_config(self) -> Dict[str, Any]:
        def describe(p):
            return {"mode": "llm", "provider": p.name, "model": p.model} if p else {"mode": "deterministic_offline"}
        return {"explorer": describe(self.explorer.llm), "hercules": describe(self.hercules.llm),
                "adjudicator": describe(self.llm_adjudicator.provider if self.llm_adjudicator else None),
                "intent": describe(self.intent.llm if self.intent else None),
                "prep": describe(self.prep.agent.llm if self.prep else None),
                "sandbox": self.prep.provider if self.prep else "off"}

    def invalidate_caches(self, tenant: str, source_id: Optional[str] = None) -> int:
        """Drop compiled contexts for a tenant (and per-source compiler parts) after data or knowledge changes."""
        with self._lock:
            keys = [k for k in self._ctx_cache if k[0] == tenant]
            for k in keys:
                del self._ctx_cache[k]
        if self.compiler is not None:
            with self.compiler._lock:
                for k in [k for k in self.compiler._cache if k[0] == tenant and (source_id is None or k[1] == source_id)]:
                    del self.compiler._cache[k]
        return len(keys)

    def refresh_collection_profiles(self, ctx: AccessContext, source_id: str, collections: List[str]) -> List[str]:
        """Re-profile only the given collections and update their Column/Collection nodes (ontology structure untouched)."""
        from backend.contracts.models import GraphNode, NodeType, VerificationStatus
        from backend.ontology.builder import is_dimension, is_measure

        adapter = self.gateway.adapter(ctx, source_id)
        wanted = set(collections) & set(self.gateway.collections(ctx, source_id))
        nodes = [n for n in self.kg.nodes(NodeType.COLUMN, source_id, ctx.tenant_id) if n["properties"].get("collection") in wanted]
        if not nodes or not hasattr(adapter, "profile_collection"):
            return []
        profiles = {c: adapter.profile_collection(c) for c in wanted}
        updated = []
        for n in nodes:
            p = dict(n["properties"])
            prof = profiles[p["collection"]].get(p["column"])
            if prof is None:
                continue
            p.update(null_ratio=prof.null_ratio, distinct_count=prof.distinct_count, cardinality_ratio=prof.cardinality_ratio,
                     min=prof.min_value, max=prof.max_value, mean=prof.mean_value, top_values=prof.top_values[:15],
                     samples=prof.sample_values[:3], is_dimension=is_dimension(prof),
                     is_measure=is_measure(p["column"], prof, bool(p.get("is_key"))))
            updated.append(GraphNode(node_id=n["node_id"], node_type=NodeType(n["node_type"]), label=n["label"], source_id=source_id,
                                     properties=p, provenance=n["provenance"], verification_status=VerificationStatus(n["verification_status"]),
                                     schema_version=n["schema_version"], confidence=n["confidence"], evidence_refs=n["evidence_refs"]))
        self.kg.upsert_nodes(updated, ctx.tenant_id)
        for c in wanted:
            node = self.kg.get_node(f"coll:{source_id}:{c}", ctx.tenant_id)
            if node:
                props = dict(node["properties"], row_count=next(iter(profiles[c].values())).total_count if profiles[c] else 0)
                self.kg.upsert_nodes([GraphNode(node_id=node["node_id"], node_type=NodeType.COLLECTION, label=node["label"],
                                                source_id=source_id, properties=props, provenance=node["provenance"],
                                                verification_status=VerificationStatus(node["verification_status"]),
                                                schema_version=node["schema_version"], confidence=node["confidence"],
                                                evidence_refs=node["evidence_refs"])], ctx.tenant_id)
        return sorted(wanted)

    def toolbox(self, use_memory: bool = True) -> ToolBox:
        return ToolBox(self.gateway, self.kg, self.memory if use_memory else None, self.settings.exports_dir,
                       contexts=self.contexts, skills=self.skills, sandbox=self.sandbox, files=self.files)

    def prepare_source(self, ctx: AccessContext, source_id: str, force: bool = False) -> Dict[str, Any]:
        """Ontology build (incremental) + collection reference generation for one source."""
        report = self.builder.build(ctx, source_id, force=force)
        if self.memory and not report.skipped:
            self.memory.invalidate_source(source_id, report.schema_version)
        gen = self.contexts.generate(ctx, source_id, force=force) if self.contexts else None
        return {"ontology": report.__dict__, "contexts": gen.__dict__ if gen else None}

    def register_files(self, source_id: str, paths: List[str], tenants: Optional[List[str]] = None,
                       ctx: Optional[AccessContext] = None) -> Dict[str, Any]:
        desc = self.gateway.register(FileSourceAdapter(source_id, paths, name=source_id), tenants=tenants or self.settings.tenants)
        ctx = ctx or AccessContext(tenant_id=(tenants or self.settings.tenants)[0], max_rows=self.settings.max_rows)
        return {"source": desc.model_dump(), **self.prepare_source(ctx, source_id, force=True)}


def _providers(settings: Settings, override: Optional[Tuple[Any, ...]]) -> Tuple[Any, Any, Any, Any, Any]:
    """(explorer, hercules, adjudicator, intent, prep) providers; None means deterministic."""
    if override is not None:
        return (tuple(override) + (None,) * 5)[:5]
    if settings.reasoning_mode == "offline":
        return None, None, None, None, None
    from backend.llm.config import LLMSettings, build_provider

    exp, her = build_provider(LLMSettings.from_env("explorer")), build_provider(LLMSettings.from_env("hercules"))
    adj, intent = build_provider(LLMSettings.from_env("adjudicator")), build_provider(LLMSettings.from_env("intent"))
    prep = build_provider(LLMSettings.from_env("prep"))
    if settings.reasoning_mode == "llm" and not (exp and her):
        raise RuntimeError("BACKEND_REASONING_MODE=llm but no LLM is configured; set BACKEND_FOUNDRY_ENDPOINT and BACKEND_FOUNDRY_API_KEY "
                           "(or BACKEND_LLM_BASE_URL / BACKEND_LLM_API_KEY / BACKEND_LLM_MODEL, or per-agent BACKEND_<AGENT>_LLM_*)")
    return exp, her, adj, intent, prep


def build_engine(settings: Optional[Settings] = None, register_defaults: bool = True,
                 llm_providers: Optional[Tuple[Any, Any]] = None) -> Engine:
    """``llm_providers=(explorer, hercules[, adjudicator[, intent]])`` overrides environment configuration (used by tests)."""
    from backend.agents.intent import IntentAgent
    from backend.orchestration.workflow import DualPathWorkflow

    settings = settings or Settings()
    explorer_llm, hercules_llm, adjudicator_llm, intent_llm, prep_llm = _providers(settings, llm_providers)
    gateway = SourceGateway()
    if register_defaults:
        # "public" holds knowledge-ingestion's bookkeeping tables (raw HubSpot payloads), which agents must not read.
        gateway.register(PostgresSourceAdapter("finance_pg", pg_uri(settings.data_db), name="Finance warehouse",
                                               description="Operational finance warehouse",
                                               exclude_schemas=["public", "hubspot"]), tenants=settings.tenants)
        # HubSpot CRM data written by knowledge-ingestion. Its sync events use source_id "hubspot" (BACKEND_SOURCE_ID)
        # and name exactly these collections: contacts in their own schema, companies in prospect_accounts.
        gateway.register(PostgresSourceAdapter("hubspot", pg_uri(settings.data_db), name="HubSpot CRM",
                                               description="Contacts and companies synced from HubSpot by knowledge-ingestion",
                                               include_schemas=["hubspot"],
                                               include_collections=["pipeline_crm.prospect_accounts"]), tenants=settings.tenants)
        if Path(settings.uploads_dir).exists() and any(Path(settings.uploads_dir).iterdir()):
            gateway.register(FileSourceAdapter("uploads", [settings.uploads_dir], name="Uploaded files",
                                               description="User-uploaded CSV / Excel / JSON / Parquet files"),
                             tenants=settings.tenants)
    knowledge_uri = pg_uri(settings.knowledge_db)
    kg = KnowledgeGraph(knowledge_uri)
    packs = load_packs()
    builder = OntologyBuilder(gateway, kg, packs)
    compiler = SemanticContextCompiler(gateway, kg)
    toolbox = ToolBox(gateway, kg, None, settings.exports_dir)
    engine = Engine(settings=settings, gateway=gateway, kg=kg, builder=builder,
                    default_ctx=AccessContext(max_rows=settings.max_rows), memory=LearningStore(knowledge_uri), packs=packs,
                    compiler=compiler, explorer=ExplorerAgent(packs, max_steps=settings.explorer_max_steps, llm=explorer_llm),
                    hercules=HerculesAgent(packs, compiler, settings.hercules_context_tokens, llm=hercules_llm),
                    verifier=Verifier(gateway, kg, packs), adjudicator=Adjudicator(gateway, toolbox))
    engine.skills = SkillRegistry()
    engine.contexts = CollectionContextGenerator(gateway, kg, engine.memory, compiler)
    engine.cross_source = CrossSourceMatcher(gateway, kg)
    engine.ingestion = IngestionPipeline(engine)
    from backend.events.consumer import EventConsumer
    from backend.events.store import RuntimeStore
    from backend.runtime.investigator import ChangeInvestigator
    from backend.runtime.sessions import WorkspaceManager
    from backend.tools.files import FileStore
    from backend.tools.sandbox import SandboxManager
    from backend.verification.llm_adjudicator import LLMAdjudicator

    engine.runtime_store = RuntimeStore(knowledge_uri)
    from backend.governance.store import GovernanceStore
    engine.governance = GovernanceStore(knowledge_uri)
    engine.investigator = ChangeInvestigator(engine, engine.runtime_store)
    engine.consumer = EventConsumer(engine.runtime_store, engine.investigator)
    engine.files = FileStore()
    engine.sandbox = SandboxManager(engine.files)
    engine.workspaces = WorkspaceManager(engine)
    engine.llm_adjudicator = LLMAdjudicator(adjudicator_llm, gateway) if adjudicator_llm else None
    engine.intent = IntentAgent(intent_llm)
    from backend.runtime.conversations import ConversationStore
    engine.conversations = ConversationStore(knowledge_uri)
    from backend.prep.service import PrepJobStore, PrepService
    engine.prep = PrepService(engine, prep_llm, PrepJobStore(knowledge_uri))
    from backend.leakage.service import LeakageService
    ledger_llm = None
    if llm_providers is None and settings.reasoning_mode != "offline":
        from backend.llm.config import LLMSettings, build_provider
        ledger_llm = build_provider(LLMSettings.from_env("ledger"))
    engine.leakage = LeakageService(engine, knowledge_uri, ledger_llm=ledger_llm)
    if register_defaults:
        # Detected leakage cases, readable by the agents like any other data (read-only, one table).
        gateway.register(PostgresSourceAdapter("leakage", knowledge_uri, name="Leakage cases",
                                               description="Spend leakage cases found by the detectors, with recoverable value, "
                                                           "confidence, root cause and recovery status",
                                               include_collections=["backend_runtime.leakage_cases"]), tenants=settings.tenants)
    engine.workflow = DualPathWorkflow(engine)
    return engine
