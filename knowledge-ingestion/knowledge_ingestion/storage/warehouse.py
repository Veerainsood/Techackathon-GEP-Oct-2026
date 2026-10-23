"""Load HubSpot records into the techackathon warehouse tables (PostgreSQL).

The techackathon DDL (sql/postgres_ddl.sql) must be applied to the database first.
This module maps:

- HubSpot contacts  -> hubspot.contacts (a new schema, outside the 39 techackathon tables)
- HubSpot companies -> pipeline_crm.prospect_accounts
- each sync run     -> data_ops.ingestion_batches

HubSpot has no value for several required warehouse columns (region, industry, segment,
owner, existing-customer flag). Those are filled with UNKNOWN, and new accounts point to a
placeholder sales rep, so the foreign keys stay valid.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, String, Table, insert, inspect, select, text, update
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
]

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

    def prepare(self) -> None:
        """Create the hubspot schema and contacts table, and the placeholder squad and rep."""
        with self._engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS hubspot"))
        hubspot_metadata.create_all(self._engine)
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

    def load(self, batch_id: str, records: dict[str, list[Any]], synced_at: datetime) -> None:
        """Insert or update the changed records. All writes happen in one transaction."""
        synced = _naive_utc(synced_at)
        with self._engine.begin() as conn:
            for item in records.get("contacts", []):
                self._upsert_contact(conn, item, batch_id, synced)
            for item in records.get("companies", []):
                self._upsert_account(conn, item)

    def _upsert_contact(self, conn: Connection, item: Any, batch_id: str, synced: datetime | None) -> None:
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
        exists = conn.execute(select(contacts.c.contact_id).where(contacts.c.contact_id == item.id)).first()
        if exists:
            conn.execute(update(contacts).where(contacts.c.contact_id == item.id).values(**values))
        else:
            conn.execute(insert(contacts).values(contact_id=item.id, **values))

    def _upsert_account(self, conn: Connection, item: Any) -> None:
        properties = item.properties or {}
        name = _text(properties.get("name")) or UNKNOWN
        exists = conn.execute(
            select(self._accounts.c.crm_account_id).where(self._accounts.c.crm_account_id == item.id)
        ).first()
        if exists:
            # Only the name is refreshed. Owner and segment may be set in the warehouse later, so they are left alone.
            conn.execute(
                update(self._accounts).where(self._accounts.c.crm_account_id == item.id).values(crm_account_name=name)
            )
        else:
            conn.execute(
                insert(self._accounts).values(
                    crm_account_id=item.id,
                    conglomerate_id=None,
                    crm_account_name=name,
                    crm_region_raw=UNKNOWN,
                    crm_industry_raw=UNKNOWN,
                    market_segment=UNKNOWN,
                    account_owner_rep_id=UNASSIGNED_REP_ID,
                    is_existing_customer=False,
                )
            )

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
