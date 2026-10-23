"""Runtime policy: timestamp mappings, investigation limits and when (not) to spend LLM calls.

Loaded from ``UDRE_RUNTIME_POLICY`` (path to JSON) with environment overrides. Defaults are conservative:
automatic agent analysis is OFF; only explicitly configured watched analyses or anomalies may trigger it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class WatchedAnalysis:
    analysis_id: str
    question: str
    sources: List[str]
    collections: List[str] = field(default_factory=list)  # empty = any change in the sources
    mode: str = "dual"


@dataclass
class RuntimePolicy:
    timestamp_columns: Dict[str, Dict[str, str]] = field(default_factory=dict)  # source -> collection -> column
    lookback_s: int = 300           # re-scan before window_start to catch late-arriving rows
    max_keys: int = 500             # bound on keys used for impact queries
    max_visibility_retries: int = 5
    auto_analysis: bool = False     # Stage B automatic agent analysis on events
    anomaly_ratio: float = 5.0      # changed rows vs trailing average per event
    anomaly_min_rows: int = 50
    llm_on_unresolved_relationships: bool = False
    stage_b_mode: str = "dual"
    watched: List[WatchedAnalysis] = field(default_factory=list)

    @classmethod
    def load(cls) -> "RuntimePolicy":
        data: Dict[str, Any] = {}
        path = os.environ.get("UDRE_RUNTIME_POLICY")
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        p = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "watched"})
        p.watched = [WatchedAnalysis(**w) for w in data.get("watched", [])]
        if os.environ.get("UDRE_AUTO_ANALYSIS"):
            p.auto_analysis = os.environ["UDRE_AUTO_ANALYSIS"].lower() in ("1", "true", "yes")
        if os.environ.get("UDRE_EVENT_LOOKBACK_S"):
            p.lookback_s = int(os.environ["UDRE_EVENT_LOOKBACK_S"])
        return p

    def timestamp_column(self, source_id: str, collection: str, declared: Optional[str]) -> Optional[str]:
        return declared or self.timestamp_columns.get(source_id, {}).get(collection)
