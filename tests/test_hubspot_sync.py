"""HubSpot -> warehouse consistency: knowledge-ingestion's loader links HubSpot contacts and companies to the CRM accounts
that already represent them and keeps the customer flag consistent with both HubSpot and finance data.

Runs against an isolated copy of the synthetic warehouse (database ``backend_crm_sync_lab``); HubSpot is faked."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from unittest import mock

import psycopg2
import pytest

from backend.events.contracts import SyncEvent

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lab(generator, tmp_path_factory):
    from backend import pg_embedded
    from backend.synthetic.loader import load_to_postgres

    uri = pg_embedded.ensure_database("backend_crm_sync_lab")
    with psycopg2.connect(uri) as c, c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS hubspot CASCADE")
    load_to_postgres(generator.frames, uri)
    url = uri.replace("postgresql://", "postgresql+psycopg://")
    # knowledge-ingestion reads its configuration at import time; point it at the lab database.
    with mock.patch.dict(os.environ, {"DATABASE_URL": url, "HUBSPOT_PORTAL_ID": "test", "BACKEND_EVENTS_URL": ""}):
        sys.path.insert(0, str(ROOT / "knowledge-ingestion"))
        from knowledge_ingestion.storage.warehouse import Warehouse

        w = Warehouse(url)
        w.prepare()
    return {"w": w, "uri": uri, "url": url, "tmp": tmp_path_factory.mktemp("ki")}


def contact(cid, company, stage):
    from knowledge_ingestion.connectors.hubspot.client import HubSpotRecord

    return HubSpotRecord(cid, {"email": f"{cid}@example.com", "company": company, "lifecyclestage": stage,
                               "lastmodifieddate": "2026-10-24T00:00:00Z"})


def q(lab, sql, params=()):
    with psycopg2.connect(lab["uri"]) as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def account_of_group(lab, group_name):
    return q(lab, """SELECT min(a.crm_account_id) FROM pipeline_crm.prospect_accounts a
                     JOIN master_ref.client_conglomerates g USING (conglomerate_id) WHERE g.conglomerate_name = %s""",
             (group_name,))[0][0]


def flag(lab, account_id):
    return q(lab, "SELECT is_existing_customer FROM pipeline_crm.prospect_accounts WHERE crm_account_id = %s", (account_id,))[0][0]


def load(lab, *records, companies=()):
    from datetime import datetime, timezone

    return lab["w"].load("test-batch", {"contacts": list(records), "companies": list(companies)}, datetime.now(timezone.utc))


def test_contacts_link_to_the_accounts_that_already_represent_their_company(lab):
    accounts_before = q(lab, "SELECT count(*) FROM pipeline_crm.prospect_accounts")[0][0]
    changes = load(lab, contact("c1", "Contoso Logistics", "customer"), contact("c2", "LITWARE PVT. LTD.", "opportunity"),
                   contact("c3", "Fabrikam Retail", "lead"))
    linked = dict(q(lab, "SELECT contact_id, crm_account_id FROM hubspot.contacts WHERE contact_id IN ('c1','c2','c3')"))
    fabrikam = q(lab, "SELECT crm_account_id FROM pipeline_crm.prospect_accounts WHERE crm_account_name = 'Fabrikam Retail'")[0][0]
    assert linked == {"c1": account_of_group(lab, "Contoso Logistics"), "c2": account_of_group(lab, "Litware Pvt Ltd"), "c3": fabrikam}
    assert q(lab, "SELECT count(*) FROM pipeline_crm.prospect_accounts")[0][0] == accounts_before  # no duplicates
    assert flag(lab, linked["c1"]) is True and flag(lab, fabrikam) is False
    assert changes == {}  # every flag already agreed with HubSpot and the contracts


def test_unknown_company_becomes_one_prospect_and_is_reused(lab):
    first = load(lab, contact("t1", "Tailspin Toys", "lead"))
    second = load(lab, contact("t2", "Tailspin Toys Inc.", "lead"))
    rows = q(lab, "SELECT crm_account_id, is_existing_customer FROM pipeline_crm.prospect_accounts WHERE crm_account_name LIKE 'Tailspin%%'")
    assert rows == [("HSN-tailspin-toys", False)] and first == {"HSN-tailspin-toys": "insert"} and second == {}


def test_lifecycle_change_to_customer_flips_the_flag_and_is_reported(lab):
    fabrikam = q(lab, "SELECT crm_account_id FROM pipeline_crm.prospect_accounts WHERE crm_account_name = 'Fabrikam Retail'")[0][0]
    assert load(lab, contact("c3", "Fabrikam Retail", "customer")) == {fabrikam: "update"} and flag(lab, fabrikam) is True
    assert load(lab, contact("c3", "Fabrikam Retail", "lead")) == {fabrikam: "update"} and flag(lab, fabrikam) is False


def test_signed_contract_keeps_an_account_a_customer(lab):
    contoso = account_of_group(lab, "Contoso Logistics")
    assert load(lab, contact("c1", "Contoso Logistics", "lead")) == {} and flag(lab, contoso) is True


def test_company_object_links_by_legal_alias_and_hubspot_owns_the_name(lab):
    from knowledge_ingestion.connectors.hubspot.client import HubSpotRecord

    contoso = account_of_group(lab, "Contoso Logistics")
    load(lab, companies=[HubSpotRecord("9001", {"name": "Contoso Inc."})])
    assert q(lab, "SELECT crm_account_id, matched_by FROM hubspot.company_links WHERE hubspot_company_id = '9001'") == [(contoso, "alias")]
    assert load(lab, companies=[HubSpotRecord("9001", {"name": "Contoso Logistics"})]) == {contoso: "update"}
    assert q(lab, "SELECT crm_account_name FROM pipeline_crm.prospect_accounts WHERE crm_account_id = %s", (contoso,)) == [("Contoso Logistics",)]


def test_sync_event_names_crm_account_ids_and_matches_the_backend_contract(lab):
    from knowledge_ingestion.config import BackendConfig, config
    from knowledge_ingestion.pipeline import hubspot_sync
    from knowledge_ingestion.publish import backend_events
    from knowledge_ingestion.storage import sql_store

    store = sql_store.SqlStore(f"sqlite:///{lab['tmp'] / 'store.db'}")
    sql_store.metadata.create_all(store._engine)
    records = {"contacts": [contact("w1", "Woodgrove Bank", "customer"), contact("n1", "Northwind Traders", "customer"),
                            contact("f9", "Fabrikam Retail", "customer")], "companies": []}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def search_modified_since(self, object_type, properties, modified_property, since_ms):
            return records[object_type]

    cfg = replace(config, backend=BackendConfig(events_url="http://127.0.0.1:9", secret="s", tenant_id="default",
                                          source_id="hubspot", database_id=None))
    with mock.patch.object(hubspot_sync, "config", cfg), mock.patch.object(backend_events, "config", cfg), \
            mock.patch.object(hubspot_sync, "HubSpotClient", FakeClient), \
            mock.patch.object(hubspot_sync, "get_warehouse", lambda: lab["w"]):
        hubspot_sync.sync_hubspot(store)
    events = [SyncEvent.model_validate(json.loads(e["body"])) for e in store.pending_outbox("hubspot", 10)]
    assert len(events) == 1
    by_name = {c.name: [list(ch.key.values())[0] for ch in c.changes] for c in events[0].collections}
    fabrikam = q(lab, "SELECT crm_account_id FROM pipeline_crm.prospect_accounts WHERE crm_account_name = 'Fabrikam Retail'")[0][0]
    assert sorted(by_name["hubspot.contacts"]) == ["f9", "n1", "w1"]
    assert by_name["pipeline_crm.prospect_accounts"] == [fabrikam]  # Fabrikam became a customer; the others already were
