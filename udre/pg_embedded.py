"""Manage a local embedded PostgreSQL 16 (+pgvector) using the binaries bundled with ``pgserver``.

The server log lives outside the data directory: on Windows, crash recovery fsyncs every file in
PGDATA and stalls on a log file held open by pg_ctl.  The server is left running between
processes; stop it with ``python -m udre.pg_embedded stop``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = int(os.environ.get("UDRE_PG_PORT", "54329"))


def _bin(name: str) -> str:
    import pgserver

    exe = name + (".exe" if os.name == "nt" else "")
    return str(Path(pgserver.__file__).parent / "pginstall" / "bin" / exe)


def _paths() -> tuple[Path, Path]:
    data = Path(os.environ.get("UDRE_PGDATA", ROOT / ".pgdata"))
    log = Path(os.environ.get("UDRE_PGLOG", ROOT / ".pglog" / "postgres.log"))
    log.parent.mkdir(parents=True, exist_ok=True)
    return data, log


def base_uri(port: int = DEFAULT_PORT) -> str:
    return f"postgresql://postgres@127.0.0.1:{port}/postgres"


def _ready(port: int) -> bool:
    try:
        psycopg2.connect(base_uri(port), connect_timeout=2).close()
        return True
    except psycopg2.OperationalError:
        return False


def _kill_orphans() -> int:
    """Kill leftover processes of the bundled postgres binary; they hold shared memory after a crash."""
    import psutil

    bin_dir = str(Path(_bin("postgres")).parent).lower().replace("\\", "/")
    killed = 0
    for proc in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or []).lower().replace("\\", "/")
            if proc.info["name"] and proc.info["name"].lower().startswith("postgres") and bin_dir in cmd:
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if killed:
        time.sleep(1)
    return killed


def start(port: int = DEFAULT_PORT, timeout_s: int = 90) -> str:
    if _ready(port):
        return base_uri(port)
    _kill_orphans()
    data, log = _paths()
    if not (data / "PG_VERSION").exists():
        data.mkdir(parents=True, exist_ok=True)
        subprocess.run([_bin("initdb"), "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
                       check=True, capture_output=True, text=True)
    stray_log = data / "log"
    if stray_log.exists():
        try:
            stray_log.unlink()
        except OSError:
            pass
    pid_file = data / "postmaster.pid"
    if pid_file.exists():
        pid_file.unlink()  # stale lock from an unclean shutdown; we verified nothing answers on the port
    subprocess.Popen([_bin("pg_ctl"), "-D", str(data), "-l", str(log), "-o", f"-h 127.0.0.1 -p {port}", "start"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _ready(port):
            return base_uri(port)
        time.sleep(0.5)
    raise RuntimeError(f"PostgreSQL did not start within {timeout_s}s; see {log}")


def stop() -> None:
    data, _ = _paths()
    subprocess.run([_bin("pg_ctl"), "-D", str(data), "-m", "fast", "stop"], capture_output=True, text=True)


def ensure_database(name: str, port: int = DEFAULT_PORT) -> str:
    start(port)
    conn = psycopg2.connect(base_uri(port))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{name}"')
    conn.close()
    return f"postgresql://postgres@127.0.0.1:{port}/{name}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "stop":
        stop()
        print("stopped")
    else:
        print(start())
