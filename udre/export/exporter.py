"""Extensible exporters. Register a new format with ``register_exporter``."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


class ReportExporter(ABC):
    format: str
    extension: str

    @abstractmethod
    def write(self, rows: List[Dict[str, Any]], path: Path, metadata: Dict[str, Any]) -> None: ...


class CsvExporter(ReportExporter):
    format, extension = "csv", ".csv"

    def write(self, rows, path, metadata):
        pd.DataFrame(rows).to_csv(path, index=False)


class ExcelExporter(ReportExporter):
    """Results sheet plus a lineage sheet (question, query, evidence, verification status)."""

    format, extension = "excel", ".xlsx"

    def write(self, rows, path, metadata):
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            pd.DataFrame(rows).to_excel(xw, sheet_name="results", index=False)
            lineage = [{"field": k, "value": json.dumps(v, default=str) if not isinstance(v, str) else v}
                       for k, v in metadata.items()]
            pd.DataFrame(lineage).to_excel(xw, sheet_name="lineage", index=False)


class JsonExporter(ReportExporter):
    format, extension = "json", ".json"

    def write(self, rows, path, metadata):
        path.write_text(json.dumps({"metadata": metadata, "rows": rows}, indent=2, default=str), encoding="utf-8")


class MarkdownReportExporter(ReportExporter):
    format, extension = "markdown", ".md"

    def write(self, rows, path, metadata):
        lines = [f"# {metadata.get('title', 'Query result')}", ""]
        for key in ("question", "answer", "verification_status", "agreement_status", "query"):
            if key in metadata:
                val = metadata[key]
                lines += [f"**{key.replace('_', ' ').title()}:**", "", f"```sql\n{val}\n```" if key == "query" else str(val), ""]
        if rows:
            cols = list(rows[0])
            lines += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            lines += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows[:500]]
        for key in ("evidence", "assumptions"):
            if metadata.get(key):
                lines += ["", f"## {key.title()}", ""] + [f"- {e}" for e in metadata[key]]
        path.write_text("\n".join(lines), encoding="utf-8")


EXPORTERS: Dict[str, ReportExporter] = {e.format: e for e in (CsvExporter(), ExcelExporter(), JsonExporter(), MarkdownReportExporter())}


def register_exporter(exporter: ReportExporter) -> None:
    EXPORTERS[exporter.format] = exporter


def export_result(rows: List[Dict[str, Any]], fmt: str, out_dir: str, name: Optional[str] = None,
                  metadata: Optional[Dict[str, Any]] = None) -> str:
    if fmt not in EXPORTERS:
        raise ValueError(f"unsupported export format {fmt}; available: {sorted(EXPORTERS)}")
    exporter = EXPORTERS[fmt]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", name or "result").strip("_")[:60] or "result"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{safe}_{stamp}{exporter.extension}"
    exporter.write(rows, path, dict(metadata or {}, exported_at=stamp))
    return str(path)
