"""Load (or reload) only the procurement area and its leakage answer key, leaving the other schemas untouched.

    python scripts/load_procurement.py

build_dataset.py does this as part of a full rebuild; use this script on a database that already has live data
(for example HubSpot-synced CRM rows) that a full rebuild would reset.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402

from backend.synthetic.loader import get_postgres_uri  # noqa: E402
from backend.synthetic.procurement import ProcurementGenerator, load  # noqa: E402


def main() -> None:
    uri = get_postgres_uri()
    with psycopg2.connect(uri) as c, c.cursor() as cur:
        cur.execute("SELECT conglomerate_name, conglomerate_id FROM master_ref.client_conglomerates")
        groups = dict(cur.fetchall())
    data = ProcurementGenerator(groups=groups).generate()
    counts = load(data, uri)
    (ROOT / "benchmark" / "leakage_answer_key.json").write_text(
        json.dumps({"cases": data.answer_key, "legitimate_exceptions": data.exceptions}, indent=2, default=str), encoding="utf-8")
    print(f"procurement loaded: {counts}")
    print(f"{len(data.answer_key)} seeded leakage cases (${sum(c['recoverable_usd'] for c in data.answer_key):,.2f}), "
          f"{len(data.exceptions)} legitimate exceptions -> benchmark/leakage_answer_key.json")


if __name__ == "__main__":
    main()
