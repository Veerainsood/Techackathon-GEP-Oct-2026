"""HubSpot CRM connector (CRM v3 objects/properties/pipelines/schemas, v4 associations).

Authentication: private-app access token, or OAuth (client id/secret + refresh token; refreshed on expiry
and once on HTTP 401).  Scopes: crm.objects.companies.read, crm.objects.contacts.read, crm.objects.deals.read,
crm.objects.line_items.read, e-commerce (products), crm.schemas.custom.read and crm.objects.custom.read
for custom objects.

* Properties are discovered dynamically from /crm/v3/properties/{object} (labels, types, descriptions,
  enumeration options). HubSpot returns property values as strings; values are typed from that metadata.
  Nothing about specific properties is hardcoded beyond the per-object last-modified field, which is
  detected from metadata (hs_lastmodifieddate, else lastmodifieddate).
* Incremental sync uses the CRM search API (last-modified >= checkpoint, ascending), sliding the window
  past the search API's 10,000-result cap; full refresh lists every object and reconciles absences.
* Archived records are fetched with archived=true and kept as tombstones (_backend_archived = true).
* Associations (v4 batch read) are stored per direction as assoc__<from>__<to> with association type
  id, category and label, and declared as relationships so ontology edges keep type and direction.
* Webhooks (optional): v3 signatures are verified and events are queued; the next sync re-reads the
  queued ids through batch read.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from backend.contracts.models import SourceType
from backend.ingestion.base import (
    ApiClient,
    AuthenticationError,
    ConnectorCapabilities,
    FetchResult,
    ObjectSpec,
    RelationshipHint,
    SourceConnector,
    chunks,
    snake,
)

HUBSPOT_API = "https://api.hubapi.com"
STANDARD_OBJECTS = ["companies", "contacts", "deals", "products", "line_items"]
DEFAULT_ASSOCIATIONS = [("companies", "contacts"), ("companies", "deals"), ("contacts", "deals"), ("deals", "line_items")]
SEARCH_CAP = 10_000


def _typed(value: Any, ptype: Optional[str]) -> Any:
    if value is None or value == "":
        return None
    try:
        if ptype == "number":
            return float(value)
        if ptype == "bool":
            return str(value).lower() == "true"
        if ptype in ("datetime", "date"):
            v = str(value)
            if v.isdigit():
                return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).isoformat()
            return v
    except (TypeError, ValueError):
        return value
    return value


def verify_webhook_v3(client_secret: str, method: str, uri: str, body: str, timestamp: str, signature: str,
                      max_age_s: int = 300, now_ms: Optional[int] = None) -> bool:
    now_ms = now_ms or int(time.time() * 1000)
    try:
        if abs(now_ms - int(timestamp)) > max_age_s * 1000:
            return False
    except ValueError:
        return False
    digest = hmac.new(client_secret.encode(), f"{method}{uri}{body}{timestamp}".encode(), hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


class HubSpotSourceAdapter(SourceConnector):
    source_type = SourceType.REST_API

    def __init__(self, access_token: Optional[str] = None, client_id: Optional[str] = None, client_secret: Optional[str] = None,
                 refresh_token: Optional[str] = None, objects: Optional[List[str]] = None,
                 properties: Optional[Dict[str, List[str]]] = None, associations: Optional[Sequence[Tuple[str, str]]] = None,
                 include_custom_objects: bool = True, base_url: str = HUBSPOT_API, page_size: int = 100,
                 transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep):
        self._token = access_token or os.environ.get("BACKEND_HUBSPOT_ACCESS_TOKEN", "")
        self._client_id = client_id or os.environ.get("BACKEND_HUBSPOT_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("BACKEND_HUBSPOT_CLIENT_SECRET", "")
        self._refresh = refresh_token or os.environ.get("BACKEND_HUBSPOT_REFRESH_TOKEN", "")
        if not self._token and not (self._client_id and self._client_secret and self._refresh):
            raise AuthenticationError("HubSpot connector requires a private-app token (BACKEND_HUBSPOT_ACCESS_TOKEN) or OAuth "
                                      "client id/secret and refresh token")
        self.objects = objects or list(STANDARD_OBJECTS)
        self.selected_properties = properties or {}
        self.associations = list(associations or DEFAULT_ASSOCIATIONS)
        self.include_custom = include_custom_objects
        self.base_url, self.page_size, self.sleep = base_url.rstrip("/"), page_size, sleep
        self._token_exp = float("inf") if access_token or (self._token and not self._refresh) else 0.0
        self._http = httpx.Client(transport=transport, timeout=30)
        self.api = ApiClient(self.base_url, self._get_token, self.secrets, transport=transport, sleep=sleep)
        self._paths: Dict[str, str] = {}  # object name -> API object type (custom objects use their objectTypeId)
        self._meta: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._modprop: Dict[str, str] = {}
        self.pending_ids: Dict[str, List[str]] = {}

    # ------------------------------------------------------------------ auth
    def secrets(self) -> List[str]:
        return [s for s in (self._token, self._client_secret, self._refresh) if s]

    def _get_token(self, force: bool = False) -> str:
        if self._token and not force and time.time() < self._token_exp - 60:
            return self._token
        if not self._refresh:
            if force:
                raise AuthenticationError("HubSpot private-app token rejected (HTTP 401); no refresh token configured")
            return self._token
        resp = self._http.post(f"{self.base_url}/oauth/v1/token", data={
            "grant_type": "refresh_token", "client_id": self._client_id, "client_secret": self._client_secret,
            "refresh_token": self._refresh})
        if resp.status_code != 200:
            raise AuthenticationError(f"HubSpot OAuth refresh failed (HTTP {resp.status_code})")
        data = resp.json()
        self._token, self._token_exp = data["access_token"], time.time() + int(data.get("expires_in", 1800))
        return self._token

    def authenticate(self) -> None:
        if self._refresh:
            self._get_token(force=True)
        self.api.get_json("/crm/v3/properties/companies" if "companies" in self.objects else f"/crm/v3/properties/{self.objects[0]}")

    # ------------------------------------------------------------------ discovery
    def _properties(self, obj: str) -> Dict[str, Dict[str, Any]]:
        data = self.api.get_json(f"/crm/v3/properties/{self._paths.get(obj, obj)}")
        meta = {}
        for p in data.get("results", []):
            meta[snake(p["name"])] = {"label": p.get("label"), "type": p.get("type"), "description": p.get("description") or "",
                                      "options": [o.get("value") for o in p.get("options") or []] or None,
                                      "name": p["name"], "custom": not p.get("hubspotDefined", True)}
        return meta

    def discover_objects(self) -> List[ObjectSpec]:
        objs = list(self.objects)
        if self.include_custom:
            try:
                for s in self.api.get_json("/crm/v3/schemas").get("results", []):
                    name = snake(s.get("name") or s["objectTypeId"])
                    self._paths[name] = s["objectTypeId"]
                    if name not in objs:
                        objs.append(name)
            except AuthenticationError:
                pass  # custom-object scope not granted: standard objects only
        specs = []
        for obj in objs:
            meta = self._properties(obj)
            self._meta[obj] = meta
            mod = "hs_lastmodifieddate" if "hs_lastmodifieddate" in meta else "lastmodifieddate" if "lastmodifieddate" in meta else None
            if mod:
                self._modprop[obj] = meta[mod]["name"]
            field_meta = {k: v for k, v in meta.items()}
            field_meta.update({"created_at": {"label": "Created at", "type": "datetime"},
                               "updated_at": {"label": "Updated at", "type": "datetime"},
                               "archived": {"label": "Archived in HubSpot", "type": "bool"}})
            specs.append(ObjectSpec(name=obj, incremental="search_modified" if mod else "none", cursor_field=mod,
                                    description=f"HubSpot CRM {obj}" + (" (custom object)" if obj in self._paths else ""),
                                    field_meta=field_meta))
        return specs

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            live_query=False, snapshot=True, incremental_objects=[o for o in self._modprop],
            full_refresh_objects=[o for o in self.objects if o not in self._modprop], webhooks=True,
            api_version="HubSpot CRM v3 (objects, properties, pipelines, schemas) + v4 associations",
            required_permissions=["crm.objects.companies.read", "crm.objects.contacts.read", "crm.objects.deals.read",
                                  "crm.objects.line_items.read", "e-commerce", "crm.schemas.custom.read", "crm.objects.custom.read"],
            limitations=["Snapshot only: SQL runs on the synchronized copy",
                         "Search-based incremental sync can miss records modified during the sync window; periodic full refresh reconciles",
                         "Hard deletes are detected only by full refresh (or webhooks)"])

    # ------------------------------------------------------------------ fetch
    def _props_param(self, obj: str) -> List[str]:
        if obj in self.selected_properties:
            return self.selected_properties[obj]
        return [m["name"] for m in self._meta.get(obj, {}).values() if m.get("name")]

    def _record(self, obj: str, r: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": str(r["id"]), "created_at": r.get("createdAt"), "updated_at": r.get("updatedAt"),
                               "archived": bool(r.get("archived", False))}
        meta = self._meta.get(obj, {})
        for k, v in (r.get("properties") or {}).items():
            key = snake(k)
            if key in ("id", "created_at", "updated_at", "archived"):
                key = f"prop_{key}"
            val = _typed(v, (meta.get(snake(k)) or {}).get("type"))
            if key.endswith("_id") and isinstance(val, float) and val.is_integer():
                val = str(int(val))  # identifiers typed as numbers must still join against string ids
            out[key] = val
        return out

    def _list(self, obj: str, archived: bool) -> Tuple[List[Dict[str, Any]], int]:
        params: Dict[str, Any] = {"limit": self.page_size, "archived": str(archived).lower(),
                                  "properties": ",".join(self._props_param(obj))}
        out, pages, after = [], 0, None
        while True:
            if after:
                params["after"] = after
            data = self.api.get_json(f"/crm/v3/objects/{self._paths.get(obj, obj)}", params=params)
            out += [self._record(obj, r) for r in data.get("results", [])]
            pages += 1
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return out, pages

    def _search_modified(self, obj: str, since_ms: int) -> Tuple[List[Dict[str, Any]], int, int]:
        prop = self._modprop[obj]
        out: Dict[str, Dict[str, Any]] = {}
        pages, window, max_seen = 0, since_ms, since_ms
        while True:
            after, fetched_in_window = None, 0
            while True:
                body = {"filterGroups": [{"filters": [{"propertyName": prop, "operator": "GTE", "value": str(window)}]}],
                        "sorts": [{"propertyName": prop, "direction": "ASCENDING"}], "properties": self._props_param(obj),
                        "limit": self.page_size}
                if after:
                    body["after"] = after
                data = self.api.post_json(f"/crm/v3/objects/{self._paths.get(obj, obj)}/search", body)
                pages += 1
                for r in data.get("results", []):
                    rec = self._record(obj, r)
                    out[rec["id"]] = rec
                    ts = (r.get("properties") or {}).get(prop) or r.get("updatedAt")
                    max_seen = max(max_seen, _to_ms(ts))
                fetched_in_window += len(data.get("results", []))
                after = ((data.get("paging") or {}).get("next") or {}).get("after")
                if not after:
                    return list(out.values()), pages, max_seen
                if fetched_in_window >= SEARCH_CAP - self.page_size:  # slide the window past the search cap
                    window = max_seen
                    break

    def fetch(self, spec: ObjectSpec, checkpoint: Optional[str], full: bool = False) -> FetchResult:
        obj = spec.name
        queued = self.pending_ids.pop(obj, [])
        if full or not checkpoint or obj not in self._modprop:
            active, p1 = self._list(obj, archived=False)
            archived, p2 = self._list(obj, archived=True)
            records = active + archived
            newest = max([_to_ms(r.get("updated_at")) for r in records] or [0])
            return FetchResult(records=records, mode="full", checkpoint=str(newest) if obj in self._modprop else None,
                               archived_ids=[r["id"] for r in archived], pages=p1 + p2)
        records, pages, newest = self._search_modified(obj, int(checkpoint))
        if queued:
            records += self.batch_read(obj, [i for i in queued if i not in {r["id"] for r in records}])
        archived, p2 = self._list(obj, archived=True)
        return FetchResult(records=records + archived, mode="incremental", checkpoint=str(max(newest, int(checkpoint))),
                           archived_ids=[r["id"] for r in archived], pages=pages + p2)

    def batch_read(self, obj: str, ids: List[str]) -> List[Dict[str, Any]]:
        out = []
        for batch in chunks(ids, 100):
            data = self.api.post_json(f"/crm/v3/objects/{self._paths.get(obj, obj)}/batch/read",
                                      {"inputs": [{"id": i} for i in batch], "properties": self._props_param(obj)})
            out += [self._record(obj, r) for r in data.get("results", [])]
        return out

    # ------------------------------------------------------------------ associations, pipelines
    def extra_tables(self, specs: List[ObjectSpec], ids: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
        tables: Dict[str, List[Dict[str, Any]]] = {}
        for frm, to in self.associations:
            if frm not in ids or to not in ids:
                continue
            rows = []
            for batch in chunks(ids[frm], 100):
                data = self.api.post_json(f"/crm/v4/associations/{self._paths.get(frm, frm)}/{self._paths.get(to, to)}/batch/read",
                                          {"inputs": [{"id": i} for i in batch]})
                for res in data.get("results", []):
                    for target in res.get("to", []):
                        for at in target.get("associationTypes", []) or [{}]:
                            rows.append({"id": f"{res['from']['id']}:{target['toObjectId']}:{at.get('typeId')}",
                                         "from_id": str(res["from"]["id"]), "to_id": str(target["toObjectId"]),
                                         "association_type_id": at.get("typeId"), "association_category": at.get("category"),
                                         "association_label": at.get("label"), "direction": f"{frm}->{to}"})
            tables[f"assoc__{frm}__{to}"] = rows
        if "deals" in ids:
            pipes, stages = [], []
            for p in self.api.get_json("/crm/v3/pipelines/deals").get("results", []):
                pipes.append({"id": p["id"], "label": p.get("label"), "display_order": p.get("displayOrder"), "archived": p.get("archived", False)})
                for s in p.get("stages", []):
                    md = s.get("metadata") or {}
                    stages.append({"id": s["id"], "pipeline_id": p["id"], "label": s.get("label"), "display_order": s.get("displayOrder"),
                                   "probability": _typed(md.get("probability"), "number"), "is_closed": _typed(md.get("isClosed"), "bool")})
            tables["deal_pipelines"], tables["deal_pipeline_stages"] = pipes, stages
        return tables

    def relationship_hints(self, specs: List[ObjectSpec]) -> List[RelationshipHint]:
        names = {s.name for s in specs}
        hints = []
        for frm, to in self.associations:
            if frm in names and to in names:
                t = f"assoc__{frm}__{to}"
                props = {"origin": "hubspot_v4_association", "direction": f"{frm}->{to}", "from_object": frm, "to_object": to}
                hints += [RelationshipHint(t, "from_id", frm, "id", dict(props, role="from")),
                          RelationshipHint(t, "to_id", to, "id", dict(props, role="to"))]
        if "deals" in names:
            hints += [RelationshipHint("deal_pipeline_stages", "pipeline_id", "deal_pipelines", "id", {"origin": "hubspot_pipeline"}),
                      RelationshipHint("deals", "pipeline", "deal_pipelines", "id", {"origin": "hubspot_pipeline_property"}),
                      RelationshipHint("deals", "dealstage", "deal_pipeline_stages", "id", {"origin": "hubspot_pipeline_property"})]
        if {"line_items", "products"} <= names:
            hints.append(RelationshipHint("line_items", "hs_product_id", "products", "id", {"origin": "hubspot_product_reference"}))
        return hints

    # ------------------------------------------------------------------ webhooks
    def handle_webhook(self, events: List[Dict[str, Any]], store=None) -> int:
        """Queue changed object ids from (already signature-verified) webhook events."""
        n = 0
        for ev in events:
            obj = {"contact": "contacts", "company": "companies", "deal": "deals", "product": "products",
                   "line_item": "line_items"}.get(str(ev.get("subscriptionType", "")).split(".")[0])
            if not obj:
                continue
            self.pending_ids.setdefault(obj, []).append(str(ev["objectId"]))
            if store is not None:
                store.enqueue_change(obj, str(ev["objectId"]), ev.get("subscriptionType", ""), str(ev.get("occurredAt", "")))
            n += 1
        return n


def _to_ms(ts: Any) -> int:
    if ts is None:
        return 0
    s = str(ts)
    if s.isdigit():
        return int(s)
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0
