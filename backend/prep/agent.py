"""Data-prep agent: reads a file's quality profile, investigates in the sandbox when needed, and proposes a plan made of
declarative operations (so every change is auditable and replayable). The code, not the model, decides which
operations are safe to apply automatically and which need a person's approval.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from backend.llm.base import LLMProvider, ToolSpec, Usage

Op = Literal["trim_whitespace", "standardize_case", "cast_numeric", "parse_dates", "normalize_values", "fill_missing",
             "flag_duplicates", "drop_exact_duplicates", "drop_rows_missing"]
# Applied without asking (fills and mappings are logged with their rule and can be reverted from the kept original).
SAFE_OPS = {"trim_whitespace", "standardize_case", "flag_duplicates", "fill_missing", "normalize_values"}
DROP_OPS = {"drop_exact_duplicates", "drop_rows_missing"}
# Safe only when every non-missing value converts; otherwise values would become missing, so a person decides.
LOSSLESS_IF_FULLY_PARSED = {"cast_numeric": "numeric_parse_rate", "parse_dates": "date_parse_rate"}


class Fix(BaseModel):
    op: Op
    columns: List[str] = Field(default_factory=list)
    params: Dict[str, Any] = Field(default_factory=dict)
    rationale: str


class Finding(BaseModel):
    title: str
    detail: str
    severity: Literal["info", "warning", "critical"] = "info"


class PrepPlan(BaseModel):
    summary: str
    findings: List[Finding] = Field(default_factory=list)
    fixes: List[Fix] = Field(default_factory=list)


SUBMIT = ToolSpec("submit_plan", "Submit the data-quality findings and the proposed fixes. Call exactly once.",
                  PrepPlan.model_json_schema())
RUN_PYTHON = ToolSpec("run_python", "Run Python (pandas, duckdb, numpy) in the sandbox to investigate the file. The file is at the "
                      "path given in the first message. Print what you need; output is truncated to 6000 characters.",
                      {"type": "object", "properties": {"gap": {"type": "string"}, "code": {"type": "string"}},
                       "required": ["gap", "code"]})


def classify(fix: Fix, profile: Dict[str, Any], source: str = "upload") -> str:
    """'auto', 'approval' or 'skipped' for one fix, decided from the operation, the measured profile and the data's origin.

    CRM batches are applied without approval; dropping records is skipped there, because the database would then miss
    records the CRM still has. For uploads, only operations that remove rows or turn values into blanks need approval."""
    if source != "upload":
        return "skipped" if fix.op in DROP_OPS else "auto"
    if fix.op in SAFE_OPS:
        return "auto"
    if fix.op in DROP_OPS:
        return "approval"
    rate_key = LOSSLESS_IF_FULLY_PARSED.get(fix.op)
    if rate_key:
        cols = {c["name"]: c for c in profile["columns"]}
        ambiguous = fix.op == "parse_dates" and not fix.params.get("format") and \
            any(cols.get(c, {}).get("ambiguous_dates", 0) for c in fix.columns)
        if fix.columns and not ambiguous and all(cols.get(c, {}).get(rate_key, 0) >= 1.0 for c in fix.columns):
            return "auto"
    return "approval"


def validate(plan: PrepPlan, profile: Dict[str, Any]) -> Optional[str]:
    known = {c["name"] for c in profile["columns"]}
    for f in plan.fixes:
        missing = [c for c in f.columns if c not in known]
        if missing:
            return f"{f.op}: unknown columns {missing}; columns are {sorted(known)}"
        if f.op not in ("flag_duplicates", "drop_exact_duplicates") and not f.columns:
            return f"{f.op} needs at least one column"
        if f.op == "fill_missing" and f.params.get("method") not in ("median", "mean", "mode", "constant", "ffill", "product"):
            return "fill_missing needs params.method in median, mean, mode, constant, ffill, product"
        if f.op == "fill_missing" and f.params.get("method") == "constant" and any(c in str(f.params.get("value", "")) for c in known):
            return (f"fill_missing: the constant {f.params.get('value')!r} names a column; constants are written as text. "
                    "For a value computed from two columns use method 'product' with params.of [column_a, column_b]")
        if f.op == "fill_missing" and f.params.get("method") == "product" and (
                len(f.params.get("of") or []) != 2 or any(c not in known for c in f.params.get("of") or [])):
            return "fill_missing method 'product' needs params.of with exactly two existing columns"
        if f.op == "normalize_values" and not isinstance(f.params.get("mapping"), dict):
            return "normalize_values needs params.mapping {old: new}"
    return None


def rules_plan(profile: Dict[str, Any]) -> PrepPlan:
    """Deterministic plan used without an LLM: conservative fixes only; nothing is removed."""
    fixes: List[Fix] = []
    findings: List[Finding] = []
    for c in profile["columns"]:
        name = c["name"]
        if c["whitespace_issues"]:
            fixes.append(Fix(op="trim_whitespace", columns=[name], rationale=f"{c['whitespace_issues']} values have stray spaces"))
        text_typed = c["dtype"] in ("object", "str", "string")
        if text_typed and c["nulls"] < profile["rows"] and c["numeric_parse_rate"] >= 0.95 and not c["id_like"]:
            fixes.append(Fix(op="cast_numeric", columns=[name], rationale=f"{c['numeric_parse_rate']:.0%} of values are numbers"))
        elif text_typed and c.get("date_parse_rate", 0) >= 0.95:
            fixes.append(Fix(op="parse_dates", columns=[name], rationale=f"{c['date_parse_rate']:.0%} of values are dates"))
        money = re.search(r"amount|paid|price|total|value|cost|balance|fee|tax|usd|eur|inr|gbp", name, re.I)
        if 0 < c["nulls"] and c["null_pct"] < 0.5 and not c["id_like"] and c["numeric_parse_rate"] >= 0.95 and not money:
            # text gaps (names, titles, emails) are facts that cannot be inferred, and a missing amount usually means
            # "none" (e.g. not paid yet): both are left missing
            fixes.append(Fix(op="fill_missing", columns=[name], params={"method": "median"},
                             rationale=f"{c['nulls']} missing values filled with the median"))
        if c.get("duplicate_ids"):
            findings.append(Finding(title=f"Repeated {name}", severity="warning",
                                    detail=f"{c['duplicate_ids']} rows share an {name}; check whether these are duplicates or valid repeats"))
        if c["null_pct"] >= 0.5:
            findings.append(Finding(title=f"Mostly empty column {name}", severity="warning", detail=f"{c['null_pct']:.0%} missing"))
    d = profile["duplicates"]
    if d["exact_rows"]:
        fixes.append(Fix(op="flag_duplicates", rationale="identical rows are flagged, not removed: they may be valid repeats"))
        findings.append(Finding(title="Identical rows", severity="warning",
                                detail=f"{d['exact_rows']} rows in {d['exact_groups']} groups are identical (rows {d['exact_row_numbers'][:8]}). "
                                       "They could be load errors or genuine repeats such as duplicate subscriptions; nothing was removed."))
    return PrepPlan(summary=f"{profile['rows']} rows, {len(profile['columns'])} columns checked with rules", findings=findings, fixes=fixes)


class DataPrepAgent:
    name = "DataPrep"

    def __init__(self, llm: Optional[LLMProvider] = None, max_turns: int = 8):
        self.llm = llm
        self.max_turns = max_turns

    def plan(self, profile: Dict[str, Any], filename: str, data_path: str, source: str,
             run_python: Callable[[str], Dict[str, Any]], on_event: Optional[Callable[[Dict[str, Any]], None]] = None
             ) -> Tuple[PrepPlan, Dict[str, Any]]:
        if self.llm is None:
            return rules_plan(profile), {"mode": "rules"}
        try:
            return asyncio.run(self._plan_llm(profile, filename, data_path, source, run_python, on_event or (lambda e: None)))
        except Exception as exc:  # model unavailable or no valid plan: conservative rules, and say so
            return rules_plan(profile), {"mode": "rules_fallback", "failure": f"{type(exc).__name__}: {str(exc)[:200]}"}

    async def _plan_llm(self, profile, filename, data_path, source, run_python, emit):
        from backend.agents.llm_agent import serialize_result
        from backend.agents.prompts import PREP_SYSTEM

        usage = Usage()
        user = (f"File: {filename} (source: {source}); sandbox path: {data_path}\n\nQuality profile:\n"
                + serialize_result("profile", profile))
        messages: List[Dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": user}]}]
        investigations = 0
        for turn in range(self.max_turns):
            last = turn == self.max_turns - 1
            resp = await self.llm.acomplete(PREP_SYSTEM, messages, [SUBMIT] if last else [RUN_PYTHON, SUBMIT],
                                            tool_choice="submit_plan" if last else None, max_tokens=3000, usage=usage)
            messages.append({"role": "assistant", "content": resp.raw_content or [{"type": "text", "text": resp.text or "(no content)"}]})
            if resp.text:
                emit({"agent": self.name, "event": "model_text", "ts": round(time.time(), 3), "text": resp.text[:500]})
            if not resp.tool_calls:
                messages.append({"role": "user", "content": [{"type": "text", "text": "Investigate with run_python or call submit_plan."}]})
                continue
            results = []
            for call in resp.tool_calls:
                if call.name == "submit_plan":
                    try:
                        plan = PrepPlan.model_validate(call.arguments)
                    except ValidationError as exc:
                        results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                        "content": f"Invalid plan: {exc.errors()[:5]}"})
                        continue
                    problem = validate(plan, profile)
                    if problem:
                        results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True, "content": problem})
                        continue
                    return plan, {"mode": "llm", "model": self.llm.model, "usage": usage.as_dict(), "investigations": investigations}
                if call.name == "run_python":
                    code = str(call.arguments.get("code", ""))
                    out = await asyncio.to_thread(run_python, code)
                    investigations += 1
                    emit({"agent": self.name, "event": "tool", "tool": "run_python", "objective": call.arguments.get("gap", ""),
                          "code": code[:2000], "exit_code": out.get("exit_code"), "stdout": (out.get("stdout") or "")[:2000]})
                    body = json.dumps({"exit_code": out.get("exit_code"), "stdout": (out.get("stdout") or "")[:6000],
                                       "stderr": (out.get("stderr") or "")[:2000]})
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": serialize_result("run_python", body)})
                else:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                    "content": f"Tool {call.name} is not available."})
            messages.append({"role": "user", "content": results})
        raise RuntimeError("data-prep agent did not submit a plan")
