"""Discover, profile and build the ontology for every registered source.

Usage: python scripts/build_knowledge.py [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from udre.engine import build_engine  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    engine = build_engine()
    for sid in [s.source_id for s in engine.gateway.sources(engine.default_ctx)]:
        t0 = time.time()
        report = engine.builder.build(engine.default_ctx, sid, force=args.force)
        print(f"{sid}: {time.time() - t0:.1f}s {json.dumps(report.__dict__, default=str)}")
    print(json.dumps(engine.kg.stats(), indent=1))


if __name__ == "__main__":
    main()
