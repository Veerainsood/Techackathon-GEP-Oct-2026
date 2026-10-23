"""Engine facade: wires gateway, knowledge graph, compiler, memory, tools, agents and workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from udre.config import Settings, pg_uri
from udre.gateway import AccessContext, SourceGateway
from udre.gateway.file_adapter import FileSourceAdapter
from udre.gateway.postgres_adapter import PostgresSourceAdapter
from udre.ontology.builder import OntologyBuilder
from udre.ontology.graph import KnowledgeGraph


@dataclass
class Engine:
    settings: Settings
    gateway: SourceGateway
    kg: KnowledgeGraph
    builder: OntologyBuilder
    default_ctx: AccessContext


def build_engine(settings: Optional[Settings] = None, register_defaults: bool = True) -> Engine:
    settings = settings or Settings()
    gateway = SourceGateway()
    if register_defaults:
        gateway.register(PostgresSourceAdapter("finance_pg", pg_uri(settings.data_db), name="Finance warehouse (PostgreSQL)",
                                               description="Operational finance warehouse"), tenants=settings.tenants)
        if Path(settings.uploads_dir).exists() and any(Path(settings.uploads_dir).iterdir()):
            gateway.register(FileSourceAdapter("uploads", [settings.uploads_dir], name="Uploaded files",
                                               description="User-uploaded CSV / Excel / JSON / Parquet files"),
                             tenants=settings.tenants)
    kg = KnowledgeGraph(pg_uri(settings.knowledge_db))
    builder = OntologyBuilder(gateway, kg)
    return Engine(settings=settings, gateway=gateway, kg=kg, builder=builder,
                  default_ctx=AccessContext(max_rows=settings.max_rows))
