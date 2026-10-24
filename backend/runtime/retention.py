"""Retention: files the server writes are deleted after a time to live (default 48 hours).

Covered: the file store (uploads, cleaned versions, sandbox outputs and saved exports), the exports folder and temp
folders left by local sandbox runs. Workspaces built on files that were removed are closed, so their knowledge entries go
too. Conversations, approvals and findings are database rows and stay; a conversation whose files expired asks for them
to be attached again. Runs in the background every BACKEND_RETENTION_SWEEP_S seconds (default hourly).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("backend.retention")
TTL_HOURS = float(os.environ.get("BACKEND_FILE_TTL_HOURS", "48"))
SWEEP_S = float(os.environ.get("BACKEND_RETENTION_SWEEP_S", "3600"))
TEMP_PREFIX = "backend-"  # LocalProcessSandbox folders


def _created(d: Path) -> float:
    """When a stored file was created: its record's timestamp, else the folder's modification time."""
    rec = d / "record.json"
    try:
        return datetime.fromisoformat(json.loads(rec.read_text(encoding="utf-8"))["created_at"]).timestamp()
    except Exception:
        return d.stat().st_mtime


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def sweep(engine: Any, ttl_hours: float = TTL_HOURS, now: Optional[float] = None) -> Dict[str, Any]:
    cutoff = (now or time.time()) - ttl_hours * 3600
    out = {"files": 0, "exports": 0, "temp_dirs": 0, "workspaces": 0, "bytes": 0}
    store = getattr(engine, "files", None)
    if store is not None and store.root.exists():
        for d in store.root.glob("*/*/*"):  # <tenant>/<kind>/<file id>
            if d.is_dir() and _created(d) < cutoff:
                out["bytes"] += _size(d)
                shutil.rmtree(d, ignore_errors=True)
                out["files"] += 1
    exports = Path(engine.settings.exports_dir)
    if exports.exists():
        for f in exports.iterdir():
            if f.stat().st_mtime < cutoff:
                out["bytes"] += _size(f)
                shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
                out["exports"] += 1
    for d in Path(tempfile.gettempdir()).glob(f"{TEMP_PREFIX}*"):
        if d.is_dir() and d.stat().st_mtime < cutoff:
            out["bytes"] += _size(d)
            shutil.rmtree(d, ignore_errors=True)
            out["temp_dirs"] += 1
    workspaces = getattr(engine, "workspaces", None)
    if workspaces is not None and store is not None:
        out["workspaces"] = workspaces.expire_missing()
    if any(out[k] for k in ("files", "exports", "temp_dirs", "workspaces")):
        log.info("retention removed %s (%.1f MB)", {k: v for k, v in out.items() if k != "bytes"}, out["bytes"] / 1e6)
    return out


_started = False


def start(engine: Any, every_s: float = SWEEP_S) -> None:
    global _started
    if _started or every_s <= 0:
        return
    _started = True

    def loop() -> None:
        time.sleep(30)
        while True:
            try:
                sweep(engine)
            except Exception:
                log.exception("retention sweep failed; retried next time")
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="retention").start()
