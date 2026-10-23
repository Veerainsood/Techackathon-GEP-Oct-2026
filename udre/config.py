"""Runtime configuration (environment-driven) and default source wiring."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    data_db: str = os.environ.get("UDRE_DATA_DB", "aethelgard_finance")
    knowledge_db: str = os.environ.get("UDRE_KNOWLEDGE_DB", "udre_knowledge")
    uploads_dir: str = os.environ.get("UDRE_UPLOADS_DIR", str(ROOT / "data" / "uploads"))
    exports_dir: str = os.environ.get("UDRE_EXPORTS_DIR", str(ROOT / "exports"))
    explorer_max_steps: int = int(os.environ.get("UDRE_EXPLORER_MAX_STEPS", "14"))
    hercules_context_tokens: int = int(os.environ.get("UDRE_HERCULES_CONTEXT_TOKENS", "32000"))
    max_rows: int = int(os.environ.get("UDRE_MAX_ROWS", "1000"))
    reasoning_mode: str = os.environ.get("UDRE_REASONING_MODE", "auto")  # auto | offline | llm
    tenants: List[str] = field(default_factory=lambda: ["default"])


def pg_uri(database: str) -> str:
    explicit = os.environ.get("UDRE_PG_URI")
    if explicit:
        return explicit.rsplit("/", 1)[0] + f"/{database}"
    from udre import pg_embedded

    return pg_embedded.ensure_database(database)
