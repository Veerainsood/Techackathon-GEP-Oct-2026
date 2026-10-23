"""Common data-source interface and a shared implementation for SQL-capable sources."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rapidfuzz import fuzz, process

from udre.contracts.models import (
    CollectionSchema,
    ColumnProfile,
    QueryExecutionResult,
    SourceDescriptor,
    ValueMatch,
)

NUMERIC_TYPES = ("int", "numeric", "decimal", "double", "float", "real", "money", "bigint", "smallint", "hugeint")
TEMPORAL_TYPES = ("date", "time")
TEXT_TYPES = ("char", "text", "string", "varchar", "uuid", "json")


def type_family(raw: str) -> str:
    t = (raw or "").lower()
    if "bool" in t:
        return "boolean"
    if any(k in t for k in TEMPORAL_TYPES):
        return "temporal"
    if any(k in t for k in NUMERIC_TYPES):
        return "numeric"
    return "text"


def normalize_text(value: Any) -> str:
    return re.sub(r"[\s_\-./]+", " ", str(value).lower()).strip()


def jsonable(v: Any) -> Any:
    """Convert DB values (Decimal, date, ...) into JSON-friendly Python values."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    try:
        from decimal import Decimal

        if isinstance(v, Decimal):
            return float(v)
    except ImportError:  # pragma: no cover
        pass
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


class DataSourceAdapter(ABC):
    """Interface every source must implement. Non-SQL sources implement ``execute`` natively."""

    descriptor: SourceDescriptor

    @property
    def source_id(self) -> str:
        return self.descriptor.source_id

    @property
    def dialect(self) -> Optional[str]:
        """sqlglot dialect name, or None for non-SQL sources."""
        return None

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def discover_collections(self) -> List[str]: ...

    @abstractmethod
    def describe_collection(self, collection: str) -> CollectionSchema: ...

    @abstractmethod
    def profile_column(self, collection: str, column: str, top_k: int = 15) -> ColumnProfile: ...

    @abstractmethod
    def sample_rows(self, collection: str, n: int = 5) -> QueryExecutionResult: ...

    @abstractmethod
    def search_values(self, collection: str, column: str, term: str, limit: int = 10) -> List[ValueMatch]: ...

    @abstractmethod
    def execute(self, query: str, max_rows: int = 1000, timeout_s: int = 30) -> QueryExecutionResult: ...

    def explain(self, query: str) -> str:
        return "query plans are not supported by this source"

    def schema_fingerprint(self) -> str:
        """Hash of collection/column structure, used to invalidate stale knowledge."""
        import hashlib

        parts = []
        for c in sorted(self.discover_collections()):
            schema = self.describe_collection(c)
            parts.append(c + ":" + ",".join(f"{col.name}:{col.data_type}" for col in schema.columns))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


