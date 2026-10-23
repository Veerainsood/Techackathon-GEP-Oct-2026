"""Returns the knowledge store for the database named in DATABASE_URL.

The pipeline and API call get_store() and never depend on a specific database.
Any database SQLAlchemy supports can be used once its driver is installed and
listed in SUPPORTED_DRIVERS (config.py).
"""

from __future__ import annotations

from functools import lru_cache

from knowledge_ingestion.config import config
from knowledge_ingestion.storage.base import KnowledgeStore
from knowledge_ingestion.storage.sql_store import SqlStore


@lru_cache(maxsize=1)
def get_store() -> KnowledgeStore:
    """Create the store once per process and make sure its tables exist."""
    store = SqlStore(config.database_url)
    store.init_schema()
    return store
