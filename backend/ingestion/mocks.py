"""Deterministic mock Microsoft Graph and HubSpot APIs (httpx transports) for tests and offline demos.

These reproduce the documented wire behaviour the connectors depend on (OAuth endpoints, paging links,
throttling with Retry-After, string-typed HubSpot properties, archived records, v4 association payloads,
search API, report export jobs).  They are fixtures: passing against them does not prove live integration.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx

BASE_TIME = datetime(2025, 12, 1, tzinfo=timezone.utc)


def _guid(seed: str) -> str:
    h = hashlib.md5(seed.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# =============================================================================== Microsoft Graph
class MockGraphAPI:
    TOKEN = "mock-graph-access-token"

    def __init__(self, tenant_id: str = "11111111-2222-3333-4444-555555555555", client_id: str = "app-client-id",
                 client_secret: str = "app-client-secret", devices: int = 230, throttle_once: bool = True, seed: int = 7):
        self.tenant_id, self.client_id, self.client_secret = tenant_id, client_id, client_secret
        self.throttle_once = throttle_once
        self.requests: List[str] = []
        self.fail_paths: Dict[str, int] = {}
        rng = random.Random(seed)
        os_versions = {"Windows": ["10.0.19045", "10.0.22631"], "iOS": ["17.6", "18.1"], "Android": ["13", "14"], "macOS": ["14.6", "15.1"]}
        self.devices = []
        for i in range(devices):
            osn = rng.choice(["Windows", "Windows", "iOS", "Android", "macOS"])
            user = f"user{i % 97:03d}@contoso-sim.example"
            self.devices.append({
                "@odata.etag": f"W/\"{i}\"", "id": _guid(f"dev{i}"), "deviceName": f"{osn[:3].upper()}-{1000 + i}",
                "managedDeviceOwnerType": rng.choice(["company", "company", "personal"]),
                "enrolledDateTime": (BASE_TIME - timedelta(days=rng.randint(30, 900))).isoformat().replace("+00:00", "Z"),
                "lastSyncDateTime": (BASE_TIME - timedelta(hours=rng.randint(1, 24 * 40))).isoformat().replace("+00:00", "Z"),
                "operatingSystem": osn, "osVersion": rng.choice(os_versions[osn]),
                "complianceState": rng.choices(["compliant", "noncompliant", "inGracePeriod", "unknown"], [0.72, 0.16, 0.07, 0.05])[0],
                "managementAgent": "mdm", "userPrincipalName": user, "userDisplayName": f"Sim User {i % 97}",
                "model": rng.choice(["Latitude 7440", "iPhone 15", "Pixel 8", "MacBook Pro", "Surface Pro 9"]),
                "manufacturer": rng.choice(["Dell", "Apple", "Google", "Microsoft"]), "serialNumber": f"SN{rng.randint(10**7, 10**8)}",
                "isEncrypted": rng.random() > 0.1, "deviceCategoryDisplayName": rng.choice(["Corporate Laptop", "Mobile", "Kiosk"]),
                "hardwareInformation": {"totalStorageSpace": rng.choice([128, 256, 512]) * 2**30,
                                        "freeStorageSpace": rng.randint(5, 100) * 2**30, "tpmVersion": rng.choice(["2.0", None])},
                "deviceActionResults": [] if rng.random() > 0.1 else [{"actionName": "syncDevice", "actionState": "done"}],
            })
        self.policies = [{"@odata.type": f"#microsoft.graph.{t}", "id": _guid(t), "displayName": n, "version": 1 + i,
                          "createdDateTime": "2024-03-01T00:00:00Z", "lastModifiedDateTime": "2025-10-01T00:00:00Z"}
                         for i, (t, n) in enumerate([("windows10CompliancePolicy", "Windows baseline"), ("iosCompliancePolicy", "iOS baseline"),
                                                     ("androidWorkProfileCompliancePolicy", "Android work profile"),
                                                     ("macOSCompliancePolicy", "macOS baseline")])]
        osmap = {"Windows": 0, "iOS": 1, "Android": 2, "macOS": 3}
        self.statuses: Dict[str, List[Dict[str, Any]]] = {p["id"]: [] for p in self.policies}
        for d in self.devices:
            p = self.policies[osmap[d["operatingSystem"]]]
            status = {"compliant": "compliant", "noncompliant": "nonCompliant", "inGracePeriod": "inGracePeriod"}.get(d["complianceState"], "unknown")
            self.statuses[p["id"]].append({"id": _guid("st" + d["id"]), "deviceDisplayName": d["deviceName"], "userName": d["userDisplayName"],
                                           "deviceModel": d["model"], "status": status, "userPrincipalName": d["userPrincipalName"],
                                           "lastReportedDateTime": d["lastSyncDateTime"]})
        self.apps = [{"id": _guid(f"app{i}"), "displayName": n, "version": f"{rng.randint(1, 30)}.{rng.randint(0, 9)}",
                      "sizeInByte": rng.randint(10**6, 10**9), "deviceCount": rng.randint(1, devices), "platform": pl}
                     for i, (n, pl) in enumerate([(f"App {chr(65 + k % 26)}{k}", rng.choice(["windows", "ios", "androidWorkProfile", "macOS"]))
                                                  for k in range(60)])]
        self.jobs: Dict[str, Dict[str, Any]] = {}

    def _page(self, items: List[Dict[str, Any]], url: httpx.URL, top_default: int = 100) -> Dict[str, Any]:
        q = parse_qs(urlparse(str(url)).query)
        top = int(q.get("$top", [top_default])[0])
        skip = int(q.get("$skiptoken", ["0"])[0])
        page = items[skip: skip + top]
        out: Dict[str, Any] = {"@odata.context": "https://graph.microsoft.com/v1.0/$metadata#mock", "value": page}
        if skip + top < len(items):
            base = str(url).split("?")[0]
            out["@odata.nextLink"] = f"{base}?$top={top}&$skiptoken={skip + top}"
        return out

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(f"{request.method} {url.path}")
        if url.host == "login.microsoftonline.com":
            form = parse_qs(request.content.decode())
            if url.path != f"/{self.tenant_id}/oauth2/v2.0/token":
                return httpx.Response(400, json={"error": "invalid_tenant", "error_description": "AADSTS90002: Tenant not found."})
            if form.get("client_id", [""])[0] != self.client_id or form.get("client_secret", [""])[0] != self.client_secret:
                return httpx.Response(401, json={"error": "invalid_client",
                                                 "error_description": "AADSTS7000215: Invalid client secret provided."})
            if form.get("scope", [""])[0] != "https://graph.microsoft.com/.default":
                return httpx.Response(400, json={"error": "invalid_scope"})
            return httpx.Response(200, json={"token_type": "Bearer", "expires_in": 3599, "access_token": self.TOKEN})
        if url.host == "download.mock":
            return httpx.Response(200, content=self._report_zip())
        if request.headers.get("authorization") != f"Bearer {self.TOKEN}":
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken", "message": "Access token is empty."}})
        path = url.path.replace("/v1.0", "", 1)
        for p, n in list(self.fail_paths.items()):
            if path.startswith(p) and n > 0:
                self.fail_paths[p] = n - 1
                return httpx.Response(500, json={"error": {"code": "InternalServerError"}})
        if path == "/deviceManagement/managedDevices":
            if self.throttle_once and "$skiptoken" in str(url):
                self.throttle_once = False
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": "TooManyRequests"}})
            return httpx.Response(200, json=self._page(self.devices, url))
        if path == "/deviceManagement/deviceCompliancePolicies":
            return httpx.Response(200, json=self._page(self.policies, url))
        if path.startswith("/deviceManagement/deviceCompliancePolicies/") and path.endswith("/deviceStatuses"):
            pid = path.split("/")[3]
            return httpx.Response(200, json=self._page(self.statuses.get(pid, []), url))
        if path == "/deviceManagement/detectedApps":
            return httpx.Response(200, json=self._page(self.apps, url))
        if path == "/deviceManagement/reports/exportJobs" and request.method == "POST":
            body = json.loads(request.content)
            jid = f"job-{len(self.jobs) + 1}"
            self.jobs[jid] = {"id": jid, "reportName": body["reportName"], "status": "notStarted", "polls": 0}
            return httpx.Response(201, json={k: v for k, v in self.jobs[jid].items() if k != "polls"})
        if path.startswith("/deviceManagement/reports/exportJobs("):
            jid = path.split("'")[1]
            job = self.jobs[jid]
            job["polls"] += 1
            if job["polls"] >= 2:
                job.update(status="completed", url="https://download.mock/report.zip")
            else:
                job["status"] = "inProgress"
            return httpx.Response(200, json={k: v for k, v in job.items() if k != "polls"})
        return httpx.Response(404, json={"error": {"code": "ResourceNotFound", "message": path}})

    def _report_zip(self) -> bytes:
        out = io.StringIO()
        out.write("DeviceName,ComplianceState,OS,LastContact\n")
        for d in self.devices[:50]:
            out.write(f"{d['deviceName']},{d['complianceState']},{d['operatingSystem']},{d['lastSyncDateTime']}\n")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("DevicesWithInventory.csv", out.getvalue())
        return buf.getvalue()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


# =============================================================================== HubSpot
class MockHubSpotAPI:
    TOKEN = "pat-mock-token"

    def __init__(self, company_names: Optional[List[str]] = None, throttle_once: bool = True, seed: int = 11,
                 oauth_client_id: str = "hs-client", oauth_client_secret: str = "hs-secret", refresh_token: str = "hs-refresh"):
        self.rng = random.Random(seed)
        self.throttle_once = throttle_once
        self.oauth = (oauth_client_id, oauth_client_secret, refresh_token)
        self.valid_tokens = {self.TOKEN}
        self.requests: List[str] = []
        self.clock = BASE_TIME
        names = list(company_names or [f"Company {i}" for i in range(25)])
        names += ["Lumio Analytics", "Brackwater Freight", "Quintessa Labs", "Harrowgate Media", "Fennick Robotics",
                  "Tallis Energy", "Mirefield Health", "Calloway Foods", "Oxbridge Data", "Pellucid Systems"]
        self.props = {
            "companies": [("name", "Company name", "string", None, True), ("domain", "Company domain", "string", None, True),
                          ("industry", "Industry", "enumeration", ["FINANCIAL_SERVICES", "HOSPITAL_HEALTH_CARE", "RETAIL", "MACHINERY",
                                                                   "GOVERNMENT_ADMINISTRATION", "COMPUTER_SOFTWARE"], True),
                          ("annualrevenue", "Annual revenue", "number", None, True),
                          ("lifecyclestage", "Lifecycle stage", "enumeration", ["lead", "opportunity", "customer"], True),
                          ("customer_tier__c", "Customer tier", "enumeration", ["Gold", "Silver", "Bronze"], False),
                          ("hs_lastmodifieddate", "Last modified date", "datetime", None, True)],
            "contacts": [("firstname", "First name", "string", None, True), ("lastname", "Last name", "string", None, True),
                         ("email", "Email", "string", None, True), ("jobtitle", "Job title", "string", None, True),
                         ("lifecyclestage", "Lifecycle stage", "enumeration", ["subscriber", "lead", "customer"], True),
                         ("lastmodifieddate", "Last modified date", "datetime", None, True)],
            "deals": [("dealname", "Deal name", "string", None, True), ("amount", "Amount", "number", None, True),
                      ("deal_currency_code", "Currency", "enumeration", ["USD", "EUR", "GBP"], True),
                      ("pipeline", "Pipeline", "enumeration", ["default", "enterprise"], True),
                      ("dealstage", "Deal stage", "enumeration", None, True), ("closedate", "Close date", "datetime", None, True),
                      ("hs_is_closed_won", "Is closed won", "bool", None, True), ("hs_lastmodifieddate", "Last modified date", "datetime", None, True),
                      ("renewal_risk__c", "Renewal risk", "enumeration", ["low", "medium", "high"], False)],
            "products": [("name", "Product name", "string", None, True), ("price", "Unit price", "number", None, True),
                         ("hs_sku", "SKU", "string", None, True), ("hs_lastmodifieddate", "Last modified date", "datetime", None, True)],
            "line_items": [("name", "Name", "string", None, True), ("quantity", "Quantity", "number", None, True),
                           ("price", "Unit price", "number", None, True), ("amount", "Net amount", "number", None, True),
                           ("hs_product_id", "Product ID", "number", None, True), ("hs_lastmodifieddate", "Last modified date", "datetime", None, True)],
            "2-900001": [("plan_name", "Plan", "enumeration", ["Starter", "Growth", "Scale"], False), ("mrr", "Monthly recurring revenue", "number", None, False),
                         ("status", "Status", "enumeration", ["active", "paused", "cancelled"], False),
                         ("hs_lastmodifieddate", "Last modified date", "datetime", None, True)],
        }
        stages = {"default": ["appointmentscheduled", "qualifiedtobuy", "contractsent", "closedwon", "closedlost"],
                  "enterprise": ["ent_discovery", "ent_proposal", "ent_closedwon", "ent_closedlost"]}
        self.pipelines = [{"id": pid, "label": pid.title(), "displayOrder": i, "archived": False,
                           "stages": [{"id": s, "label": s.replace("_", " ").title(), "displayOrder": j,
                                       "metadata": {"probability": str(1.0 if "won" in s else 0.0 if "lost" in s else round(0.2 + 0.2 * j, 1)),
                                                    "isClosed": str("closed" in s).lower()}} for j, s in enumerate(ss)]}
                          for i, (pid, ss) in enumerate(stages.items())]
        self.objects: Dict[str, List[Dict[str, Any]]] = {k: [] for k in self.props}
        nid = iter(range(1000, 10**6))

        def ts(days: int) -> str:
            return str(int((BASE_TIME - timedelta(days=days)).timestamp() * 1000))

        for n in names:
            dom = n.lower().split()[0] + ".example"
            self.objects["companies"].append(self._obj(next(nid), {"name": n, "domain": dom, "industry": self.rng.choice(self.props["companies"][2][3]),
                                                                    "annualrevenue": str(self.rng.randint(1, 900) * 10**6),
                                                                    "lifecyclestage": self.rng.choice(["lead", "opportunity", "customer"]),
                                                                    "customer_tier__c": self.rng.choice(["Gold", "Silver", "Bronze", ""]),
                                                                    "hs_lastmodifieddate": ts(self.rng.randint(5, 300))}))
        for i in range(80):
            self.objects["contacts"].append(self._obj(next(nid), {"firstname": f"Fn{i}", "lastname": f"Ln{i}", "email": f"person{i}@mail.example",
                                                                   "jobtitle": self.rng.choice(["CFO", "Controller", "IT Director", "Buyer"]),
                                                                   "lifecyclestage": self.rng.choice(["subscriber", "lead", "customer"]),
                                                                   "lastmodifieddate": ts(self.rng.randint(5, 300))}, archived=i < 3))
        for i in range(6):
            self.objects["products"].append(self._obj(next(nid), {"name": f"Product {i}", "price": str(100 * (i + 1)), "hs_sku": f"SKU-{i}",
                                                                   "hs_lastmodifieddate": ts(200)}))
        for i in range(60):
            pipe = self.rng.choice(["default", "default", "enterprise"])
            stage = self.rng.choice(stages[pipe])
            self.objects["deals"].append(self._obj(next(nid), {"dealname": f"Deal {i}", "amount": str(self.rng.randint(5, 400) * 1000),
                                                                "deal_currency_code": self.rng.choice(["USD", "USD", "EUR", "GBP"]),
                                                                "pipeline": pipe, "dealstage": stage, "closedate": ts(self.rng.randint(-90, 200)),
                                                                "hs_is_closed_won": str("won" in stage).lower(),
                                                                "renewal_risk__c": self.rng.choice(["low", "medium", "high", ""]),
                                                                "hs_lastmodifieddate": ts(self.rng.randint(5, 300))}))
        for i in range(90):
            prod = self.rng.choice(self.objects["products"])
            q = self.rng.randint(1, 20)
            self.objects["line_items"].append(self._obj(next(nid), {"name": f"LI {i}", "quantity": str(q), "price": prod["properties"]["price"],
                                                                     "amount": str(q * float(prod["properties"]["price"])), "hs_product_id": prod["id"],
                                                                     "hs_lastmodifieddate": ts(self.rng.randint(5, 300))}))
        for i in range(20):
            self.objects["2-900001"].append(self._obj(next(nid), {"plan_name": self.rng.choice(["Starter", "Growth", "Scale"]),
                                                                   "mrr": str(self.rng.randint(5, 90) * 100),
                                                                   "status": self.rng.choice(["active", "active", "paused", "cancelled"]),
                                                                   "hs_lastmodifieddate": ts(self.rng.randint(5, 300))}))
        self.assoc: Dict[tuple, List[tuple]] = {}
        comps, deals, contacts = self.objects["companies"], self.objects["deals"], self.objects["contacts"]
        for d in deals:
            c = self.rng.choice(comps)
            self.assoc.setdefault(("companies", "deals"), []).append((c["id"], d["id"], 342, "HUBSPOT_DEFINED", None))
        for k in contacts:
            c = self.rng.choice(comps)
            self.assoc.setdefault(("companies", "contacts"), []).append((c["id"], k["id"], 280, "HUBSPOT_DEFINED", None))
            if self.rng.random() < 0.15:
                self.assoc[("companies", "contacts")].append((c["id"], k["id"], 15, "USER_DEFINED", "Billing contact"))
            d = self.rng.choice(deals)
            self.assoc.setdefault(("contacts", "deals"), []).append((k["id"], d["id"], 4, "HUBSPOT_DEFINED", None))
        for li in self.objects["line_items"]:
            d = self.rng.choice(deals)
            self.assoc.setdefault(("deals", "line_items"), []).append((d["id"], li["id"], 19, "HUBSPOT_DEFINED", None))

    def _obj(self, oid: int, props: Dict[str, str], archived: bool = False) -> Dict[str, Any]:
        mod = props.get("hs_lastmodifieddate") or props.get("lastmodifieddate")
        iso = datetime.fromtimestamp(int(mod) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return {"id": str(oid), "properties": dict(props, hs_object_id=str(oid)), "createdAt": "2024-01-15T10:00:00Z",
                "updatedAt": iso, "archived": archived}

    # -------------------------------------------------- mutations used by tests
    def touch(self, obj: str, oid: str, **props: str) -> None:
        self.clock += timedelta(days=1)
        ms = str(int(self.clock.timestamp() * 1000))
        for o in self.objects[obj]:
            if o["id"] == oid:
                o["properties"].update(props)
                key = "lastmodifieddate" if obj == "contacts" else "hs_lastmodifieddate"
                o["properties"][key] = ms
                o["updatedAt"] = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def archive(self, obj: str, oid: str) -> None:
        for o in self.objects[obj]:
            if o["id"] == oid:
                o["archived"] = True

    def add_property(self, obj: str, name: str, label: str, ptype: str, values: Dict[str, str]) -> None:
        self.props[obj].append((name, label, ptype, None, False))
        for o in self.objects[obj]:
            if o["id"] in values:
                o["properties"][name] = values[o["id"]]

    # -------------------------------------------------- handler
    def _key(self, obj: str) -> str:
        return obj

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        self.requests.append(f"{request.method} {path}")
        if path == "/oauth/v1/token":
            form = parse_qs(request.content.decode())
            if (form.get("client_id", [""])[0], form.get("client_secret", [""])[0], form.get("refresh_token", [""])[0]) != self.oauth:
                return httpx.Response(400, json={"status": "BAD_REFRESH_TOKEN"})
            token = f"oauth-{len(self.valid_tokens)}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 1800, "token_type": "bearer"})
        auth = request.headers.get("authorization", "")
        if auth.replace("Bearer ", "") not in self.valid_tokens:
            return httpx.Response(401, json={"status": "error", "category": "INVALID_AUTHENTICATION",
                                             "message": "Authentication credentials not found."})
        q = parse_qs(url.query.decode() if isinstance(url.query, bytes) else str(url.query))
        if path == "/crm/v3/schemas":
            return httpx.Response(200, json={"results": [{"objectTypeId": "2-900001", "name": "subscriptions", "fullyQualifiedName": "p_mock_subscriptions",
                                                          "labels": {"singular": "Subscription", "plural": "Subscriptions"},
                                                          "primaryDisplayProperty": "plan_name"}]})
        if path.startswith("/crm/v3/properties/"):
            obj = path.rsplit("/", 1)[1]
            if obj not in self.props:
                return httpx.Response(404, json={"message": f"unknown object {obj}"})
            return httpx.Response(200, json={"results": [
                {"name": n, "label": lbl, "type": t, "fieldType": "select" if t == "enumeration" else "text",
                 "description": f"{lbl} of the {obj.rstrip('s')}", "hubspotDefined": hd,
                 "options": [{"label": o.title(), "value": o} for o in (opts or [])]} for n, lbl, t, opts, hd in self.props[obj]]})
        if path == "/crm/v3/pipelines/deals":
            return httpx.Response(200, json={"results": self.pipelines})
        if path.startswith("/crm/v4/associations/") and path.endswith("/batch/read"):
            _, _, _, _, frm, to, _, _ = path.split("/")
            ids = {i["id"] for i in json.loads(request.content)["inputs"]}
            grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            for f, t, tid, cat, label in self.assoc.get((frm, to), []):
                if f in ids:
                    grouped.setdefault(f, {}).setdefault(t, []).append({"category": cat, "typeId": tid, "label": label})
            return httpx.Response(200, json={"status": "COMPLETE", "results": [
                {"from": {"id": f}, "to": [{"toObjectId": int(t), "associationTypes": types} for t, types in tos.items()]}
                for f, tos in grouped.items()]})
        if path.startswith("/crm/v3/objects/"):
            parts = path.split("/")
            obj = parts[4]
            if obj not in self.objects:
                return httpx.Response(404, json={"message": f"unknown object {obj}"})
            items = self.objects[obj]
            if len(parts) == 5 and request.method == "GET":
                if self.throttle_once and obj == "companies":
                    self.throttle_once = False
                    return httpx.Response(429, headers={"Retry-After": "0"}, json={"status": "error", "category": "RATE_LIMITS"})
                archived = q.get("archived", ["false"])[0] == "true"
                sel = [o for o in items if o["archived"] == archived]
                return self._paged(sel, int(q.get("limit", ["100"])[0]), q.get("after", [None])[0], q.get("properties", [""])[0])
            if len(parts) == 6 and parts[5] == "search":
                body = json.loads(request.content)
                f = body["filterGroups"][0]["filters"][0]
                prop, value = f["propertyName"], int(f["value"])
                sel = sorted([o for o in items if not o["archived"] and int(o["properties"].get(prop) or 0) >= value],
                             key=lambda o: int(o["properties"].get(prop) or 0))
                resp = self._paged(sel, body.get("limit", 100), body.get("after"), ",".join(body.get("properties", [])))
                data = json.loads(resp.content)
                data["total"] = len(sel)
                return httpx.Response(200, json=data)
            if len(parts) == 7 and parts[5] == "batch" and parts[6] == "read":
                body = json.loads(request.content)
                ids = {i["id"] for i in body["inputs"]}
                return httpx.Response(200, json={"status": "COMPLETE", "results": [self._project(o, ",".join(body.get("properties", [])))
                                                                                  for o in items if o["id"] in ids]})
        return httpx.Response(404, json={"message": f"not mocked: {request.method} {path}"})

    @staticmethod
    def _project(o: Dict[str, Any], props: str) -> Dict[str, Any]:
        wanted = set(filter(None, props.split(",")))
        out = dict(o)
        out["properties"] = {k: v for k, v in o["properties"].items() if not wanted or k in wanted or k == "hs_object_id"}
        return out

    def _paged(self, items: List[Dict[str, Any]], limit: int, after: Optional[str], props: str) -> httpx.Response:
        start = int(after or 0)
        page = items[start: start + limit]
        body: Dict[str, Any] = {"results": [self._project(o, props) for o in page]}
        if start + limit < len(items):
            body["paging"] = {"next": {"after": str(start + limit), "link": "..."}}
        return httpx.Response(200, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def webhook_signature(client_secret: str, method: str, uri: str, body: str, timestamp: str) -> str:
    import base64
    import hmac

    return base64.b64encode(hmac.new(client_secret.encode(), f"{method}{uri}{body}{timestamp}".encode(), hashlib.sha256).digest()).decode()
