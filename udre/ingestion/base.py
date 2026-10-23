"""Connector contract and shared HTTP client for API-backed sources."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

import httpx

from udre.contracts.models import SourceType


class ConnectorError(RuntimeError):
    pass


class AuthenticationError(ConnectorError):
    pass


@dataclass
class ObjectSpec:
    """One API resource that becomes one snapshot collection."""

    name: str
    primary_key: str = "id"
    incremental: str = "none"  # none (full refresh + reconciliation) | timestamp | search_modified | change_queue
    cursor_field: Optional[str] = None
    description: str = ""
    field_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # column -> {label, type, description, options}
    parent: Optional[str] = None
    limitations: List[str] = field(default_factory=list)


@dataclass
class FetchResult:
    records: List[Dict[str, Any]]
    mode: str  # full | incremental
    checkpoint: Optional[str] = None
    archived_ids: List[str] = field(default_factory=list)
    pages: int = 0


@dataclass
class RelationshipHint:
    """Relationship stated by API metadata (e.g. association definitions, parent/child resources)."""

    child: str
    child_col: str
    parent: str
    parent_col: str
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorCapabilities:
    live_query: bool = False
    snapshot: bool = True
    incremental_objects: List[str] = field(default_factory=list)
    full_refresh_objects: List[str] = field(default_factory=list)
    webhooks: bool = False
    api_version: str = ""
    required_permissions: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)


class SourceConnector(ABC):
    source_type: SourceType

    @abstractmethod
    def authenticate(self) -> None: ...

    @abstractmethod
    def discover_objects(self) -> List[ObjectSpec]: ...

    @abstractmethod
    def fetch(self, spec: ObjectSpec, checkpoint: Optional[str], full: bool = False) -> FetchResult: ...

    def extra_tables(self, specs: List[ObjectSpec], ids: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
        """Additional tables derived from metadata (associations, pipelines...). Default: none."""
        return {}

    def relationship_hints(self, specs: List[ObjectSpec]) -> List[RelationshipHint]:
        return []

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities()

    def secrets(self) -> List[str]:
        return []


def snake(name: str) -> str:
    s = re.sub(r"[@#.]", "_", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[^0-9A-Za-z_]+", "_", s).strip("_").lower()
    return s or "field"


class ApiClient:
    """HTTP client with throttling/backoff, a single token refresh on 401, and secret redaction."""

    RETRY = {429, 500, 502, 503, 504}

    def __init__(self, base_url: str, token_provider: Callable[[bool], str], secrets: Callable[[], List[str]],
                 transport: Optional[httpx.BaseTransport] = None, max_retries: int = 5, max_backoff_s: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep, timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token_provider = token_provider
        self.secrets = secrets
        self.client = httpx.Client(transport=transport, timeout=timeout_s)
        self.max_retries = max_retries
        self.max_backoff_s = max_backoff_s
        self.sleep = sleep
        self.stats = {"requests": 0, "throttled": 0, "retries": 0, "token_refreshes": 0}

    def _redact(self, text: str) -> str:
        for s in self.secrets():
            if s and len(s) > 4:
                text = text.replace(s, "***")
        return re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1***", text)

    def request(self, method: str, url: str, params: Optional[Dict[str, Any]] = None, json: Any = None,
                headers: Optional[Dict[str, str]] = None) -> httpx.Response:
        full = url if url.startswith("http") else f"{self.base_url}{url}"
        attempt, refreshed, backoff = 0, False, 1.0
        while True:
            token = self.token_provider(False)
            h = {"authorization": f"Bearer {token}", "accept": "application/json", **(headers or {})}
            self.stats["requests"] += 1
            try:
                resp = self.client.request(method, full, params=params, json=json, headers=h)
            except httpx.TransportError as exc:
                resp = None
                err = f"transport error {type(exc).__name__}"
            if resp is not None and resp.status_code == 401 and not refreshed:
                refreshed = True
                self.stats["token_refreshes"] += 1
                self.token_provider(True)
                continue
            if resp is not None and resp.status_code < 400:
                return resp
            if resp is not None and resp.status_code not in self.RETRY:
                if resp.status_code in (401, 403):
                    raise AuthenticationError(f"{method} {self._redact(full)} -> HTTP {resp.status_code}: {self._redact(resp.text[:300])}")
                raise ConnectorError(f"{method} {self._redact(full)} -> HTTP {resp.status_code}: {self._redact(resp.text[:300])}")
            attempt += 1
            if attempt > self.max_retries:
                detail = f"HTTP {resp.status_code}" if resp is not None else err
                raise ConnectorError(f"{method} {self._redact(full)} failed after {self.max_retries} retries ({detail})")
            wait = backoff
            if resp is not None:
                if resp.status_code == 429:
                    self.stats["throttled"] += 1
                ra = resp.headers.get("retry-after")
                interval = resp.headers.get("x-hubspot-ratelimit-interval-milliseconds")
                if ra:
                    try:
                        wait = float(ra)
                    except ValueError:
                        wait = backoff
                elif interval and resp.status_code == 429:
                    wait = float(interval) / 1000.0
            self.stats["retries"] += 1
            self.sleep(min(wait, self.max_backoff_s))
            backoff = min(backoff * 2, self.max_backoff_s)

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", url, params=params).json()

    def post_json(self, url: str, body: Any) -> Any:
        return self.request("POST", url, json=body).json()


def chunks(seq: List[Any], n: int) -> Iterable[List[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
