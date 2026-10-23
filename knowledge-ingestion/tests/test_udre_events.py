"""Tests for publishing sync events to UDRE. Run from the knowledge-ingestion folder:

    .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v

The tests use a temporary SQLite database and a local fake UDRE endpoint. They do not call HubSpot.
If the UDRE repository is next to this folder, each event is also validated against UDRE's SyncEvent model.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from knowledge_ingestion.config import UdreConfig, config
from knowledge_ingestion.publish import udre_events
from knowledge_ingestion.publish.udre_events import (
    build_failed_event,
    build_success_events,
    deliver_pending,
)
from knowledge_ingestion.storage.sql_store import SqlStore

SECRET = "test-secret"
SOURCE = "hubspot"
REPO_ROOT = Path(__file__).resolve().parents[2]

T0 = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=5)
T2 = T1 + timedelta(minutes=5)


def _try_import_udre_model():
    """Use UDRE's own contract model when the UDRE code is in the same repository."""
    if not (REPO_ROOT / "udre" / "events" / "contracts.py").exists():
        return None
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from udre.events.contracts import SyncEvent, verify_signature  # noqa: WPS433

        return SyncEvent, verify_signature
    except Exception:
        return None
    finally:
        sys.path.pop(0)


UDRE_CONTRACT = _try_import_udre_model()


def _udre_config(url: str | None) -> UdreConfig:
    return UdreConfig(events_url=url, secret=SECRET, tenant_id="default", source_id="hubspot", database_id=None)


