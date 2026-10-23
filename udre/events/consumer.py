"""Event consumer: validate -> authenticate -> deduplicate -> persist -> (worker) claim -> investigate -> checkpoint.

An event is marked ``processed`` only after the investigation, watermark and version updates succeed.
Visibility problems defer the event with exponential backoff; repeated failures end in ``failed`` with the
error kept for operators. Workers claim with a lease, so a crashed worker's event is retried after expiry.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from pydantic import ValidationError

from udre.events.contracts import SyncEvent, verify_signature
from udre.events.store import RuntimeStore
from udre.runtime.investigator import ChangeInvestigator, EventRejected, NotYetVisible


class EventConsumer:
    def __init__(self, store: RuntimeStore, investigator: ChangeInvestigator, secret: Optional[str] = None,
                 allow_unsigned: Optional[bool] = None, max_attempts: Optional[int] = None,
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.store = store
        self.investigator = investigator
        self.secret = secret if secret is not None else os.environ.get("UDRE_EVENT_SECRET", "")
        self.allow_unsigned = allow_unsigned if allow_unsigned is not None else os.environ.get("UDRE_EVENT_ALLOW_UNSIGNED") == "1"
        self.max_attempts = max_attempts or investigator.policy.max_visibility_retries
        self.on_event = on_event or (lambda e: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ ingress
    def submit(self, body: bytes, signature: Optional[str]) -> Dict[str, Any]:
        if not verify_signature(body, signature, self.secret) and not self.allow_unsigned:
            return {"status": "rejected", "reason": "invalid or missing signature"}
        try:
            ev = SyncEvent.model_validate_json(body)
        except ValidationError as exc:
            return {"status": "rejected", "reason": "invalid event", "errors": json.loads(exc.json())[:10]}
        row = {"event_id": ev.event_id, "tenant_id": ev.tenant_id, "source_id": ev.source_id, "database_id": ev.database_id,
               "event_type": ev.event_type, "sequence": ev.sequence, "data_version": ev.data_version,
               "window_start": ev.window_start, "window_end": ev.window_end, "payload": json.loads(ev.model_dump_json(by_alias=True))}
        if not self.store.insert_event(row):
            return {"status": "duplicate", "event_id": ev.event_id}
        self.on_event({"event": "sync_event_received", "event_id": ev.event_id, "tenant_id": ev.tenant_id, "source_id": ev.source_id,
                       "data_version": ev.data_version})
        return {"status": "accepted", "event_id": ev.event_id}

    # ------------------------------------------------------------------ processing
    def process_one(self) -> Optional[Dict[str, Any]]:
        row = self.store.claim_next()
        if not row:
            return None
        ev = SyncEvent.model_validate(row["payload"])
        try:
            summary = self.investigator.process(ev)
        except NotYetVisible as exc:
            if row["attempts"] >= self.max_attempts:
                self.store.finish_event(ev.event_id, "failed", f"writes never became visible: {exc}")
                self.store.mark_sync_status(ev.tenant_id, ev.source_id, "visibility_timeout", str(exc))
                return self._emit(ev, "failed", {"error": str(exc)})
            backoff = min(2 ** row["attempts"], 300)
            self.store.finish_event(ev.event_id, "deferred", str(exc), retry_in_s=backoff)
            return self._emit(ev, "deferred", {"error": str(exc), "retry_in_s": backoff})
        except EventRejected as exc:
            self.store.finish_event(ev.event_id, "rejected", str(exc))
            return self._emit(ev, "rejected", {"error": str(exc)})
        except Exception as exc:  # transient or unexpected: retry with backoff, then fail visibly
            status = "failed" if row["attempts"] >= self.max_attempts else "deferred"
            self.store.finish_event(ev.event_id, status, f"{type(exc).__name__}: {exc}", retry_in_s=min(2 ** row["attempts"], 300))
            if status == "failed":
                self.store.mark_sync_status(ev.tenant_id, ev.source_id, "investigation_failed", str(exc)[:300])
            return self._emit(ev, status, {"error": f"{type(exc).__name__}: {exc}"})
        status = "processed"
        iid = self.store.record_investigation(ev.event_id, ev.tenant_id, ev.source_id, status, summary, summary.get("stage_b"))
        self.store.finish_event(ev.event_id, status, notes=summary.get("notes"))
        return self._emit(ev, status, {"investigation_id": iid, "summary": summary})

    def _emit(self, ev: SyncEvent, status: str, data: Dict[str, Any]) -> Dict[str, Any]:
        out = {"event": "sync_event_" + status, "event_id": ev.event_id, "tenant_id": ev.tenant_id, "source_id": ev.source_id,
               "data_version": ev.data_version, **data}
        self.on_event(out)
        return out

    def drain(self, max_events: int = 100) -> list:
        results = []
        for _ in range(max_events):
            r = self.process_one()
            if r is None:
                break
            results.append(r)
        return results

    # ------------------------------------------------------------------ background worker
    def start(self, poll_s: float = 1.0) -> None:
        if self._thread and self._thread.is_alive():
            return

        def loop():
            while not self._stop.is_set():
                try:
                    if self.process_one() is None:
                        self._stop.wait(poll_s)
                except Exception:
                    self._stop.wait(poll_s)

        self._stop.clear()
        self._thread = threading.Thread(target=loop, name="udre-event-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
