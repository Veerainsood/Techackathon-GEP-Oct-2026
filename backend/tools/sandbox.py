"""Sandboxed Python computation for agents (Daytona, or an explicitly enabled local process).

* Providers: ``daytona`` (Daytona SDK; requires DAYTONA_API_KEY), ``local`` (subprocess in a temp
  workspace -- NOT a security boundary, for development only), ``off`` (default; no sandbox tool).
* Sandboxes are created lazily per (run, agent): a simple SQL question never provisions one, and
  Explorer and Hercules never share a writable workspace.
* Files are never assumed present: ``ensure_file`` uploads the stored bytes and verifies SHA-256 inside the
  sandbox before code can use them.  Files written to ``out/`` are collected as artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.tools.files import FileStore, StoredFile


class SandboxError(RuntimeError):
    pass


class Sandbox(ABC):
    provider = "abstract"
    workdir = "/home/daytona/workspace"

    @abstractmethod
    def upload(self, rel_path: str, data: bytes) -> str: ...

    @abstractmethod
    def sha256(self, path: str) -> str: ...

    @abstractmethod
    def run_python(self, code: str, timeout_s: int = 120) -> Dict[str, Any]: ...

    @abstractmethod
    def list_outputs(self) -> List[str]: ...

    @abstractmethod
    def download(self, path: str) -> bytes: ...

    @abstractmethod
    def close(self) -> None: ...


class LocalProcessSandbox(Sandbox):
    """Development fallback: isolated working directory and process, but shares the host -- not a security sandbox."""

    provider = "local"

    def __init__(self, label: str):
        self.root = Path(tempfile.mkdtemp(prefix=f"backend-{label}-"))
        (self.root / "data").mkdir()
        (self.root / "out").mkdir()
        self.workdir = str(self.root)

    def upload(self, rel_path: str, data: bytes) -> str:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def sha256(self, path: str) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def run_python(self, code: str, timeout_s: int = 120) -> Dict[str, Any]:
        script = self.root / f"_run_{int(time.time() * 1000)}.py"
        script.write_text(code, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "URI"))}
        try:
            p = subprocess.run([sys.executable, "-I", str(script)], cwd=self.root, capture_output=True, text=True, timeout=timeout_s, env=env)
            return {"exit_code": p.returncode, "stdout": p.stdout[-20000:], "stderr": p.stderr[-8000:]}
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": f"timed out after {timeout_s}s"}

    def list_outputs(self) -> List[str]:
        return [str(p) for p in (self.root / "out").rglob("*") if p.is_file()]

    def download(self, path: str) -> bytes:
        p = Path(path).resolve()
        if self.root.resolve() not in p.parents:
            raise SandboxError("path outside sandbox")
        return p.read_bytes()

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class DaytonaSandbox(Sandbox):
    """Daytona-backed sandbox (SDK: Daytona.create / sandbox.fs / sandbox.process / Daytona.delete)."""

    provider = "daytona"

    def __init__(self, label: str, client: Any = None):
        if client is None:
            try:
                from daytona import Daytona, DaytonaConfig
            except ImportError as exc:
                raise SandboxError("daytona SDK not installed (pip install daytona)") from exc
            key = os.environ.get("DAYTONA_API_KEY")
            if not key:
                raise SandboxError("DAYTONA_API_KEY is not set")
            client = Daytona(DaytonaConfig(api_key=key, api_url=os.environ.get("DAYTONA_API_URL") or None,
                                           target=os.environ.get("DAYTONA_TARGET") or None))
        self.client = client
        try:
            self.sb = client.create(timeout=float(os.environ.get("DAYTONA_CREATE_TIMEOUT_S", "120")))
        except Exception as exc:
            raise SandboxError(f"Daytona sandbox creation failed: {type(exc).__name__}: {exc}") from exc
        try:
            self.workdir = self.sb.get_work_dir()
        except Exception:
            self.workdir = "/home/daytona"
        self._exec(f"mkdir -p {self.workdir}/data {self.workdir}/out")

    def _exec(self, cmd: str, timeout: int = 60) -> Any:
        try:
            return self.sb.process.exec(cmd, cwd=self.workdir, timeout=timeout)
        except Exception as exc:
            raise SandboxError(f"Daytona exec failed: {type(exc).__name__}: {exc}") from exc

    def upload(self, rel_path: str, data: bytes) -> str:
        dst = f"{self.workdir}/{rel_path}"
        try:
            self.sb.fs.upload_file(data, dst)
        except Exception as exc:
            raise SandboxError(f"Daytona upload failed: {type(exc).__name__}: {exc}") from exc
        return dst

    def sha256(self, path: str) -> str:
        res = self._exec(f"sha256sum '{path}'")
        out = getattr(res, "result", "") or ""
        if getattr(res, "exit_code", 1) != 0 or not out.strip():
            raise SandboxError(f"checksum failed in sandbox: {out[:200]}")
        return out.split()[0]

    def run_python(self, code: str, timeout_s: int = 120) -> Dict[str, Any]:
        script = f"{self.workdir}/_run_{int(time.time() * 1000)}.py"
        self.upload(script.split(self.workdir + "/", 1)[1], code.encode())
        res = self._exec(f"python3 {script}", timeout=timeout_s)
        return {"exit_code": getattr(res, "exit_code", -1), "stdout": (getattr(res, "result", "") or "")[-20000:], "stderr": ""}

    def list_outputs(self) -> List[str]:
        res = self._exec(f"find {self.workdir}/out -type f")
        return [l for l in (getattr(res, "result", "") or "").splitlines() if l.strip()]

    def download(self, path: str) -> bytes:
        try:
            data = self.sb.fs.download_file(path)
        except Exception as exc:
            raise SandboxError(f"Daytona download failed: {type(exc).__name__}: {exc}") from exc
        if data is None:
            raise SandboxError(f"Daytona returned no content for {path}")
        return data

    def close(self) -> None:
        try:
            self.client.delete(self.sb)
        except Exception:
            pass


class SandboxManager:
    """Lazily provisions one sandbox per (run, agent); transfers files with integrity checks; collects artifacts."""

    def __init__(self, files: FileStore, provider: Optional[str] = None, daytona_client: Any = None):
        self.files = files
        self.provider = (provider or os.environ.get("BACKEND_SANDBOX", "off")).lower()
        self._client = daytona_client
        self._boxes: Dict[Tuple[str, str], Sandbox] = {}
        self._uploaded: Dict[Tuple[str, str], Dict[str, str]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.provider in ("daytona", "local")

    def _box(self, run_id: str, agent: str) -> Sandbox:
        key = (run_id, agent)
        with self._lock:
            if key not in self._boxes:
                label = f"{agent.lower()}-{run_id[:8]}"
                self._boxes[key] = DaytonaSandbox(label, self._client) if self.provider == "daytona" else LocalProcessSandbox(label)
                self._uploaded[key] = {}
            return self._boxes[key]

    def ensure_file(self, run_id: str, agent: str, rec: StoredFile) -> str:
        box = self._box(run_id, agent)
        key = (run_id, agent)
        if rec.file_id in self._uploaded[key]:
            return self._uploaded[key][rec.file_id]
        path = box.upload(f"data/{rec.file_id}_{rec.filename}", self.files.read_bytes(rec))
        got = box.sha256(path)
        if got != rec.sha256:
            raise SandboxError(f"integrity check failed for {rec.filename} inside sandbox ({got[:12]} != {rec.sha256[:12]})")
        self._uploaded[key][rec.file_id] = path
        return path

    def run(self, run_id: str, agent: str, code: str, files: List[StoredFile], tenant: str, user: str,
            timeout_s: int = 120) -> Dict[str, Any]:
        box = self._box(run_id, agent)
        paths = {rec.filename: self.ensure_file(run_id, agent, rec) for rec in files}
        before = set(box.list_outputs())
        res = box.run_python(code, timeout_s)
        artifacts = []
        for p in sorted(set(box.list_outputs()) - before)[:10]:
            rec = self.files.save_bytes(tenant, user, Path(p).name, box.download(p), kind="artifact", run_id=run_id,
                                        origin=f"sandbox:{agent}")
            artifacts.append(rec.public())
        return {"provider": box.provider, "workdir": box.workdir, "files": paths, **res, "artifacts": artifacts}

    def close_run(self, run_id: str) -> None:
        with self._lock:
            for key in [k for k in self._boxes if k[0] == run_id]:
                self._boxes.pop(key).close()
                self._uploaded.pop(key, None)