class SQLSourceAdapter(DataSourceAdapter):
    """Shared profiling / sampling / value-search logic for any SQL engine.

    Subclasses provide ``_run`` (raw execution) plus catalog discovery.
    """

    _schema_cache: Dict[str, CollectionSchema]

    def quote(self, name: str) -> str:
        return ".".join('"' + p.replace('"', '""') + '"' for p in name.split("."))

    @abstractmethod
    def _run(self, sql: str, params: Optional[Sequence[Any]] = None, timeout_s: int = 30) -> Tuple[List[str], List[tuple]]: ...

    # ------------------------------------------------------------ execution
    def execute(self, query: str, max_rows: int = 1000, timeout_s: int = 30) -> QueryExecutionResult:
        t0 = time.perf_counter()
        try:
            cols, rows = self._run(query, timeout_s=timeout_s)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            return QueryExecutionResult(
                source_id=self.source_id, query=query, succeeded=True, columns=cols,
                rows=[{c: jsonable(v) for c, v in zip(cols, r)} for r in rows], row_count=len(rows),
                truncated=truncated, execution_time_ms=(time.perf_counter() - t0) * 1000)
        except Exception as exc:  # surfaced to agents as an observation, never swallowed silently
            return QueryExecutionResult(
                source_id=self.source_id, query=query, succeeded=False,
                error_message=f"{type(exc).__name__}: {str(exc).strip()[:600]}",
                execution_time_ms=(time.perf_counter() - t0) * 1000)

    def explain(self, query: str) -> str:
        try:
            _cols, rows = self._run(f"EXPLAIN {query}")
            return "\n".join(str(r[-1]) for r in rows)
        except Exception as exc:
            return f"EXPLAIN failed: {exc}"

    # ------------------------------------------------------------ profiling
    def profile_column(self, collection: str, column: str, top_k: int = 15) -> ColumnProfile:
        schema = self.describe_collection(collection)
        col = next((c for c in schema.columns if c.name == column), None)
        if col is None:
            raise KeyError(f"{collection}.{column} does not exist")
        fam = type_family(col.data_type)
        q, t = self.quote(column), self.quote(collection)
        agg = [f"COUNT(*)", f"COUNT({q})", f"COUNT(DISTINCT {q})"]
        if fam in ("numeric", "temporal"):
            agg += [f"MIN({q})", f"MAX({q})"]
        else:
            agg += [f"MIN(CAST({q} AS VARCHAR))", f"MAX(CAST({q} AS VARCHAR))"] if fam == "text" else ["NULL", "NULL"]
        agg.append(f"AVG({q})" if fam == "numeric" else "NULL")
        _c, rows = self._run(f"SELECT {', '.join(agg)} FROM {t}")
        total, non_null, distinct, mn, mx, mean = rows[0]
        total = int(total or 0)
        distinct = int(distinct or 0)
        is_cat = fam in ("text", "boolean") and distinct > 0 and (distinct <= 60 or distinct / max(non_null, 1) < 0.02)
        top: List[Dict[str, Any]] = []
        if is_cat or fam == "boolean":
            _c, trows = self._run(
                f"SELECT {q} AS v, COUNT(*) AS n FROM {t} WHERE {q} IS NOT NULL GROUP BY {q} ORDER BY n DESC LIMIT {int(top_k)}")
            top = [{"value": jsonable(v), "count": int(n)} for v, n in trows]
        _c, srows = self._run(f"SELECT DISTINCT {q} FROM {t} WHERE {q} IS NOT NULL LIMIT 5")
        return ColumnProfile(
            source_id=self.source_id, collection_name=collection, column_name=column, data_type=col.data_type,
            total_count=total, null_count=total - int(non_null or 0),
            null_ratio=round((total - int(non_null or 0)) / total, 4) if total else 0.0,
            distinct_count=distinct, cardinality_ratio=round(distinct / total, 4) if total else 0.0,
            is_categorical=is_cat, min_value=jsonable(mn), max_value=jsonable(mx),
            mean_value=float(mean) if mean is not None else None, top_values=top,
            sample_values=[jsonable(r[0]) for r in srows])

    def profile_collection(self, collection: str, top_k: int = 15) -> Dict[str, ColumnProfile]:
        """Profile every column with two queries (aggregates + categorical distributions)."""
        schema = self.describe_collection(collection)
        t = self.quote(collection)
        exprs: List[str] = ["COUNT(*)"]
        for col in schema.columns:
            q, fam = self.quote(col.name), type_family(col.data_type)
            exprs += [f"COUNT({q})", f"COUNT(DISTINCT {q})"]
            if fam in ("numeric", "temporal"):
                exprs += [f"MIN({q})", f"MAX({q})"]
            elif fam == "text":
                exprs += [f"MIN(CAST({q} AS VARCHAR))", f"MAX(CAST({q} AS VARCHAR))"]
            else:
                exprs += ["NULL", "NULL"]
            exprs.append(f"AVG({q})" if fam == "numeric" else "NULL")
        _c, rows = self._run(f"SELECT {', '.join(exprs)} FROM {t}")
        agg = rows[0]
        total = int(agg[0] or 0)
        stats: Dict[str, tuple] = {}
        for i, col in enumerate(schema.columns):
            stats[col.name] = agg[1 + i * 5: 6 + i * 5]
        categorical = []
        for col in schema.columns:
            non_null, distinct = int(stats[col.name][0] or 0), int(stats[col.name][1] or 0)
            fam = type_family(col.data_type)
            if fam in ("text", "boolean") and distinct > 0 and (distinct <= 60 or distinct / max(non_null, 1) < 0.02):
                categorical.append(col.name)
        tops: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categorical}
        if categorical:
            union = " UNION ALL ".join(
                f"SELECT '{c}' AS c, CAST({self.quote(c)} AS VARCHAR) AS v, COUNT(*) AS n FROM {t} "
                f"WHERE {self.quote(c)} IS NOT NULL GROUP BY CAST({self.quote(c)} AS VARCHAR)" for c in categorical)
            _c, trows = self._run(union)
            for c, v, n in sorted(trows, key=lambda r: (r[0], -int(r[2]), str(r[1]))):
                if len(tops[c]) < top_k:
                    tops[c].append({"value": v, "count": int(n)})
        _c, srows = self._run(f"SELECT * FROM {t} LIMIT 5")
        profiles: Dict[str, ColumnProfile] = {}
        for i, col in enumerate(schema.columns):
            non_null, distinct, mn, mx, mean = stats[col.name]
            non_null, distinct = int(non_null or 0), int(distinct or 0)
            samples = list(dict.fromkeys(jsonable(r[i]) for r in srows if r[i] is not None))[:5]
            profiles[col.name] = ColumnProfile(
                source_id=self.source_id, collection_name=collection, column_name=col.name, data_type=col.data_type,
                total_count=total, null_count=total - non_null,
                null_ratio=round((total - non_null) / total, 4) if total else 0.0, distinct_count=distinct,
                cardinality_ratio=round(distinct / total, 4) if total else 0.0, is_categorical=col.name in tops,
                min_value=jsonable(mn), max_value=jsonable(mx), mean_value=float(mean) if mean is not None else None,
                top_values=tops.get(col.name, []), sample_values=samples)
        return profiles

    def sample_rows(self, collection: str, n: int = 5) -> QueryExecutionResult:
        return self.execute(f"SELECT * FROM {self.quote(collection)} LIMIT {int(n)}", max_rows=n)

    def search_values(self, collection: str, column: str, term: str, limit: int = 10) -> List[ValueMatch]:
        q, t = self.quote(column), self.quote(collection)
        _c, rows = self._run(
            f"SELECT CAST({q} AS VARCHAR) AS v, COUNT(*) AS n FROM {t} WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5000")
        if not rows:
            return []
        counts = {r[0]: int(r[1]) for r in rows}
        nterm = normalize_text(term)
        matches: List[ValueMatch] = []
        seen = set()
        for v in counts:
            nv = normalize_text(v)
            kind = "exact" if v == term else "case_insensitive" if nv == nterm else None
            if kind:
                matches.append(ValueMatch(source_id=self.source_id, collection_name=collection, column_name=column,
                                          matched_value=v, similarity_score=1.0, match_type=kind, occurrence_count=counts[v]))
                seen.add(v)
        if len(nterm) >= 3:
            for v in counts:
                if v in seen:
                    continue
                nv = normalize_text(v)
                if re.search(rf"\b{re.escape(nterm)}\b", nv) or (len(nv) >= 3 and re.search(rf"\b{re.escape(nv)}\b", nterm)):
                    matches.append(ValueMatch(source_id=self.source_id, collection_name=collection, column_name=column,
                                              matched_value=v, similarity_score=0.85, match_type="substring",
                                              occurrence_count=counts[v]))
                    seen.add(v)
        normalized = {normalize_text(v): v for v in counts if v not in seen}
        for nv, score, _ in process.extract(nterm, list(normalized), scorer=fuzz.ratio, limit=limit):
            if score >= 82:
                v = normalized[nv]
                matches.append(ValueMatch(source_id=self.source_id, collection_name=collection, column_name=column,
                                          matched_value=v, similarity_score=round(score / 100, 3), match_type="fuzzy",
                                          occurrence_count=counts[v]))
        matches.sort(key=lambda m: (-m.similarity_score, -m.occurrence_count))
        return matches[:limit]
