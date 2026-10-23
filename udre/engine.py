"""Engine facade: wires gateway, knowledge graph, compiler, memory, tools, agents and workflow."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from udre.agents.explorer import ExplorerAgent
from udre.agents.hercules import HerculesAgent
from udre.compiler.semantic_compiler import SemanticContextCompiler
from udre.config import Settings, pg_uri
from udre.contracts.models import CompiledSemanticContext
from udre.gateway import AccessContext, SourceGateway
from udre.gateway.file_adapter import FileSourceAdapter
from udre.gateway.postgres_adapter import PostgresSourceAdapter
from udre.memory.learning_store import LearningStore
from udre.ontology.builder import OntologyBuilder, load_packs
from udre.ontology.graph import KnowledgeGraph
from udre.ingestion.pipeline import IngestionPipeline
from udre.ontology.cross_source import CrossSourceMatcher
from udre.skills.context_generator import CollectionContextGenerator
from udre.skills.registry import SkillRegistry
from udre.skills.tools import ToolBox
from udre.verification.adjudicator import Adjudicator
from udre.verification.validators import Verifier


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
            export_formats: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.workflow.run(question, ctx or self.default_ctx, mode=mode, use_memory=use_memory,
                                 export_formats=export_formats)

    def toolbox(self, use_memory: bool = True) -> ToolBox:
        return ToolBox(self.gateway, self.kg, self.memory if use_memory else None, self.settings.exports_dir,
                       contexts=self.contexts, skills=self.skills)

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


def _providers(settings: Settings, override: Optional[Tuple[Any, Any]]) -> Tuple[Any, Any]:
    if override is not None:
        return override
    if settings.reasoning_mode == "offline":
        return None, None
    from udre.llm.config import LLMSettings, build_provider

    exp, her = build_provider(LLMSettings.from_env("explorer")), build_provider(LLMSettings.from_env("hercules"))
    if settings.reasoning_mode == "llm" and not (exp and her):
        raise RuntimeError("UDRE_REASONING_MODE=llm but no LLM is configured; set UDRE_LLM_BASE_URL, UDRE_LLM_API_KEY "
                           "and UDRE_LLM_MODEL (or per-agent UDRE_EXPLORER_LLM_* / UDRE_HERCULES_LLM_*)")
    return exp, her


def build_engine(settings: Optional[Settings] = None, register_defaults: bool = True,
                 llm_providers: Optional[Tuple[Any, Any]] = None) -> Engine:
    """``llm_providers=(explorer, hercules)`` overrides environment configuration (used by tests)."""
    from udre.orchestration.workflow import DualPathWorkflow

    settings = settings or Settings()
    explorer_llm, hercules_llm = _providers(settings, llm_providers)
    gateway = SourceGateway()
    if register_defaults:
        gateway.register(PostgresSourceAdapter("finance_pg", pg_uri(settings.data_db), name="Finance warehouse (PostgreSQL)",
                                               description="Operational finance warehouse"), tenants=settings.tenants)
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
    engine.workflow = DualPathWorkflow(engine)
    return engine
