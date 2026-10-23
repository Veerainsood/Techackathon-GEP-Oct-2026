"""Backend-managed storage for uploaded files and generated artifacts.

Originals are stored under ``<root>/<tenant>/<kind>/<id>/`` with a JSON record holding the SHA-256, size,
content type and owner.  IDs are opaque UUIDs; every read checks tenant (and owner unless shared), and
paths are never built from user input other than the sanitized original filename.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, List, Optional

ROOT = Path(os.environ.get("BACKEND_FILE_STORE", Path(__file__).resolve().parents[2] / "data" / "file_store"))
ALLOWED_UPLOAD_EXT = {".csv", ".tsv", ".parquet", ".json", ".jsonl", ".ndjson", ".xlsx", ".xls", ".txt"}
MAX_UPLOAD_BYTES = int(os.environ.get("BACKEND_MAX_UPLOAD_MB", "500")) * 1024 * 1024


class FileAccessDenied(PermissionError):
    pass


@dataclass
class StoredFile:
    file_id: str
    kind: str  # upload | artifact
    tenant_id: str
    owner: str
    filename: str
    content_type: str
    size: int
    sha256: str
    created_at: str
    shared: bool = False
    run_id: Optional[str] = None
    origin: Optional[str] = None  # e.g. sandbox:Explorer, export:excel

    def public(self) -> dict:
        d = asdict(self)
        d["download_url"] = f"/{'files' if self.kind == 'upload' else 'artifacts'}/{self.file_id}/download"
        return d


def _safe_name(name: str) -> str:
    base = Path(name or "file").name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:120] or "file"


class FileStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or ROOT)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, tenant: str, kind: str, file_id: str) -> Path:
        return self.root / _safe_name(tenant) / kind / file_id

    def save(self, tenant: str, owner: str, filename: str, stream: BinaryIO, kind: str = "upload", content_type: str = "",
             run_id: Optional[str] = None, origin: Optional[str] = None, shared: bool = False) -> StoredFile:
        name = _safe_name(filename)
        if kind == "upload" and Path(name).suffix.lower() not in ALLOWED_UPLOAD_EXT:
            raise ValueError(f"unsupported file type {Path(name).suffix}; allowed: {sorted(ALLOWED_UPLOAD_EXT)}")
        file_id = f"{'f' if kind == 'upload' else 'a'}-{uuid.uuid4().hex[:16]}"
        d = self._dir(tenant, kind, file_id)
        d.mkdir(parents=True, exist_ok=True)
        h, size = hashlib.sha256(), 0
        tmp = d / (name + ".part")
        with tmp.open("wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    shutil.rmtree(d, ignore_errors=True)
                    raise ValueError(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                h.update(chunk)
                out.write(chunk)
        os.replace(tmp, d / name)
        rec = StoredFile(file_id=file_id, kind=kind, tenant_id=tenant, owner=owner, filename=name, content_type=content_type,
                         size=size, sha256=h.hexdigest(), created_at=datetime.now(timezone.utc).isoformat(), shared=shared,
                         run_id=run_id, origin=origin)
        (d / "record.json").write_text(json.dumps(asdict(rec)), encoding="utf-8")
        return rec

    def save_bytes(self, tenant: str, owner: str, filename: str, data: bytes, **kw) -> StoredFile:
        import io

        return self.save(tenant, owner, filename, io.BytesIO(data), **kw)

    def get(self, tenant: str, user: str, file_id: str, kind: Optional[str] = None) -> StoredFile:
        if not re.fullmatch(r"[fa]-[0-9a-f]{16}", file_id or ""):
            raise FileAccessDenied("invalid file id")
        kinds = [kind] if kind else ["upload", "artifact"]
        for k in kinds:
            p = self._dir(tenant, k, file_id) / "record.json"
            if p.exists():
                rec = StoredFile(**json.loads(p.read_text(encoding="utf-8")))
                if rec.owner != user and not rec.shared and user != "backend-runtime":
                    raise FileAccessDenied("file belongs to another user")
                return rec
        raise FileAccessDenied("file not found for this tenant")  # same error as cross-tenant: no existence oracle

    def path(self, rec: StoredFile) -> Path:
        return self._dir(rec.tenant_id, rec.kind, rec.file_id) / rec.filename

    def read_bytes(self, rec: StoredFile) -> bytes:
        data = self.path(rec).read_bytes()
        if hashlib.sha256(data).hexdigest() != rec.sha256:
            raise IOError(f"integrity check failed for {rec.file_id}")
        return data

    def list(self, tenant: str, user: str, kind: str = "upload", run_id: Optional[str] = None) -> List[StoredFile]:
        base = self.root / _safe_name(tenant) / kind
        out = []
        for p in sorted(base.glob("*/record.json")) if base.exists() else []:
            rec = StoredFile(**json.loads(p.read_text(encoding="utf-8")))
            if (rec.owner == user or rec.shared) and (run_id is None or rec.run_id == run_id):
                out.append(rec)
        return sorted(out, key=lambda r: r.created_at, reverse=True)
