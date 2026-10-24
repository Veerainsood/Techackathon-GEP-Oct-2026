"""Clients behind proxies that hold a streamed response back can poll the stream's events instead."""

import json


def test_stream_events_can_be_polled_by_the_owner_only(pg_uri, uploads_dir):
    from fastapi.testclient import TestClient

    from backend.api import app as api
    from backend.engine import build_engine

    e = build_engine(llm_providers=(None,) * 5)
    original = api.engine
    api.engine = lambda: e
    try:
        c = TestClient(api.app)
        h = {"X-Tenant-Id": "default", "X-User-Id": "poller"}
        sid = "test-stream-0001"
        streamed = [json.loads(line[6:]) for line in c.post("/ask/stream", headers=h, json={"question": "hi", "stream_id": sid}).text.split("\n")
                    if line.startswith("data: ")]
        polled = c.get(f"/ask/stream/{sid}/events?after=0", headers=h).json()
        assert polled["done"] is True and [x["seq"] for x in polled["events"]] == list(range(len(polled["events"])))
        assert polled["events"] == streamed and polled["events"][-1]["event"] == "result"
        tail = c.get(f"/ask/stream/{sid}/events?after={len(streamed) - 1}", headers=h).json()["events"]
        assert [x["event"] for x in tail] == ["result"]
        assert c.get(f"/ask/stream/{sid}/events", headers={**h, "X-User-Id": "someone-else"}).status_code == 404
        assert c.post("/ask/stream", headers=h, json={"question": "hi", "stream_id": sid}).status_code == 409
    finally:
        api.engine = original
