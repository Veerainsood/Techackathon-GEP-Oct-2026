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

    def register_files(self, source_id: str, paths: List[str], tenants: Optional[List[str]] = None) -> Dict[str, Any]:
        desc = self.gateway.register(FileSourceAdapter(source_id, paths, name=source_id), tenants=tenants or self.settings.tenants)
        report = self.builder.build(self.default_ctx, source_id, force=True)
        return {"source": desc.model_dump(), "ontology": report.__dict__}


def build_engine(settings: Optional[Settings] = None, register_defaults: bool = True) -> Engine:
    from udre.orchestration.workflow import DualPathWorkflow

    settings = settings or Settings()
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
                    compiler=compiler, explorer=ExplorerAgent(packs, max_steps=settings.explorer_max_steps),
                    hercules=HerculesAgent(packs, compiler, settings.hercules_context_tokens),
                    verifier=Verifier(gateway, kg, packs), adjudicator=Adjudicator(gateway, toolbox))
    engine.workflow = DualPathWorkflow(engine)
    return engine
