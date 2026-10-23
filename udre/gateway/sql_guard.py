"""Static SQL analysis: read-only enforcement, table/column extraction and row bounding (sqlglot)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set

import sqlglot
from sqlglot import exp

WRITE_NODES = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Merge", "Command", "Copy", "Grant", "TruncateTable",
    "Set", "Use", "Transaction", "Commit", "Rollback", "Pragma", "Attach", "Detach", "Install", "Load") if hasattr(exp, n))

BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export", "dblink",
    "pg_terminate_backend", "pg_cancel_backend", "set_config", "pg_reload_conf", "read_csv", "read_csv_auto",
    "read_parquet", "read_json", "read_json_auto", "read_ndjson", "read_text", "read_blob", "glob", "httpfs",
    "sniff_csv", "query_table", "getenv",
}
_FUNC_RE = re.compile(r"\b(" + "|".join(sorted(BLOCKED_FUNCTIONS)) + r")\s*\(", re.IGNORECASE)


class UnsafeQueryError(ValueError):
    pass


@dataclass
class QueryAnalysis:
    sql: str
    tables: Set[str] = field(default_factory=set)
    columns: Set[str] = field(default_factory=set)
    aggregates: List[str] = field(default_factory=list)
    group_by: List[str] = field(default_factory=list)
    joins: int = 0
    has_distinct_agg: bool = False
    where_sql: str = ""
    limit: Optional[int] = None


def _table_name(t: exp.Table) -> str:
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


def parse_single(sql: str, dialect: Optional[str]) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQueryError(f"SQL parse error: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise UnsafeQueryError("exactly one statement is allowed")
    return statements[0]


def assert_read_only(sql: str, dialect: Optional[str]) -> exp.Expression:
    tree = parse_single(sql, dialect)
    if not isinstance(tree, exp.Query):
        raise UnsafeQueryError(f"only read queries are allowed, got {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise UnsafeQueryError(f"write/DDL construct {type(node).__name__} is not allowed")
        if isinstance(node, exp.Into):
            raise UnsafeQueryError("SELECT INTO is not allowed")
    m = _FUNC_RE.search(sql)
    if m:
        raise UnsafeQueryError(f"function {m.group(1)} is not allowed")
    return tree


def analyze(sql: str, dialect: Optional[str]) -> QueryAnalysis:
    tree = parse_single(sql, dialect)
    cte_names = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    qa = QueryAnalysis(sql=sql)
    qa.tables = {_table_name(t) for t in tree.find_all(exp.Table) if t.name and t.name not in cte_names}
    qa.columns = {c.name for c in tree.find_all(exp.Column) if c.name}
    for agg in tree.find_all(exp.AggFunc):
        qa.aggregates.append(agg.sql(dialect=dialect))
        if isinstance(agg.this, exp.Distinct):
            qa.has_distinct_agg = True
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is not None:
        group = select.args.get("group")
        qa.group_by = [g.sql(dialect=dialect) for g in group.expressions] if group else []
        qa.joins = len(select.args.get("joins") or [])
        where = select.args.get("where")
        qa.where_sql = where.this.sql(dialect=dialect) if where else ""
        lim = select.args.get("limit")
        if lim is not None and isinstance(lim.expression, exp.Literal):
            qa.limit = int(lim.expression.this)
    return qa


def bound_rows(sql: str, dialect: Optional[str], max_rows: int) -> str:
    """Ensure the query returns at most ``max_rows + 1`` rows (the +1 detects truncation)."""
    tree = parse_single(sql, dialect)
    cap = max_rows + 1
    if isinstance(tree, exp.Select):
        lim = tree.args.get("limit")
        if lim is not None and isinstance(lim.expression, exp.Literal) and int(lim.expression.this) <= cap:
            return tree.sql(dialect=dialect)
        return tree.limit(cap).sql(dialect=dialect)
    return f"SELECT * FROM ({tree.sql(dialect=dialect)}) AS _udre_bounded LIMIT {cap}"
