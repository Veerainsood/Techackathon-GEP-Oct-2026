"""Score detected leakage cases against the seeded answer key.

A seeded case counts as detected when a case of the same type references it and the recoverable value is within 1%
(or $1). False positives are detected cases with no seeded counterpart; flagged legitimate exceptions are reported
separately because they are the false positives the acceptance criteria care about most.
"""

from __future__ import annotations

from typing import Any, Dict, List


def score(cases: List[Dict[str, Any]], answer_key: Dict[str, Any]) -> Dict[str, Any]:
    seeded = answer_key["cases"]
    found = {(c["type"], c["ref"]): c for c in cases}
    detected, wrong_value, missed = [], [], []
    for s in seeded:
        c = found.get((s["type"], s["ref"]))
        if c is None:
            missed.append(s)
        elif abs(c["recoverable_usd"] - s["recoverable_usd"]) <= max(1.0, 0.01 * s["recoverable_usd"]):
            detected.append(s)
        else:
            wrong_value.append({**s, "detected_usd": c["recoverable_usd"]})
    keys = {(s["type"], s["ref"]) for s in seeded}
    false_positives = [c for c in cases if (c["type"], c["ref"]) not in keys]
    exceptions_flagged = [e for e in answer_key.get("legitimate_exceptions", []) if (e["type"], e["ref"]) in found]
    by_type: Dict[str, Dict[str, int]] = {}
    for s in seeded:
        t = by_type.setdefault(s["type"], {"seeded": 0, "detected": 0})
        t["seeded"] += 1
        t["detected"] += int(s in detected)
    return {"seeded": len(seeded), "detected": len(detected), "detection_rate": round(len(detected) / max(1, len(seeded)), 3),
            "wrong_value": wrong_value, "missed": missed, "flagged": len(cases), "false_positives": false_positives,
            "false_positive_rate": round(len(false_positives) / max(1, len(cases)), 3), "exceptions_flagged": exceptions_flagged,
            "recoverable_seeded_usd": round(sum(s["recoverable_usd"] for s in seeded), 2),
            "recoverable_detected_usd": round(sum(s["recoverable_usd"] for s in detected), 2), "by_type": by_type}
