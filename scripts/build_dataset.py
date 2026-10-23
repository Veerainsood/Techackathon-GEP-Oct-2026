"""Generate the synthetic dataset, load it into PostgreSQL, export files, and validate it.

Usage: python scripts/build_dataset.py [--seed 20261023] [--skip-files]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402

from backend.synthetic.generator import SEED, SyntheticFinanceGenerator  # noqa: E402
from backend.synthetic.ground_truth import FINANCIAL_INVARIANTS, build_benchmark  # noqa: E402
from backend.synthetic.loader import export_files, get_postgres_uri, load_to_postgres  # noqa: E402
from backend.synthetic.schema_spec import generate_postgresql_ddl, generate_schema_mapping_markdown  # noqa: E402


def check_benchmark_sql(cur, bench) -> list[tuple[str, bool]]:
    results = []
    for q in bench:
        if q["behavior"] != "answer":
            continue
        cur.execute(q["verification_sql"])
        rows = cur.fetchall()
        exp = q["expected"]
        if q["answer_type"] == "scalar":
            got = None if rows[0][0] is None else float(rows[0][0])
            if q["sign_insensitive"] and got is not None:
                got, exp = abs(got), abs(exp)
            ok = got is not None and abs(got - float(exp)) < 0.006
        else:
            got_map = {str(r[0]): float(r[1]) for r in rows}
            ok = set(got_map) == set(exp) and all(abs(got_map[k] - exp[k]) < 0.006 for k in exp)
        results.append((q["id"], ok))
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--skip-files", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    gen = SyntheticFinanceGenerator(seed=args.seed)
    frames = gen.generate()
    print(f"generated {sum(len(f) for f in frames.values())} rows across {len(frames)} tables in {time.time() - t0:.1f}s")

    uri = get_postgres_uri()
    t0 = time.time()
    load_to_postgres(frames, uri)
    print(f"loaded into {uri} in {time.time() - t0:.1f}s")

    (ROOT / "sql").mkdir(exist_ok=True)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "benchmark").mkdir(exist_ok=True)
    (ROOT / "sql" / "postgres_ddl.sql").write_text(generate_postgresql_ddl(), encoding="utf-8")
    (ROOT / "docs" / "SCHEMA_MAPPING.md").write_text(generate_schema_mapping_markdown(), encoding="utf-8")
    bench = build_benchmark(gen)
    for split in ("dev", "heldout", "heldout2", "heldout3"):
        subset = [q for q in bench if q["split"] == split]
        (ROOT / "benchmark" / f"{split}.json").write_text(json.dumps(subset, indent=2, default=str), encoding="utf-8")
    if not args.skip_files:
        written = export_files(frames, str(ROOT / "data"))
        print(f"exported {len(written)} upload files to data/uploads")

    conn = psycopg2.connect(uri)
    failures = 0
    with conn.cursor() as cur:
        for inv in FINANCIAL_INVARIANTS:
            cur.execute(inv["sql"])
            violations = cur.fetchone()[0]
            failures += violations != 0
            print(f"invariant {inv['name']:<45} violations={violations}")
        for qid, ok in check_benchmark_sql(cur, bench):
            failures += not ok
            print(f"benchmark {qid} sql==ground_truth: {ok}")
    conn.close()
    print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
