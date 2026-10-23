"""Benchmark harness: Explorer-only, Hercules-only, dual-path, dual-path + persistent memory.

Ground truth comes from ``benchmark/{dev,heldout}.json`` (computed independently from the
generator's event model).  The held-out split is only scored here, never used for tuning.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional

from udre.gateway.base import normalize_text
from udre.gateway.registry import AccessContext
from udre.gateway.sql_guard import analyze


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def answer_value(state: Dict[str, Any]) -> Any:
    d = state["decision"]
    for key in ("explorer_candidate", "hercules_candidate"):
        c = state.get(key)
        if c and c["query_or_plan"] == d.get("winning_query"):
            return c["structured_value"], c
    rows = d.get("final_structured_data") or []
    if rows and d.get("winning_query"):
        if len(rows) == 1 and "value" in rows[0]:
            return rows[0]["value"], None
        keys = [k for k in rows[0] if k != "value"]
        return {str(r[keys[0]]): r["value"] for r in rows} if keys else None, None
    return None, None


def is_correct(q: Dict[str, Any], value: Any) -> bool:
    exp = q["expected"]
    if value is None or exp is None:
        return False
    if q["answer_type"] == "scalar":
        a, b = _num(value), _num(exp)
        if a is None or b is None:
            return False
        if q.get("sign_insensitive"):
            a, b = abs(a), abs(b)
        return abs(a - b) <= max(0.01, 1e-6 * abs(b))
    if not isinstance(value, dict):
        return False
    got = sorted(_num(v) or 0.0 for v in value.values())
    want = sorted(_num(v) or 0.0 for v in exp.values())
    return len(got) == len(want) and all(abs(x - y) <= max(0.01, 1e-6 * abs(y)) for x, y in zip(got, want))


def pctl(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def evaluate(engine, questions: List[Dict[str, Any]], mode: str, use_memory: bool,
             ctx: Optional[AccessContext] = None) -> Dict[str, Any]:
    rows = []
    for q in questions:
        t0 = time.perf_counter()
        try:
            state = engine.ask(q["question"], ctx=ctx, mode=mode, use_memory=use_memory)
            err = None
        except Exception as exc:  # recorded as a failed answer, never hidden
            state, err = None, f"{type(exc).__name__}: {exc}"
        ms = (time.perf_counter() - t0) * 1000
        if state is None:
            rows.append(dict(id=q["id"], category=q["category"], behavior=q["behavior"], correct=False, abstained=False,
                             confident=False, error=err, ms=ms, tool_calls=0, tokens=0, schema_link=False, value_link=False))
            continue
        d = state["decision"]
        value, cand = answer_value(state)
        abstained = d["selected_path"] == "Abstained_With_Uncertainty"
        confident = d["verification_status"] == "verified_fact" and not abstained
        correct = (abstained if q["behavior"] == "flag_ambiguity" else (not abstained and is_correct(q, value)))
        tables: set = set()
        if d.get("winning_query"):
            try:
                tables = analyze(d["winning_query"], "postgres").tables
            except Exception:
                tables = set()
        filt_vals = {normalize_text(f["value"]) for f in (cand or {}).get("applied_filters", []) if f.get("value") is not None}
        filt_vals |= {normalize_text(str(f["value"]).replace("%", "").rstrip("-")) for f in (cand or {}).get("applied_filters", [])
                      if f.get("value") is not None}
        tools = sum((state.get(k) or {}).get("tool_calls_count", 0) for k in ("explorer_candidate", "hercules_candidate"))
        tokens = sum((state.get(k) or {}).get("tokens_used", 0) for k in ("explorer_candidate", "hercules_candidate"))
        rows.append(dict(
            id=q["id"], category=q["category"], behavior=q["behavior"], correct=correct, abstained=abstained,
            confident=confident, path=d["selected_path"], status=d["agreement_status"], value=str(value)[:120],
            expected=str(q["expected"])[:120], ms=round(ms, 1), tool_calls=tools, tokens=tokens,
            schema_link=set(q["expected_tables"]) <= tables if q["expected_tables"] else None,
            value_link=all(normalize_text(v) in filt_vals for v in q["expected_values"]) if q["expected_values"] else None,
            followup=bool((state.get("followup") or {}).get("repaired")), error=state.get("explorer_error") or state.get("hercules_error")))
    return {"mode": mode, "use_memory": use_memory, "rows": rows, "summary": summarize(rows)}


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    answerable = [r for r in rows if r["behavior"] == "answer"]
    ambiguous = [r for r in rows if r["behavior"] == "flag_ambiguity"]
    linked_s = [r["schema_link"] for r in answerable if r["schema_link"] is not None]
    linked_v = [r["value_link"] for r in answerable if r["value_link"] is not None]
    recon = [r for r in answerable if r["category"] in ("reconciliation", "cross_domain")]
    ms = [r["ms"] for r in rows]
    return {
        "questions": len(rows),
        "answer_accuracy": round(sum(r["correct"] for r in answerable) / max(len(answerable), 1), 3),
        "schema_linking_accuracy": round(sum(linked_s) / max(len(linked_s), 1), 3),
        "value_linking_accuracy": round(sum(linked_v) / max(len(linked_v), 1), 3),
        "reconciliation_accuracy": round(sum(r["correct"] for r in recon) / max(len(recon), 1), 3) if recon else None,
        "wrong_but_confident_rate": round(sum(1 for r in answerable if r["confident"] and not r["correct"]) / max(len(answerable), 1), 3),
        "abstention_on_ambiguous": round(sum(r["abstained"] for r in ambiguous) / max(len(ambiguous), 1), 3) if ambiguous else None,
        "unnecessary_abstention_rate": round(sum(r["abstained"] for r in answerable) / max(len(answerable), 1), 3),
        "avg_tool_calls": round(statistics.mean(r["tool_calls"] for r in rows), 1) if rows else 0,
        "avg_context_tokens": round(statistics.mean(r["tokens"] for r in rows)) if rows else 0,
        "latency_p50_ms": pctl(ms, 0.5), "latency_p95_ms": pctl(ms, 0.95),
        "followups": sum(1 for r in rows if r.get("followup")),
    }
