"""Load HubSpot records into the techackathon warehouse tables (PostgreSQL).

The techackathon DDL (sql/postgres_ddl.sql) must be applied to the database first.
This module maps:

- HubSpot contacts  -> hubspot.contacts (a new schema, outside the 39 techackathon tables)
- HubSpot companies -> pipeline_crm.prospect_accounts
- each sync run     -> data_ops.ingestion_batches

Keeping the CRM and finance data consistent:
- A HubSpot company or a contact's company is linked to the CRM account that already represents it,
  matched by account name, customer group name or verified counterparty alias (legal suffixes such as
  "Inc." or "Pvt Ltd" are ignored). Only companies with no match become new prospect accounts.
- HubSpot owns the account name and the customer lifecycle; finance owns invoices and contracts.
  is_existing_customer is true when HubSpot has a contact at the "customer" stage or the account's customer
  group has a signed contract, so the two systems never disagree about who is a customer.

HubSpot has no value for several required warehouse columns (region, industry, segment, owner). New
accounts get UNKNOWN for those and point to a placeholder sales rep, so the foreign keys stay valid.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, String, Table, bindparam, insert, inspect, select, text, update
from sqlalchemy.engine import Connection, Engine

from knowledge_ingestion.config import config
from knowledge_ingestion.storage.sql_store import create_db_engine

UNKNOWN = "UNKNOWN"
UNASSIGNED_SQUAD_ID = "SQUAD_UNASSIGNED"
UNASSIGNED_REP_ID = "REP_UNASSIGNED"
UPSTREAM_CONNECTOR = "CRM_PIPELINEHUB"
TARGET_SCHEMAS = "hubspot,pipeline_crm"

# Tables that must already exist from the techackathon DDL.
REQUIRED_TABLES = [
    ("pipeline_crm", "prospect_accounts"),
    ("pipeline_crm", "quota_carriers"),
    ("pipeline_crm", "territory_squads"),
    ("data_ops", "ingestion_batches"),
    ("master_ref", "client_conglomerates"),
    ("agreements", "counterparty_aliases"),
    ("agreements", "agreement_files"),
]
CUSTOMER_STAGE = "customer"
SIGNED_STATUSES = ("Fully_Signed", "Expired", "Terminated")  # the customer has signed at some point
# Words that only describe the legal form; "Litware Pvt Ltd" and "LITWARE PTE. LTD." are the same company.
LEGAL_WORDS = {"inc", "incorporated", "ltd", "limited", "llc", "plc", "pvt", "pte", "private", "gmbh", "bv", "co",
               "corp", "corporation", "company", "ag", "sa", "the"}
AccountChanges = dict[str, str]
"""crm_account_id -> 'insert' or 'update', for every prospect account a load created or changed."""


def normalize_company(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", name.lower())
    core = [w for w in words if w not in LEGAL_WORDS]
    return " ".join(core or words)

hubspot_metadata = MetaData(schema="hubspot")

contacts = Table(
    "contacts",
    hubspot_metadata,
    Column("contact_id", String(32), primary_key=True),
    Column("email", String(254), nullable=True),
    Column("firstname", String(120), nullable=True),
    Column("lastname", String(120), nullable=True),
    Column("company", String(160), nullable=True),
    Column("phone", String(40), nullable=True),
    Column("jobtitle", String(160), nullable=True),
    Column("lifecyclestage", String(60), nullable=True),
    Column("hubspot_created_at", DateTime, nullable=True),
    Column("hubspot_modified_at", DateTime, nullable=True),
    Column("synced_at", DateTime, nullable=False),
    Column("batch_id", String(32), nullable=False),
    Column("crm_account_id", String(32), nullable=True),  # the contact's company in pipeline_crm.prospect_accounts
)

company_links = Table(
    "company_links",
    hubspot_metadata,
    Column("hubspot_company_id", String(32), primary_key=True),
    Column("crm_account_id", String(32), nullable=False),
    Column("matched_by", String(16), nullable=False),  # account_name, group_name, alias or created
    Column("linked_at", DateTime, nullable=False),
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _naive_utc(value: Any) -> datetime | None:
    """Return a naive UTC datetime. HubSpot sends ISO strings, or epoch milliseconds as text."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _text(value)
        if raw == "":
            return None
        if raw.isdigit():
            parsed = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed  # already naive, treated as UTC
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


