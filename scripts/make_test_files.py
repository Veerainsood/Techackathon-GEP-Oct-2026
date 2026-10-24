"""Write the upload test files into test_files/:

  approval_test/vendor_payments_review.(csv|xlsx)   problems that need a person (manual approval in the chat)
  leakage_audit_pack/                               14 AP files to upload together and ask to find the leaks
  leakage_audit_pack_answer_key.json                seeded leakage and legitimate look-alikes (not for upload)

Usage: python scripts/make_test_files.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.synthetic.audit_pack import write_approval_file, write_pack  # noqa: E402


def main() -> None:
    out = ROOT / "test_files"
    out.mkdir(exist_ok=True)
    for p in write_approval_file(out):
        print(f"approval test: {p.relative_to(ROOT)}")
    key = write_pack(out)
    files = sorted((out / "leakage_audit_pack").iterdir())
    print(f"audit pack: {len(files)} files in test_files/leakage_audit_pack")
    by_type = {}
    for c in key["cases"]:
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    print(f"answer key: {len(key['cases'])} leakage cases (${key['seeded_total_usd']:,.2f}) {by_type}, "
          f"{len(key['legitimate_exceptions'])} legitimate look-alikes -> test_files/leakage_audit_pack_answer_key.json")


if __name__ == "__main__":
    main()
