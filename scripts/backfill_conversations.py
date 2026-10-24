"""Turn answers saved before conversations existed into one-turn conversations, so they appear in the side panel.

    python scripts/backfill_conversations.py --tenant default --user analyst

Saved answers record the tenant but not the user, so the owner must be given explicitly. Safe to run more than once:
answers that were already converted are skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import Settings, pg_uri  # noqa: E402
from backend.events.store import RuntimeStore  # noqa: E402
from backend.runtime.conversations import ConversationStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--user", required=True, help="owner of the converted conversations")
    args = ap.parse_args()
    uri = pg_uri(Settings().knowledge_db)
    answers, convs = RuntimeStore(uri), ConversationStore(uri)
    done = {r["answer_id"] for r in convs._exec(
        "SELECT meta->>'backfilled_from' AS answer_id FROM backend_runtime.messages WHERE meta ? 'backfilled_from'", fetch="all")}
    converted = 0
    for a in sorted(answers.answers(args.tenant, limit=1000), key=lambda r: r["created_at"]):
        if a["answer_id"] in done or not a.get("final_answer"):
            continue
        cid = convs.create(args.tenant, args.user, a["question"])
        convs.add_message(cid, "user", a["question"], {"backfilled_from": a["answer_id"]})
        note = f"\n\n(Out of date: {a['stale_reason']})" if a.get("stale") and a.get("stale_reason") else ""
        convs.add_message(cid, "assistant", a["final_answer"] + note, {"backfilled_from": a["answer_id"], "answer_id": a["answer_id"]})
        # keep the original time so the side panel shows them in the order they were asked
        convs._exec("UPDATE backend_runtime.conversations SET created_at = %s, updated_at = %s WHERE conversation_id = %s",
                    (a["created_at"], a["created_at"], cid))
        converted += 1
    print(f"converted {converted} saved answers into conversations for {args.tenant}/{args.user}")


if __name__ == "__main__":
    main()
