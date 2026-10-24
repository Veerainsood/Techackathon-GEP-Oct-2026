"""What each result column means, from the verified SQL rather than from its alias.

Column lineage (sqlglot) follows every output column through CTEs, subqueries and UNIONs to the source columns and the
functions applied on the way. From that:

- kind "measure" with unit "count" when the value is a COUNT; a SUM/AVG/MIN/MAX of a money column takes the currency
  of its source column (schema naming such as ``gross_total_usd``), else of an equality/IN filter on the currency
  column when it pins one currency; unit "percent" for a ratio of aggregates;
- kind "time" when the column derives from a date/timestamp column or a date function (DATE_TRUNC, TO_CHAR, EXTRACT);
- role "currency" when it derives from the source's currency column;
- nothing for literals and anything lineage cannot resolve (the chart layer then falls back to the rows themselves).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlglot import exp

CURRENCY_COL = re.compile(r"(^|_)(currency|ccy|cur)(_code|_cd)?$", re.I)
CODE_TOKEN = re.compile(r"(?:^|_)(usd|inr|eur|gbp|jpy|aud|cad|sgd|aed|chf|cny)(?:_|$)", re.I)
DATE_TYPE = re.compile(r"date|time", re.I)
NUMERIC_TYPE = re.compile(r"int|numeric|decimal|double|float|real|money|number|bigint", re.I)
TIME_FUNCS = tuple(getattr(exp, n) for n in ("TimestampTrunc", "DateTrunc", "TimeToStr", "Extract", "Year", "Month", "Quarter",
                                              "Week", "StrToDate", "TsOrDsToDate", "Date") if hasattr(exp, n))


def _schema(adapter: Any, tables: List[str]) -> Dict[str, Any]:
    """Nested {schema: {table: {column: type}}} as sqlglot expects, plus a flat lookup."""
    nested: Dict[str, Any] = {}
    for t in tables:
        try:
            cols = {c.name: c.data_type for c in adapter.describe_collection(t).columns}
        except Exception:
            continue
        parts = t.split(".")
        if len(parts) == 2:
            nested.setdefault(parts[0], {})[parts[1]] = cols
        else:
            nested[t] = cols
    return nested


def _type_of(nested: Dict[str, Any], table: exp.Table, column: str) -> Optional[str]:
    if table.db and isinstance(nested.get(table.db), dict) and isinstance(nested[table.db].get(table.name), dict):
        return nested[table.db][table.name].get(column)
    cols = nested.get(table.name)
    return cols.get(column) if isinstance(cols, dict) and isinstance(next(iter(cols.values()), ""), str) else None


def _currency_filter(tree: exp.Expression) -> Optional[str]:
    """A WHERE that pins one currency: currency_code = 'INR' or IN ('INR')."""
    for cond in tree.find_all(exp.EQ, exp.In):
        col = cond.this if isinstance(cond.this, exp.Column) else None
        if col is None or not CURRENCY_COL.search(col.name):
            continue
        lits = [cond.expression] if isinstance(cond, exp.EQ) else list(cond.expressions)
        vals = {str(l.this).upper() for l in lits if isinstance(l, exp.Literal) and l.is_string}
        if len(vals) == 1:
            return vals.pop().lower()
    return None


def result_semantics(gateway: Any, ctx: Any, source_id: str, sql: str, columns: List[str]) -> Dict[str, Dict[str, Any]]:
    import sqlglot
    from sqlglot.lineage import lineage

    from backend.gateway.sql_guard import analyze

    if not sql or not columns:
        return {}
    adapter = gateway.adapter(ctx, source_id)
    dialect = adapter.dialect
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
        nested = _schema(adapter, sorted(analyze(sql, dialect).tables))
    except Exception:
        return {}
    pinned = _currency_filter(tree)
    out: Dict[str, Dict[str, Any]] = {}
    for col in columns:
        try:
            node = lineage(col, sql, schema=nested or None, dialect=dialect)
        except Exception:
            continue
        aggs, funcs, leaves = set(), [], []
        for n in node.walk():
            for a in n.expression.find_all(exp.AggFunc):
                aggs.add(type(a).__name__)
            funcs += [f for f in n.expression.find_all(*TIME_FUNCS)]
            if isinstance(n.source, exp.Table) and not n.downstream:
                name = n.name.split(".")[-1]
                leaves.append((name, _type_of(nested, n.source, name)))
            has_ratio = any(isinstance(d, exp.Div) and d.this.find(exp.AggFunc) and d.expression.find(exp.AggFunc)
                            for d in n.expression.find_all(exp.Div))
            if has_ratio:
                aggs.add("Ratio")
        sem: Dict[str, Any] = {}
        money_leaves = [(c, t) for c, t in leaves if t and NUMERIC_TYPE.search(t)]
        if "Ratio" in aggs:
            sem = {"kind": "measure", "unit": "percent"}
        elif aggs and (aggs <= {"Count", "Avg", "Max", "Min"} and "Count" in aggs and not money_leaves):
            sem = {"kind": "measure", "unit": "count"}
        elif aggs or money_leaves:
            codes = {m.group(1).lower() for c, _ in money_leaves for m in [CODE_TOKEN.search(c)] if m}
            sem = {"kind": "measure", "unit": codes.pop() if len(codes) == 1 else pinned}
        elif funcs or (leaves and all(t and DATE_TYPE.search(t) for _, t in leaves)):
            sem = {"kind": "time"}
        elif leaves and all(CURRENCY_COL.search(c) for c, _ in leaves):
            sem = {"kind": "category", "role": "currency"}
        elif leaves:
            sem = {"kind": "category"}
        if sem:
            sem["source"] = sorted({c for c, _ in leaves})
            out[col] = sem
    return out
