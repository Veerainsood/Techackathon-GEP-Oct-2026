"""End-to-end demo: build (or reuse) the dataset and knowledge, then answer questions with lineage.

Usage: python scripts/run_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402

from backend.engine import build_engine  # noqa: E402
from backend.gateway.registry import AccessContext  # noqa: E402
from backend.synthetic.generator import SyntheticFinanceGenerator  # noqa: E402
from backend.synthetic.loader import export_files, get_postgres_uri, load_to_postgres  # noqa: E402

QUESTIONS = [
    ("finance_pg + uploads", None, "What is the total open balance in USD by settlement status?"),
    ("cross-domain join", None, "Which are the top 5 conglomerates by total invoice subtotal in USD, excluding VOID invoices?"),
    ("ambiguous on purpose", None, "What was our revenue last year?"),
    ("uploaded files only", {"uploads"}, "What is the total gross invoice amount in USD for invoices billed in EUR currency?"),
]


def ensure_dataset() -> None:
    uri = get_postgres_uri()
    with psycopg2.connect(uri) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('ledger_erp.sales_invoices')")
        loaded = cur.fetchone()[0] is not None
    if not loaded or not (ROOT / "data" / "uploads").exists():
        print("generating synthetic dataset (seed 20261023)...")
        frames = SyntheticFinanceGenerator().generate()
        load_to_postgres(frames, uri)
        export_files(frames, str(ROOT / "data"))


def main() -> None:
    ensure_dataset()
    engine = build_engine()
    ctx = engine.default_ctx
    for sid in [s.source_id for s in engine.gateway.sources(ctx)]:
        r = engine.builder.build(ctx, sid)
        print(f"ontology {sid}: {'up to date' if r.skipped else f'built {r.collections} collections, {r.columns} columns'}")
    stats = engine.compiled_context(ctx).compression_stats
    print(f"compressed context: {stats.raw_tokens} -> {stats.compressed_tokens} tokens "
          f"({stats.token_reduction_pct}% reduction, {stats.compression_ratio}x)\n")
    for label, sources, question in QUESTIONS:
        qctx = AccessContext(allowed_sources=sources) if sources else ctx
        state = engine.ask(question, ctx=qctx, export_formats=["excel", "markdown"])
        d = state["decision"]
        print("=" * 100)
        print(f"[{label}] {question}")
        print(f"ANSWER: {d['final_answer']}")
        print(f"status={d['verification_status']} path={d['selected_path']} agreement={d['agreement_status']} "
              f"confidence={d['confidence_score']} time={state['total_ms']:.0f}ms")
        for line in d["query_lineage"]:
            print(f"  lineage  {line}")
        for f in d["validation_findings"]:
            if f["severity"] != "info" or not f["passed"]:
                print(f"  finding  [{f['severity']}] {f['validator_name']}: {f['message'][:140]}")
        for a in d["unresolved_assumptions"][:3]:
            print(f"  assume   {a[:160]}")
        for x in state.get("exports", []):
            print(f"  export   {x['path']}")
    print("\nknowledge graph:", json.dumps({n["type"]: n["count"] for n in engine.kg.stats()["nodes"]
                                             if n["status"] != "stale_schema"}))
    print("memory:", engine.memory.stats(ctx))


if __name__ == "__main__":
    main()
