"""Proposed event contract v1 between the ingestion/synchronization service and the agent runtime.

PROPOSAL -- to be agreed with the ingestion engineer; the JSON Schema is exported to
``docs/events/sync-event.v1.schema.json``.  Required fields are the minimum the runtime needs; optional
fields make investigation precise (changed keys / CDC operations beat timestamp windows).

Delivery and visibility rules the producer must honour:
  * publish only after the database transaction(s) for the window are committed and visible to readers
  * ``window_start`` / ``window_end`` are a half-open interval ``[start, end)`` on each collection's
    ``timestamp_column``; consecutive syncs should share the boundary (end of one == start of the next)
  * ``sequence`` (or the numeric part of ``data_version``) increases monotonically per (tenant, source, database)
  * authenticity: HMAC-SHA256 of the raw body with the shared secret, hex in ``X-UDRE-Signature``
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "udre.sync-event.v1"


class RecordChange(BaseModel):
    """One CDC change, when the producer can provide it."""

    op: Literal["insert", "update", "delete"]
    key: Dict[str, Any] = Field(description="Primary-key column(s) -> value")
    changed_columns: Optional[List[str]] = None
    before: Optional[Dict[str, Any]] = Field(None, description="Old values (only if the producer captured them)")
    committed_at: Optional[datetime] = None


class CollectionChange(BaseModel):
    name: str = Field(description="Collection/table name as exposed by the source (schema-qualified for SQL sources)")
    timestamp_column: Optional[str] = Field(None, description="Column that records modification time in this collection")
    status: Literal["completed", "failed", "skipped"] = "completed"
    expected_changes: Optional[int] = Field(None, ge=0, description="Rows the producer wrote in the window (visibility check)")
    changes: List[RecordChange] = Field(default_factory=list)
    schema_changed: bool = False
    error: Optional[str] = None


class SyncEvent(BaseModel):
    schema_: str = Field(SCHEMA_VERSION, alias="schema")
    event_id: str = Field(min_length=1, max_length=200)
    event_type: Literal["database.sync.completed", "database.sync.failed", "knowledge.version.published"]
    tenant_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1, description="Source as registered in the UDRE gateway")
    database_id: Optional[str] = None
    collections: List[CollectionChange] = Field(default_factory=list)
    window_start: datetime
    window_end: datetime
    data_version: str = Field(min_length=1)
    sequence: Optional[int] = Field(None, ge=0, description="Monotonic per (tenant, source, database); defaults to digits of data_version")
    schema_version: Optional[str] = None
    knowledge_version: Optional[str] = None
    commit_id: Optional[str] = None
    status: Literal["completed", "failed", "partial"] = "completed"
    published_at: Optional[datetime] = None
    producer: Optional[str] = None

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @field_validator("window_start", "window_end", "published_at")
    @classmethod
    def _tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def _check(self) -> "SyncEvent":
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start (half-open interval [start, end))")
        if self.event_type == "database.sync.completed" and self.status == "completed" and not self.collections:
            raise ValueError("a completed sync event must list the collections it changed")
        names = [c.name for c in self.collections]
        if len(names) != len(set(names)):
            raise ValueError("collections must be unique")
        if self.sequence is None:
            digits = re.findall(r"\d+", self.data_version)
            self.sequence = int(digits[-1]) if digits else None
        return self

    @property
    def ordering_key(self) -> str:
        return f"{self.tenant_id}|{self.source_id}|{self.database_id or ''}"


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: Optional[str], secret: str) -> bool:
    if not secret or not signature:
        return False
    return hmac.compare_digest(sign(body, secret), signature.removeprefix("sha256="))


def json_schema() -> Dict[str, Any]:
    schema = SyncEvent.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA_VERSION
    schema["description"] = (__doc__ or "").strip()
    return schema
