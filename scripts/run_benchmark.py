"""Run the benchmark in all four configurations and the source-migration check.

Usage: python scripts/run_benchmark.py [--split heldout|dev|all]
Writes benchmark/results.json and benchmark/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from udre.engine import build_engine  # noqa: E402
from udre.evaluation.benchmark import evaluate  # noqa: E402
from udre.gateway.registry import AccessContext  # noqa: E402


def load(split: str):
    return json.loads((ROOT / "benchmark" / f"{split}.json").read_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="heldout", choices=["heldout", "dev", "all"])
    args = ap.parse_args()
    engine = build_engine()
    ctx = engine.default_ctx
    for sid in [s.source_id for s in engine.gateway.sources(ctx)]:
        engine.builder.build(ctx, sid)
    splits = ["dev", "heldout"] if args.split == "all" else [args.split]
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "splits": {}}
    for split in splits:
        qs = load(split)
        res = {}
        for mode in ("explorer", "hercules", "dual"):
            res[mode] = evaluate(engine, qs, mode, use_memory=False)
            print(split, mode, json.dumps(res[mode]["summary"]))
        engine.memory.clear(ctx)
        if split == "heldout":  # memory may only be warmed on the dev split
            evaluate(engine, load("dev"), "dual", use_memory=True)
        res["dual_memory"] = evaluate(engine, qs, "dual", use_memory=True)
        print(split, "dual_memory", json.dumps(res["dual_memory"]["summary"]), engine.memory.stats(ctx))
        report["splits"][split] = res

    # source migration: same dev questions answered from the uploaded-file copy only, zero code changes
    t0 = time.time()
    onboarding = engine.builder.build(ctx, "uploads", force=True)
    onboarding_s = round(time.time() - t0, 1)
    files_only = AccessContext(allowed_sources={"uploads"})
    migration_qs = [q for q in load("dev") if q["id"] in ("D01", "D02", "D03", "D06", "D10")]
    mig = evaluate(engine, migration_qs, "dual", use_memory=False, ctx=files_only)
    report["source_migration"] = {"target": "uploads (CSV/Parquet/Excel/JSON via DuckDB)", "code_changes": 0,
                                  "onboarding_seconds": onboarding_s, "ontology": onboarding.__dict__, **mig}
    print("migration", json.dumps(mig["summary"]), "onboarding_s", onboarding_s)
    (ROOT / "benchmark" / "results.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (ROOT / "benchmark" / "RESULTS.md").write_text(render_md(report), encoding="utf-8")


def render_md(report) -> str:
    keys = ["answer_accuracy", "schema_linking_accuracy", "value_linking_accuracy", "reconciliation_accuracy",
            "wrong_but_confident_rate", "abstention_on_ambiguous", "unnecessary_abstention_rate", "avg_tool_calls",
            "avg_context_tokens", "latency_p50_ms", "latency_p95_ms", "followups"]
    out = [f"# Benchmark results ({report['generated_at']})", "",
           "Expected answers are computed independently from the generator's event model. "
           "No LLM was configured for this run: both agents used the deterministic planner, so 'context tokens' counts "
           "tokens of context and tool observations processed, not model tokens.", ""]
    for split, res in report["splits"].items():
        out += [f"## {split} split", "", "| metric | " + " | ".join(res) + " |", "|---|" + "---|" * len(res)]
        for k in keys:
            out.append(f"| {k} | " + " | ".join(str(res[m]["summary"].get(k)) for m in res) + " |")
        out += ["", "| id | " + " | ".join(res) + " |", "|---|" + "---|" * len(res)]
        for i, row in enumerate(res["dual"]["rows"]):
            cells = []
            for m in res:
                r = res[m]["rows"][i]
                cells.append(("PASS" if r["correct"] else "FAIL") + (" (abstain)" if r["abstained"] else ""))
            out.append(f"| {row['id']} | " + " | ".join(cells) + " |")
        out.append("")
    mig = report.get("source_migration")
    if mig:
        out += ["## Source migration", "", f"Target: {mig['target']}; code changes: {mig['code_changes']}; "
                f"onboarding (discovery + profiling + ontology): {mig['onboarding_seconds']} s; "
                f"answer accuracy: {mig['summary']['answer_accuracy']} on {mig['summary']['questions']} questions.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    main()