class FakeUdre:
    """Stands in for POST /events/sync. Verifies the signature and records the bodies it accepted."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.status_override: int | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if outer.status_override is not None:
                    return self._reply(outer.status_override, {"detail": "forced"})
                expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, self.headers.get("X-UDRE-Signature", "")):
                    return self._reply(401, {"status": "rejected", "reason": "invalid or missing signature"})
                if UDRE_CONTRACT is not None:
                    try:
                        UDRE_CONTRACT[0].model_validate_json(body)
                    except Exception as error:
                        return self._reply(400, {"status": "rejected", "reason": "invalid event", "error": str(error)})
                outer.received.append(body)
                self._reply(200, {"status": "accepted"})

            def _reply(self, code: int, payload: dict) -> None:
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:  # keep test output quiet
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class OutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SqlStore(f"sqlite:///{Path(self._tmp.name) / 'test.db'}")
        self.store.init_schema()

    def tearDown(self) -> None:
        self.store._engine.dispose()
        self._tmp.cleanup()

    def test_success_events_are_committed_with_the_run(self) -> None:
        run_id = self.store.start_run(SOURCE)
        changes = [("contacts", "insert", "101"), ("companies", "update", "9")]
        events = build_success_events(run_id, T0, T1, changes, published_at=T1)
        self.store.save_snapshot(run_id, SOURCE, T1, {"contacts": [("101", {})]}, cursors={"contacts": 1}, outbox=events)

        pending = self.store.pending_outbox(SOURCE, 10)
        self.assertEqual([p["event_id"] for p in pending], [f"hubspot-run{run_id}-part0"])
        self.assertEqual(self.store.last_outbox_window_end(SOURCE), T1)

    def test_failed_run_still_queues_its_failure_event(self) -> None:
        run_id = self.store.start_run(SOURCE)
        self.store.fail_run(run_id, "HubSpot is down", outbox=[build_failed_event(run_id, T0, T1, "HubSpot is down", T1)])
        pending = self.store.pending_outbox(SOURCE, 10)
        self.assertEqual(len(pending), 1)
        self.assertIn("database.sync.failed", pending[0]["body"])
        self.assertEqual(self.store.last_outbox_window_end(SOURCE), T1)

    def test_no_changes_queue_no_event(self) -> None:
        self.assertEqual(build_success_events(1, T0, T1, [], published_at=T1), [])

    def test_large_change_sets_are_split_into_parts_with_increasing_sequence(self) -> None:
        changes = [("contacts", "update", str(i)) for i in range(2500)]
        events = build_success_events(7, T0, T1, changes, published_at=T1)
        self.assertEqual(len(events), 3)
        sequences = [json.loads(e.body)["sequence"] for e in events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), 3)
        counts = [
            sum(c["expected_changes"] for c in json.loads(e.body)["collections"]) for e in events
        ]
        self.assertEqual(counts, [1000, 1000, 500])


class DeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SqlStore(f"sqlite:///{Path(self._tmp.name) / 'test.db'}")
        self.store.init_schema()
        self.udre = FakeUdre()

    def tearDown(self) -> None:
        self.udre.close()
        self.store._engine.dispose()
        self._tmp.cleanup()

    def _queue(self, run_id: int, window_start: datetime, window_end: datetime, changes) -> None:
        events = build_success_events(run_id, window_start, window_end, changes, published_at=window_end)
        self.store.save_snapshot(run_id, SOURCE, window_end, {}, outbox=events)

    def test_delivers_signed_events_in_order(self) -> None:
        self._queue(1, T0, T1, [("contacts", "insert", "101")])
        self._queue(2, T1, T2, [("companies", "update", "9")])
        with mock.patch.object(udre_events, "config", replace(config, udre=_udre_config(self.udre.url))):
            delivered = deliver_pending(self.store, SOURCE)
        self.assertEqual(delivered, 2)
        self.assertEqual(self.store.pending_outbox(SOURCE, 10), [])
        first, second = (json.loads(b) for b in self.udre.received)
        self.assertEqual(first["event_id"], "hubspot-run1-part0")
        self.assertEqual(second["event_id"], "hubspot-run2-part0")
        self.assertEqual(first["collections"][0]["changes"][0]["key"], {"contact_id": "101"})
        self.assertEqual(second["collections"][1]["changes"][0]["key"], {"crm_account_id": "9"})

    def test_unreachable_udre_keeps_events_queued(self) -> None:
        self._queue(1, T0, T1, [("contacts", "insert", "101")])
        with mock.patch.object(udre_events, "config", replace(config, udre=_udre_config("http://127.0.0.1:9"))):
            self.assertEqual(deliver_pending(self.store, SOURCE), 0)
        self.assertEqual(len(self.store.pending_outbox(SOURCE, 10)), 1)

    def test_wrong_secret_keeps_events_queued_and_stops(self) -> None:
        self._queue(1, T0, T1, [("contacts", "insert", "101")])
        self._queue(2, T1, T2, [("contacts", "insert", "102")])
        bad = replace(_udre_config(self.udre.url), secret="wrong")
        with mock.patch.object(udre_events, "config", replace(config, udre=bad)):
            self.assertEqual(deliver_pending(self.store, SOURCE), 0)
        self.assertEqual(len(self.store.pending_outbox(SOURCE, 10)), 2)

    def test_server_error_keeps_events_queued(self) -> None:
        self._queue(1, T0, T1, [("contacts", "insert", "101")])
        self.udre.status_override = 503
        with mock.patch.object(udre_events, "config", replace(config, udre=_udre_config(self.udre.url))):
            deliver_pending(self.store, SOURCE)
        self.assertEqual(len(self.store.pending_outbox(SOURCE, 10)), 1)

    def test_invalid_event_is_marked_rejected_and_not_retried(self) -> None:
        self._queue(1, T0, T1, [("contacts", "insert", "101")])
        self.udre.status_override = 400
        with mock.patch.object(udre_events, "config", replace(config, udre=_udre_config(self.udre.url))):
            deliver_pending(self.store, SOURCE)
        self.assertEqual(self.store.pending_outbox(SOURCE, 10), [])

    def test_disabled_publishing_sends_nothing(self) -> None:
        self._queue(1, T0, T1, [("contacts", "insert", "101")])
        with mock.patch.object(udre_events, "config", replace(config, udre=_udre_config(None))):
            self.assertEqual(deliver_pending(self.store, SOURCE), 0)
        self.assertEqual(len(self.store.pending_outbox(SOURCE, 10)), 1)


class ContractTests(unittest.TestCase):
    @unittest.skipIf(UDRE_CONTRACT is None, "UDRE repository not found next to this folder")
    def test_built_events_match_udre_contract(self) -> None:
        SyncEvent, verify_signature = UDRE_CONTRACT
        with mock.patch.object(udre_events, "config", replace(config, udre=_udre_config("http://unused"))):
            events = build_success_events(
                3, T0, T1, [("contacts", "insert", "101"), ("companies", "update", "9")], published_at=T1
            )
            failed = build_failed_event(4, T1, T2, "HubSpot is down", T2)
        for event in [*events, failed]:
            body = event.body.encode()
            model = SyncEvent.model_validate_json(body)
            self.assertTrue(verify_signature(body, hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(), SECRET))
            self.assertLess(model.window_start, model.window_end)


if __name__ == "__main__":
    unittest.main()