class Warehouse:
    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_db_engine(database_url)
        self._check_required_tables()
        self._squads = Table("territory_squads", MetaData(), schema="pipeline_crm", autoload_with=self._engine)
        self._reps = Table("quota_carriers", MetaData(), schema="pipeline_crm", autoload_with=self._engine)
        self._accounts = Table("prospect_accounts", MetaData(), schema="pipeline_crm", autoload_with=self._engine)
        self._batches = Table("ingestion_batches", MetaData(), schema="data_ops", autoload_with=self._engine)

    def _check_required_tables(self) -> None:
        inspector = inspect(self._engine)
        missing = [f"{schema}.{name}" for schema, name in REQUIRED_TABLES if not inspector.has_table(name, schema=schema)]
        if missing:
            raise RuntimeError(
                "The techackathon schema is not in this database (missing: "
                + ", ".join(missing)
                + "). Apply sql/postgres_ddl.sql first."
            )

    def prepare(self) -> AccountChanges:
        """Create the hubspot tables and the placeholder squad and rep, then link contacts stored before linking existed.

        Returns the accounts the backfill changed.
        """
        with self._engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS hubspot"))
        hubspot_metadata.create_all(self._engine)
        with self._engine.begin() as conn:
            conn.execute(text("ALTER TABLE hubspot.contacts ADD COLUMN IF NOT EXISTS crm_account_id VARCHAR(32)"))
        with self._engine.begin() as conn:
            if conn.execute(select(self._squads.c.squad_id).where(self._squads.c.squad_id == UNASSIGNED_SQUAD_ID)).first() is None:
                conn.execute(
                    insert(self._squads).values(
                        squad_id=UNASSIGNED_SQUAD_ID,
                        parent_squad_id=None,
                        squad_name="Unassigned (HubSpot import)",
                        theater_region=UNKNOWN,
                        hierarchy_depth=1,
                    )
                )
            if conn.execute(select(self._reps.c.rep_id).where(self._reps.c.rep_id == UNASSIGNED_REP_ID)).first() is None:
                conn.execute(
                    insert(self._reps).values(
                        rep_id=UNASSIGNED_REP_ID,
                        rep_full_name="Unassigned (HubSpot import)",
                        squad_id=UNASSIGNED_SQUAD_ID,
                        manager_rep_id=None,
                        role_title="UNASSIGNED",
                        annual_quota_usd=0,
                        active_status=False,
                    )
                )
        return self.relink()

    def relink(self) -> AccountChanges:
        """Link stored contacts that have no account yet, and refresh the customer flag of every linked account."""
        changes: AccountChanges = {}
        with self._engine.begin() as conn:
            index = self._account_index(conn)
            unlinked = conn.execute(
                select(contacts.c.contact_id, contacts.c.company).where(
                    contacts.c.crm_account_id.is_(None), contacts.c.company.is_not(None)
                )
            ).all()
            for contact_id, company in unlinked:
                account = self._account_for_company(conn, company, index, changes)
                conn.execute(update(contacts).where(contacts.c.contact_id == contact_id).values(crm_account_id=account))
            linked = conn.execute(select(contacts.c.crm_account_id).where(contacts.c.crm_account_id.is_not(None)).distinct())
            self._sync_customer_flags(conn, {row[0] for row in linked}, changes)
        return changes

    def load(self, batch_id: str, records: dict[str, list[Any]], synced_at: datetime) -> AccountChanges:
        """Insert or update the changed records in one transaction. Returns the prospect accounts it changed."""
        synced = _naive_utc(synced_at)
        changes: AccountChanges = {}
        affected: set[str] = set()
        with self._engine.begin() as conn:
            index = self._account_index(conn)
            for item in records.get("companies", []):
                affected.add(self._upsert_account(conn, item, index, changes))
            for item in records.get("contacts", []):
                affected.update(self._upsert_contact(conn, item, batch_id, synced, index, changes))
            self._sync_customer_flags(conn, affected - {None}, changes)
        return changes

    # ------------------------------------------------------------------ linking
    def _account_index(self, conn: Connection) -> dict[str, tuple[str, str]]:
        """normalized company name -> (crm_account_id, how it matched). Account names win over groups and aliases."""
        accounts = conn.execute(
            select(self._accounts.c.crm_account_id, self._accounts.c.crm_account_name, self._accounts.c.conglomerate_id)
        ).all()
        primary_by_group: dict[str, str] = {}
        for account_id, _name, group_id in sorted(accounts, key=lambda r: r[0]):
            if group_id and group_id not in primary_by_group:
                primary_by_group[group_id] = account_id
        index: dict[str, tuple[str, str]] = {}
        # Accounts tied to a customer group first, then CRM-only prospects; lowest id wins among duplicates.
        for account_id, name, _group_id in sorted(accounts, key=lambda r: (r[2] is None, r[0])):
            index.setdefault(normalize_company(name), (account_id, "account_name"))
        groups = conn.execute(text("SELECT conglomerate_id, conglomerate_name FROM master_ref.client_conglomerates"))
        aliases = conn.execute(
            text("SELECT conglomerate_id, alias_name FROM agreements.counterparty_aliases WHERE is_verified_match")
        )
        for rows, how in ((groups, "group_name"), (aliases, "alias")):
            for group_id, name in rows:
                if group_id in primary_by_group:
                    index.setdefault(normalize_company(name), (primary_by_group[group_id], how))
        return index

    def _account_for_company(
        self, conn: Connection, company: str, index: dict[str, tuple[str, str]], changes: AccountChanges
    ) -> str:
        """The account for a contact's company name, creating a prospect when no account represents it."""
        key = normalize_company(company)
        if key in index:
            return index[key][0]
        account_id = ("HSN-" + re.sub(r"[^a-z0-9]+", "-", key).strip("-"))[:32]
        self._insert_prospect(conn, account_id, company)
        index[key] = (account_id, "created")
        changes[account_id] = "insert"
        return account_id

    def _insert_prospect(self, conn: Connection, account_id: str, name: str) -> None:
        conn.execute(
            insert(self._accounts).values(
                crm_account_id=account_id,
                conglomerate_id=None,
                crm_account_name=name,
                crm_region_raw=UNKNOWN,
                crm_industry_raw=UNKNOWN,
                market_segment=UNKNOWN,
                account_owner_rep_id=UNASSIGNED_REP_ID,
                is_existing_customer=False,
            )
        )

    def _sync_customer_flags(self, conn: Connection, account_ids: set[str], changes: AccountChanges) -> None:
        """is_existing_customer = a contact is at the customer stage in HubSpot, or the group has a signed contract."""
        if not account_ids:
            return
        rows = conn.execute(
            text(
                """
                SELECT a.crm_account_id, a.is_existing_customer,
                       EXISTS (SELECT 1 FROM hubspot.contacts c
                               WHERE c.crm_account_id = a.crm_account_id AND lower(c.lifecyclestage) = :stage)
                       OR EXISTS (SELECT 1 FROM agreements.agreement_files f
                                  WHERE f.matched_group_id = a.conglomerate_id AND f.execution_status IN :signed)
                FROM pipeline_crm.prospect_accounts a WHERE a.crm_account_id IN :ids
                """
            ).bindparams(bindparam("signed", expanding=True), bindparam("ids", expanding=True)),
            {"stage": CUSTOMER_STAGE, "signed": list(SIGNED_STATUSES), "ids": sorted(account_ids)},
        ).all()
        for account_id, current, wanted in rows:
            if current != wanted:
                conn.execute(
                    update(self._accounts)
                    .where(self._accounts.c.crm_account_id == account_id)
                    .values(is_existing_customer=wanted)
                )
                changes.setdefault(account_id, "update")

    def _upsert_contact(
        self,
        conn: Connection,
        item: Any,
        batch_id: str,
        synced: datetime | None,
        index: dict[str, tuple[str, str]],
        changes: AccountChanges,
    ) -> set[str | None]:
        """Insert or update a contact linked to its company's account. Returns the old and new account ids."""
        properties = item.properties or {}
        values = {
            "email": _text(properties.get("email")) or None,
            "firstname": _text(properties.get("firstname")) or None,
            "lastname": _text(properties.get("lastname")) or None,
            "company": _text(properties.get("company")) or None,
            "phone": _text(properties.get("phone")) or None,
            "jobtitle": _text(properties.get("jobtitle")) or None,
            "lifecyclestage": _text(properties.get("lifecyclestage")) or None,
            "hubspot_created_at": _naive_utc(properties.get("createdate")),
            "hubspot_modified_at": _naive_utc(properties.get("lastmodifieddate")),
            "synced_at": synced,
            "batch_id": batch_id,
        }
        values["crm_account_id"] = (
            self._account_for_company(conn, values["company"], index, changes) if values["company"] else None
        )
        previous = conn.execute(select(contacts.c.crm_account_id).where(contacts.c.contact_id == item.id)).first()
        if previous:
            conn.execute(update(contacts).where(contacts.c.contact_id == item.id).values(**values))
        else:
            conn.execute(insert(contacts).values(contact_id=item.id, **values))
        return {values["crm_account_id"], previous[0] if previous else None}

    def _upsert_account(
        self, conn: Connection, item: Any, index: dict[str, tuple[str, str]], changes: AccountChanges
    ) -> str:
        """Link a HubSpot company to its account (creating a prospect if none matches). Returns the account id."""
        properties = item.properties or {}
        name = _text(properties.get("name")) or UNKNOWN
        account_id = conn.execute(
            select(company_links.c.crm_account_id).where(company_links.c.hubspot_company_id == item.id)
        ).scalar()
        if account_id is None:
            match = index.get(normalize_company(name)) if name != UNKNOWN else None
            if match:
                account_id, matched_by = match
            else:
                account_id, matched_by = item.id, "created"
                self._insert_prospect(conn, account_id, name)
                index[normalize_company(name)] = (account_id, matched_by)
                changes[account_id] = "insert"
            conn.execute(
                insert(company_links).values(
                    hubspot_company_id=item.id,
                    crm_account_id=account_id,
                    matched_by=matched_by,
                    linked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
        # HubSpot owns the account name. Owner and segment may be set in the warehouse later, so they are left alone.
        renamed = conn.execute(
            update(self._accounts)
            .where(self._accounts.c.crm_account_id == account_id, self._accounts.c.crm_account_name != name)
            .values(crm_account_name=name)
        ).rowcount
        if renamed and name != UNKNOWN:
            changes.setdefault(account_id, "update")
        return account_id

    def log_batch(
        self,
        batch_id: str,
        started_at: datetime,
        completed_at: datetime,
        status: str,
        extracted: int,
        loaded: int,
        error: str | None,
    ) -> None:
        """Record one sync run in data_ops.ingestion_batches. status is SUCCEEDED or FAILED."""
        with self._engine.begin() as conn:
            conn.execute(
                insert(self._batches).values(
                    batch_id=batch_id,
                    upstream_connector=UPSTREAM_CONNECTOR,
                    target_schema_name=TARGET_SCHEMAS,
                    run_status=status,
                    records_extracted=extracted,
                    records_loaded=loaded,
                    rejected_rows_count=0,
                    error_diagnostic=error,
                    started_at=_naive_utc(started_at),
                    completed_at=_naive_utc(completed_at),
                )
            )


@lru_cache(maxsize=1)
def get_warehouse() -> Warehouse:
    """Create the warehouse connection once per process and prepare its tables."""
    warehouse = Warehouse(config.database_url)
    warehouse.prepare()
    return warehouse
