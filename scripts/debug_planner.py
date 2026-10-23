"""Debug the planner on the DEV split only (the held-out split is never used for tuning)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from udre.agents.catalog import view_from_context  # noqa: E402
from udre.agents.planner import SemanticPlanner, compile_sql, structured_value  # noqa: E402
from udre.compiler.semantic_compiler import SemanticContextCompiler  # noqa: E402
from udre.engine import build_engine  # noqa: E402
from udre.ontology.builder import load_packs  # noqa: E402


def main() -> None:
    e = build_engine()
    ctx = e.default_ctx
    compiled = SemanticContextCompiler(e.gateway, e.kg).compile(ctx)
    adapter = e.gateway.adapter(ctx, "finance_pg")
    view = view_from_context(compiled, "finance_pg", adapter.dialect, adapter.quote, load_packs())
    planner = SemanticPlanner(view)
    dev = json.loads((ROOT / "benchmark" / "dev.json").read_text())
    for q in dev:
        plan = planner.plan(q["question"])
        sql = compile_sql(plan, view)
        res = e.gateway.execute_query(ctx, "finance_pg", sql)
        val = structured_value(plan, res.rows) if res.succeeded else res.error_message
        print(f"\n{q['id']} {q['question']}\n  SQL: {sql}\n  got={val}\n  exp={q['expected']}\n  amb={plan.ambiguities}")


if __name__ == "__main__":
    main()
