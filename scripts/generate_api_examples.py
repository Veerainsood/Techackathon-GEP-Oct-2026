"""Call the API in-process and record real request/response pairs into docs/API_EXAMPLES.md."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from udre.api.app import app  # noqa: E402


def trim(obj, depth=0):
    if isinstance(obj, list):
        return [trim(x, depth + 1) for x in obj[:4]] + ([f"... {len(obj) - 4} more"] if len(obj) > 4 else [])
    if isinstance(obj, dict):
        return {k: trim(v, depth + 1) for k, v in obj.items() if k not in ("trace",) or depth > 0}
    if isinstance(obj, str) and len(obj) > 400:
        return obj[:400] + "..."
    return obj


def main() -> None:
    c = TestClient(app)
    calls = [
        ("GET", "/health", None, {}),
        ("GET", "/sources", None, {"X-Tenant-Id": "default", "X-User-Id": "analyst@example.com"}),
        ("GET", "/sources/finance_pg/collections?q=customer%20invoices%20open%20balance", None, {}),
        ("POST", "/ask", {"question": "What is the total open balance in USD of invoices for debtors with payment terms NET45?",
                          "mode": "dual", "use_memory": True, "export_formats": ["excel"]}, {"X-User-Id": "analyst@example.com"}),
        ("POST", "/ask", {"question": "What was our revenue last year?", "mode": "dual"}, {}),
        ("POST", "/query", {"source_id": "finance_pg", "query": "UPDATE ledger_erp.sales_invoices SET open_balance_usd = 0"}, {}),
        ("GET", "/knowledge/search?q=closed%20won&k=3", None, {}),
        ("GET", "/knowledge/stats", None, {}),
    ]
    out = ["# API examples", "", "Captured from real calls against the running engine "
           "(`python scripts/generate_api_examples.py`). Long lists are truncated.", "",
           "Start the server with `uvicorn udre.api.app:app --port 8000`. Identity headers: `X-Tenant-Id`, `X-User-Id`.", ""]
    for method, path, body, headers in calls:
        r = c.request(method, path, json=body, headers=headers)
        out += [f"## {method} {path}", ""]
        if body:
            out += ["Request:", "", "```json", json.dumps(body, indent=2), "```", ""]
        out += [f"Response ({r.status_code}):", "", "```json", json.dumps(trim(r.json()), indent=2, default=str)[:6000], "```", ""]
        print(method, path, r.status_code)
    (ROOT / "docs" / "API_EXAMPLES.md").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
