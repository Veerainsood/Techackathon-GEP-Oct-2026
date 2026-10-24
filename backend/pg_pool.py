"""One connection pool per database per process, shared by the runtime stores (conversations, governance, events, ...)."""

from __future__ import annotations

import threading
from typing import Dict

from psycopg2.pool import ThreadedConnectionPool

_POOLS: Dict[str, ThreadedConnectionPool] = {}
_LOCK = threading.Lock()


def shared_pool(uri: str, maxconn: int = 40) -> ThreadedConnectionPool:
    with _LOCK:
        pool = _POOLS.get(uri)
        if pool is None or pool.closed:
            pool = _POOLS[uri] = ThreadedConnectionPool(1, maxconn, uri, connect_timeout=5)
        return pool
