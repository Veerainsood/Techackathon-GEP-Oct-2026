"""Source registry with tenant/user authorization, read-only enforcement and resource limits."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from udre.contracts.models import QueryExecutionResult, SourceDescriptor
from udre.gateway.base import DataSourceAdapter
from udre.gateway.sql_guard import UnsafeQueryError, analyze, assert_read_only, bound_rows


class AccessDenied(PermissionError):
    pass


@dataclass
class AccessContext:
    tenant_id: str = "default"
    user_id: str = "anonymous"
    roles: Set[str] = field(default_factory=set)
    allowed_sources: Optional[Set[str]] = None  # None = every source granted to the tenant
    denied_collections: Set[str] = field(default_factory=set)  # "source_id:collection"
    max_rows: int = 1000
    timeout_s: int = 30


@dataclass
class AuditEntry:
    ts: float
    tenant_id: str
    user_id: str
    source_id: str
    action: str
    detail: str
    allowed: bool


class SourceGateway:
    def __init__(self) -> None:
        self._adapters: Dict[str, DataSourceAdapter] = {}
        self._tenants: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()
        self.audit_log: List[AuditEntry] = []

    # -------------------------------------------------------------- registration
    def register(self, adapter: DataSourceAdapter, tenants: Optional[List[str]] = None, test: bool = True) -> SourceDescriptor:
        if test and not adapter.test_connection():
            raise ConnectionError(f"source {adapter.source_id} failed its connection test")
        with self._lock:
            self._adapters[adapter.source_id] = adapter
            self._tenants[adapter.source_id] = set(tenants or adapter.descriptor.capabilities.allowed_tenants)
        adapter.descriptor.capabilities.allowed_tenants = sorted(self._tenants[adapter.source_id])
        adapter.descriptor.is_connected = True
        return adapter.descriptor

    def grant(self, source_id: str, tenant: str) -> None:
        with self._lock:
            self._tenants[source_id].add(tenant)

    def revoke(self, source_id: str, tenant: str) -> None:
        with self._lock:
            self._tenants[source_id].discard(tenant)

    def unregister(self, source_id: str) -> None:
        with self._lock:
            self._adapters.pop(source_id, None)
            self._tenants.pop(source_id, None)

    # -------------------------------------------------------------- authorization
    def _audit(self, ctx: AccessContext, source_id: str, action: str, detail: str, allowed: bool) -> None:
        with self._lock:
            self.audit_log.append(AuditEntry(time.time(), ctx.tenant_id, ctx.user_id, source_id, action, detail[:300], allowed))
            del self.audit_log[:-5000]

    def can_access(self, ctx: AccessContext, source_id: str) -> bool:
        if source_id not in self._adapters:
            return False
        if ctx.tenant_id not in self._tenants[source_id] and "*" not in self._tenants[source_id]:
            return False
        return ctx.allowed_sources is None or source_id in ctx.allowed_sources

    def adapter(self, ctx: AccessContext, source_id: str) -> DataSourceAdapter:
        if not self.can_access(ctx, source_id):
            self._audit(ctx, source_id, "access", "denied", False)
            raise AccessDenied(f"tenant {ctx.tenant_id}/user {ctx.user_id} may not access source {source_id}")
        return self._adapters[source_id]

    def check_collection(self, ctx: AccessContext, source_id: str, collection: str) -> None:
        if f"{source_id}:{collection}" in ctx.denied_collections:
            self._audit(ctx, source_id, "collection", collection, False)
            raise AccessDenied(f"collection {collection} is not permitted for this user")

    def sources(self, ctx: AccessContext) -> List[SourceDescriptor]:
        return [a.descriptor for sid, a in self._adapters.items() if self.can_access(ctx, sid)]

    def collections(self, ctx: AccessContext, source_id: str) -> List[str]:
        adapter = self.adapter(ctx, source_id)
        return [c for c in adapter.discover_collections() if f"{source_id}:{c}" not in ctx.denied_collections]

    # -------------------------------------------------------------- execution
    def execute_query(self, ctx: AccessContext, source_id: str, query: str, max_rows: Optional[int] = None) -> QueryExecutionResult:
        adapter = self.adapter(ctx, source_id)
        cap = min(max_rows or ctx.max_rows, ctx.max_rows, adapter.descriptor.capabilities.max_rows_limit)
        timeout = min(ctx.timeout_s, adapter.descriptor.capabilities.timeout_seconds)
        if adapter.dialect is None:  # native (non-SQL) source: adapter enforces its own read-only rules
            self._audit(ctx, source_id, "execute_native", query, True)
            return adapter.execute(query, max_rows=cap, timeout_s=timeout)
        try:
            assert_read_only(query, adapter.dialect)
            known = set(adapter.discover_collections())
            for table in analyze(query, adapter.dialect).tables:
                if table not in known:
                    raise UnsafeQueryError(f"unknown or unauthorized collection: {table}")
                self.check_collection(ctx, source_id, table)
            bounded = bound_rows(query, adapter.dialect, cap)
        except (UnsafeQueryError, AccessDenied) as exc:
            self._audit(ctx, source_id, "execute", query, False)
            return QueryExecutionResult(source_id=source_id, query=query, succeeded=False, error_message=f"REJECTED: {exc}")
        self._audit(ctx, source_id, "execute", query, True)
        result = adapter.execute(bounded, max_rows=cap, timeout_s=timeout)
        result.query = query
        return result

    def explain_query(self, ctx: AccessContext, source_id: str, query: str) -> str:
        adapter = self.adapter(ctx, source_id)
        if adapter.dialect is None:
            return adapter.explain(query)
        try:
            assert_read_only(query, adapter.dialect)
        except UnsafeQueryError as exc:
            return f"REJECTED: {exc}"
        return adapter.explain(query)
