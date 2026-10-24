"""Retention: stored files, exports and sandbox temp folders older than the TTL are deleted; newer ones stay."""

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from backend.runtime.retention import sweep
from backend.tools.files import FileStore


class _Workspaces:
    def __init__(self):
        self.calls = 0

    def expire_missing(self):
        self.calls += 1
        return 0


def _age(path: Path, hours: float) -> None:
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_files_older_than_the_ttl_are_deleted(tmp_path):
    store = FileStore(tmp_path / "store")
    old = store.save_bytes("default", "u1", "old.csv", b"a,b\n1,2\n")
    new = store.save_bytes("default", "u1", "new.csv", b"a,b\n3,4\n")
    rec = store.root / "default" / "upload" / old.file_id / "record.json"
    data = json.loads(rec.read_text(encoding="utf-8"))
    data["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    rec.write_text(json.dumps(data), encoding="utf-8")

    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "stale.xlsx").write_bytes(b"x" * 10)
    (exports / "fresh.xlsx").write_bytes(b"y")
    _age(exports / "stale.xlsx", 50)

    temp = Path(tempfile.mkdtemp(prefix="backend-test-retention-"))
    _age(temp, 72)

    ws = _Workspaces()
    engine = SimpleNamespace(files=store, settings=SimpleNamespace(exports_dir=str(exports)), workspaces=ws)
    out = sweep(engine, ttl_hours=48)

    assert out["files"] == 1 and out["exports"] == 1 and out["temp_dirs"] >= 1 and ws.calls == 1
    assert not (store.root / "default" / "upload" / old.file_id).exists()
    assert store.path(new).exists() and (exports / "fresh.xlsx").exists() and not (exports / "stale.xlsx").exists()
    assert not temp.exists()
    assert sweep(engine, ttl_hours=48)["files"] == 0  # idempotent
