"""Deterministic validators. Each returns ``ValidationFinding`` objects; several execute their own
check queries so that the finding carries observable evidence rather than an opinion."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from udre.agents.planner import CatalogView, FilterSpec, QueryPlan, _lit, parse_question
from udre.contracts.models import EdgeType, QueryExecutionResult, ValidationFinding
from udre.gateway.base import normalize_text
from udre.gateway.registry import AccessContext, SourceGateway
from udre.gateway.sql_guard import UnsafeQueryError, analyze
from udre.ontology.embedding import tokens
from udre.ontology.graph import KnowledgeGraph

ISO = {"USD", "EUR", "GBP", "JPY", "SGD", "CAD", "AUD", "CHF", "INR", "CNY", "HKD", "SEK", "NOK", "DKK"}


def _f(name: str, passed: bool, severity: str, message: str, **details: Any) -> ValidationFinding:
    return ValidationFinding(validator_name=name, passed=passed, severity=severity, message=message, details=details)


class Verifier:
    def __init__(self, gateway: SourceGateway, kg: KnowledgeGraph, packs: Sequence[Dict[str, Any]]):
        self.gateway = gateway
        self.kg = kg
        self.nullifying = {normalize_text(t) for p in packs for t in p.get("nullifying_status_terms", [])}
        self.ccy_tokens = {t for p in packs for t in p.get("reporting_currency_tokens", [])} or {"usd"}

    # ------------------------------------------------------------------ helpers
    def _scalar(self, ctx: AccessContext, source_id: str, sql: str) -> Dict[str, Any]:
        res = self.gateway.execute_query(ctx, source_id, sql, max_rows=50)
        return {"sql": sql, "ok": res.succeeded, "rows": res.rows[:10], "error": res.error_message}

    @staticmethod
    def _from_where(plan: QueryPlan, view: CatalogView, extra: Optional[List[str]] = None,
                    skip: Optional[FilterSpec] = None) -> str:
        q = view.quote
        aliases = {plan.primary_collection: "t0"}
        sql = f" FROM {q(plan.primary_collection)} t0"
        for j in plan.joins:
            if j.right not in aliases:
                aliases[j.right] = f"t{len(aliases)}"
                sql += f" JOIN {q(j.right)} {aliases[j.right]} ON {aliases[j.left]}.{q(j.left_col)} = {aliases[j.right]}.{q(j.right_col)}"
        conds = []
        for f in plan.filters:
            if f is skip:
                continue
            col = f"{aliases[f.collection]}.{q(f.column)}"
            conds.append({"IS NULL": f"{col} IS NULL", "YEAR": f"EXTRACT(YEAR FROM {col}) = {f.value}",
                          "LIKE": f"CAST({col} AS VARCHAR) LIKE {_lit(f.value)}"}.get(f.op, f"{col} {f.op} {_lit(f.value)}"))
        conds += extra or []
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return sql

    # ------------------------------------------------------------------ entry point
    def verify(self, ctx: AccessContext, question: str, plan: Optional[QueryPlan], sql: str,
               result: Optional[QueryExecutionResult], view: Optional[CatalogView]) -> List[ValidationFinding]:
        adapter = self.gateway.adapter(ctx, plan.source_id if plan else "")
        findings: List[ValidationFinding] = []
        findings += self.syntax_and_columns(adapter, sql)
        findings += self.source_binding(ctx, adapter, sql)
        findings += self.execution(result)
        if plan is None or view is None:
            return findings
        for check in (self.join_multiplicity, self.null_handling, self.currency_units, self.date_period,
                      self.filter_grounding, self.nullifying_status, self.reconciliation, self.balance_invariant):
            try:
                findings += check(ctx, question, plan, view, result)
            except Exception as exc:  # a broken check is reported, never silently skipped
                findings.append(_f(check.__name__, False, "warning", f"validator could not run: {exc}"))
        return findings

    # ------------------------------------------------------------------ static checks
    def syntax_and_columns(self, adapter, sql: str) -> List[ValidationFinding]:
        try:
            qa = analyze(sql, adapter.dialect)
        except UnsafeQueryError as exc:
            return [_f("syntax_and_columns", False, "error", str(exc))]
        known: set = set()
        missing_tables = []
        for t in qa.tables:
            try:
                known |= {c.name for c in adapter.describe_collection(t).columns}
            except KeyError:
                missing_tables.append(t)
        missing = sorted(c for c in qa.columns if c not in known and c not in ("value",))
        if missing_tables or missing:
            return [_f("syntax_and_columns", False, "error", "unknown tables/columns referenced",
                       missing_tables=missing_tables, missing_columns=missing)]
        return [_f("syntax_and_columns", True, "info", f"parsed; {len(qa.tables)} tables and {len(qa.columns)} columns exist")]

    def source_binding(self, ctx, adapter, sql: str) -> List[ValidationFinding]:
        tables = analyze(sql, adapter.dialect).tables
        allowed = set(self.gateway.collections(ctx, adapter.source_id))
        bad = sorted(t for t in tables if t not in allowed)
        if bad:
            return [_f("source_binding", False, "error", "query references collections outside the authorized source", tables=bad)]
        return [_f("source_binding", True, "info", f"bound to {adapter.source_id}: {sorted(tables)}")]

    def execution(self, result: Optional[QueryExecutionResult]) -> List[ValidationFinding]:
        if result is None:
            return [_f("execution", False, "error", "query was not executed")]
        if not result.succeeded:
            return [_f("execution", False, "error", "execution failed", error=result.error_message)]
        if not result.rows or all(v is None for v in result.rows[0].values()):
            return [_f("execution", False, "warning", "query returned no rows or a NULL aggregate")]
        return [_f("execution", True, "info", f"{result.row_count} rows in {result.execution_time_ms:.1f} ms",
                   truncated=result.truncated)]

    # ------------------------------------------------------------------ data-backed checks
    def join_multiplicity(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        if not plan.joins:
            return [_f("join_multiplicity", True, "info", "single collection; no join fan-out possible")]
        pk = [c.column for c in view.cols_of(plan.primary_collection) if c.is_pk]
        if not pk:
            return [_f("join_multiplicity", True, "warning", "primary collection has no known key; fan-out not measurable")]
        sql = f"SELECT COUNT(*) AS joined_rows, COUNT(DISTINCT t0.{view.quote(pk[0])}) AS base_rows" + self._from_where(plan, view)
        ev = self._scalar(ctx, plan.source_id, sql)
        if not ev["ok"]:
            return [_f("join_multiplicity", False, "warning", "fan-out check failed to run", evidence=ev)]
        joined, base = ev["rows"][0]["joined_rows"], ev["rows"][0]["base_rows"]
        if joined > base and plan.aggregation in ("SUM", "AVG", "TOP") and plan.measure and plan.measure[0] == plan.primary_collection:
            return [_f("join_multiplicity", False, "error", f"joins multiply rows ({joined} joined vs {base} base): aggregate inflated",
                       evidence=ev)]
        return [_f("join_multiplicity", True, "info", f"joins preserve grain ({joined} joined rows, {base} base rows)", evidence=ev)]

    def null_handling(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        out = []
        if plan.measure:
            col = view.columns.get(tuple(plan.measure))
            if col and col.null_ratio > 0:
                sev = "warning" if plan.aggregation == "AVG" else "info"
                out.append(_f("null_handling", True, sev, f"{plan.measure[1]} contains NULLs ({col.null_ratio:.1%}); "
                                                         f"{plan.aggregation} ignores them"))
        for j in plan.joins:
            c = view.columns.get((j.left, j.left_col))
            if c and c.null_ratio > 0:
                sql = (f"SELECT COUNT(*) AS dropped" + self._from_where(
                    QueryPlan(source_id=plan.source_id, primary_collection=plan.primary_collection, aggregation="COUNT",
                              filters=[f for f in plan.filters if f.collection == plan.primary_collection]), view,
                    extra=[f"t0.{view.quote(j.left_col)} IS NULL"]) if j.left == plan.primary_collection else None)
                if sql:
                    ev = self._scalar(ctx, plan.source_id, sql)
                    dropped = ev["rows"][0]["dropped"] if ev["ok"] else None
                    if dropped:
                        out.append(_f("null_handling", True, "warning",
                                      f"inner join on nullable {j.left}.{j.left_col} excludes {dropped} rows with no mapping",
                                      evidence=ev))
        return out or [_f("null_handling", True, "info", "no null-sensitive constructs detected")]

    def currency_units(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        if not plan.measure or plan.aggregation not in ("SUM", "AVG", "TOP", "MAX", "MIN"):
            return [_f("currency_units", True, "info", "no monetary aggregation")]
        mcoll, mcol = plan.measure
        if set(tokens(mcol)) & self.ccy_tokens:
            return [_f("currency_units", True, "info", f"{mcol} is normalized to the reporting currency")]
        ccy_cols = [c for c in view.cols_of(mcoll) if c.top_values and all(str(v) in ISO for v in c.top_values)]
        for c in ccy_cols:
            if len(c.top_values) > 1:
                constrained = any(f.collection == mcoll and f.column == c.column and f.op == "=" for f in plan.filters) or \
                    any(g == (mcoll, c.column) for g in plan.group_by)
                if not constrained:
                    siblings = [x.column for x in view.cols_of(mcoll) if set(tokens(x.column)) & self.ccy_tokens and x.is_measure]
                    return [_f("currency_units", False, "error",
                               f"{mcol} mixes currencies {c.top_values} without filtering/grouping by {c.column}",
                               normalized_alternatives=siblings)]
        return [_f("currency_units", True, "info", "single currency or currency-constrained aggregation")]

    def date_period(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        frame = parse_question(question)
        has_time = any(f.origin == "time" or re.fullmatch(r"\d{4}-\d{2}", str(f.value)) for f in plan.filters)
        out = []
        if (frame.years or frame.periods) and not has_time:
            out.append(_f("date_period", False, "error", f"question constrains time {frame.years or frame.periods} but no time filter was applied"))
        if frame.relative_time:
            out.append(_f("date_period", True, "warning", f"relative period '{frame.relative_time}' requires an assumed reference date"))
        for f in plan.filters:
            if f.origin == "time":
                sql = "SELECT COUNT(*) AS n" + self._from_where(
                    QueryPlan(source_id=plan.source_id, primary_collection=plan.primary_collection, aggregation="COUNT",
                              joins=plan.joins, filters=[f]), view)
                ev = self._scalar(ctx, plan.source_id, sql)
                n = ev["rows"][0]["n"] if ev["ok"] else 0
                out.append(_f("date_period", bool(n), "info" if n else "error",
                              f"time filter {f.column} {f.op} {f.value} matches {n} rows", evidence=ev))
        return out or [_f("date_period", True, "info", "no time constraint requested")]

    def filter_grounding(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        out = []
        for f in plan.filters:
            if f.origin not in ("value_match", "exclusion", "learned_mapping"):
                continue
            sql = f"SELECT COUNT(*) AS n FROM {view.quote(f.collection)} WHERE {view.quote(f.column)} = {_lit(f.value)}"
            ev = self._scalar(ctx, plan.source_id, sql)
            n = ev["rows"][0]["n"] if ev["ok"] else 0
            out.append(_f("filter_grounding", bool(n), "info" if n else "error",
                          f"'{f.matched_text}' -> {f.collection}.{f.column} {f.op} '{f.value}' ({n} rows carry this value)",
                          evidence=ev))
        return out or [_f("filter_grounding", True, "info", "no value filters")]

    def nullifying_status(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        out = []
        for c in view.cols_of(plan.primary_collection):
            if not c.top_values or not c.is_dimension:
                continue
            bad = [v for v in c.top_values if normalize_text(v) in self.nullifying]
            if not bad:
                continue
            if any(f.collection == c.collection and f.column == c.column for f in plan.filters) or (c.collection, c.column) in plan.group_by:
                out.append(_f("nullifying_status", True, "info", f"{c.column} is explicitly constrained"))
                continue
            impact_expr = f"SUM(t0.{view.quote(plan.measure[1])})" if plan.measure and plan.measure[0] == plan.primary_collection else "COUNT(*)"
            vals = ", ".join(_lit(v) for v in bad)
            sql = f"SELECT COUNT(*) AS n, {impact_expr} AS impact" + self._from_where(plan, view, extra=[f"t0.{view.quote(c.column)} IN ({vals})"])
            ev = self._scalar(ctx, plan.source_id, sql)
            if not ev["ok"]:
                continue
            n, impact = ev["rows"][0]["n"], ev["rows"][0]["impact"]
            if n and impact:
                out.append(_f("nullifying_status", True, "warning",
                              f"result includes {n} rows with {c.column} in {bad} (impact {impact}); confirm whether they belong",
                              evidence=ev, column=c.column, values=bad, impact=impact))
            else:
                out.append(_f("nullifying_status", True, "info", f"rows with {c.column} in {bad} do not affect the result", evidence=ev))
        return out

    def reconciliation(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        if not plan.measure or plan.aggregation != "SUM" or plan.group_by or not result or not result.succeeded:
            return []
        if any(f.collection != plan.primary_collection for f in plan.filters):
            return []
        mcoll, mcol = plan.measure
        rules = [e["properties"] for e in self.kg.edges(EdgeType.DERIVED_FROM, source_id=plan.source_id, tenant=ctx.tenant_id)
                 if e["properties"].get("rule") and e["properties"]["parent"] == mcoll and e["properties"]["parent_col"] == mcol
                 and e["verification_status"] == "verified_fact"]
        out = []
        value = result.rows[0].get("value") if result.rows else None
        for r in rules[:2]:
            q = view.quote
            where = self._from_where(QueryPlan(source_id=plan.source_id, primary_collection=mcoll, aggregation="SUM",
                                               filters=plan.filters), view)
            sql = (f"SELECT SUM(c.{q(r['child_col'])}) AS value FROM {q(r['child'])} c WHERE c.{q(r['join_child'])} IN "
                   f"(SELECT t0.{q(r['join_parent'])}{where})")
            ev = self._scalar(ctx, plan.source_id, sql)
            alt = ev["rows"][0]["value"] if ev["ok"] and ev["rows"] else None
            ok = alt is not None and value is not None and abs(float(alt) - float(value)) <= 0.01 + 1e-6 * abs(float(value))
            out.append(_f("reconciliation", ok, "info" if ok else "warning",
                          f"{'reconciles' if ok else 'does not reconcile'} with SUM({r['child']}.{r['child_col']}): {alt} vs {value}",
                          evidence=ev, rule=f"{mcoll}.{mcol}=SUM({r['child']}.{r['child_col']})"))
        return out

    def balance_invariant(self, ctx, question, plan, view, result) -> List[ValidationFinding]:
        cols = view.cols_of(plan.primary_collection)
        debit = next((c for c in cols if c.is_measure and "debit" in tokens(c.column)), None)
        credit = next((c for c in cols if c.is_measure and "credit" in tokens(c.column)), None)
        if not (debit and credit):
            return []
        q = view.quote
        sql = f"SELECT SUM(t0.{q(debit.column)}) AS debits, SUM(t0.{q(credit.column)}) AS credits" + self._from_where(plan, view)
        ev = self._scalar(ctx, plan.source_id, sql)
        if not ev["ok"]:
            return []
        d, c = ev["rows"][0]["debits"] or 0, ev["rows"][0]["credits"] or 0
        ok = abs(float(d) - float(c)) < 0.01
        return [_f("balance_invariant", ok, "info" if ok else "warning",
                   f"debits {d} {'=' if ok else '!='} credits {c} within the filtered scope", evidence=ev)]


def summarize(findings: Sequence[ValidationFinding]) -> Dict[str, int]:
    return {"errors": sum(1 for f in findings if not f.passed and f.severity == "error"),
            "warnings": sum(1 for f in findings if f.severity == "warning"),
            "passed": sum(1 for f in findings if f.passed)}
