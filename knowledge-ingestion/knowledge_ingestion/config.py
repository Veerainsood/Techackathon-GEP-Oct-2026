"""Central, validated configuration loaded from environment variables and .env.

Secrets are read here only and are never printed or logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

load_dotenv()

# SQLAlchemy driver names the knowledge store is tested with.
# To support another database, install its driver and add its name here.
SUPPORTED_DRIVERS = {"postgresql+psycopg", "mssql+pyodbc", "sqlite"}

MIN_POLL_INTERVAL_SECONDS = 30


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class HubSpotConfig:
    portal_id: str
    access_token: str


@dataclass(frozen=True)
class PollingConfig:
    enabled: bool
    interval_seconds: int


@dataclass(frozen=True)
class BackendConfig:
    """Where to send backend.sync-event.v1 events. Publishing is off unless events_url is set."""

    events_url: str | None
    secret: str
    tenant_id: str
    source_id: str
    database_id: str | None

    @property
    def enabled(self) -> bool:
        return self.events_url is not None


@dataclass(frozen=True)
class Config:
    hubspot: HubSpotConfig
    database_url: str
    polling: PollingConfig
    backend: BackendConfig


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required (set it in .env)")
    return value


def _validate_database_url(database_url: str) -> str:
    try:
        driver = make_url(database_url).drivername
    except ArgumentError as error:
        raise ConfigError(f"DATABASE_URL is not a valid SQLAlchemy URL: {error}") from error
    if driver not in SUPPORTED_DRIVERS:
        raise ConfigError(
            f"DATABASE_URL driver '{driver}' is not supported. Use one of: {', '.join(sorted(SUPPORTED_DRIVERS))}"
        )
    return database_url


def _load_polling() -> PollingConfig:
    enabled = os.getenv("POLL_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
    raw_interval = os.getenv("POLL_INTERVAL_SECONDS", "300").strip()
    try:
        interval = int(raw_interval)
    except ValueError as error:
        raise ConfigError("POLL_INTERVAL_SECONDS must be a whole number of seconds") from error
    if interval < MIN_POLL_INTERVAL_SECONDS:
        raise ConfigError(f"POLL_INTERVAL_SECONDS must be at least {MIN_POLL_INTERVAL_SECONDS}")
    return PollingConfig(enabled=enabled, interval_seconds=interval)


def _load_backend() -> BackendConfig:
    url = os.getenv("BACKEND_EVENTS_URL", "").strip().rstrip("/") or None
    secret = os.getenv("BACKEND_EVENT_SECRET", "").strip()
    if url is not None and not secret:
        raise ConfigError("BACKEND_EVENT_SECRET is required when BACKEND_EVENTS_URL is set (must match the backend service)")
    return BackendConfig(
        events_url=url,
        secret=secret,
        tenant_id=os.getenv("BACKEND_TENANT_ID", "default").strip() or "default",
        source_id=os.getenv("BACKEND_SOURCE_ID", "hubspot").strip() or "hubspot",
        database_id=os.getenv("BACKEND_DATABASE_ID", "").strip() or None,
    )


def load_config() -> Config:
    database_url = _validate_database_url(_require("DATABASE_URL"))

    return Config(
        hubspot=HubSpotConfig(
            portal_id=_require("HUBSPOT_PORTAL_ID"),
            # Token may be empty at config time; the HubSpot client checks it before calling the API.
            access_token=os.getenv("HUBSPOT_ACCESS_TOKEN", "").strip(),
        ),
        database_url=database_url,
        polling=_load_polling(),
        backend=_load_backend(),
    )


config = load_config()
