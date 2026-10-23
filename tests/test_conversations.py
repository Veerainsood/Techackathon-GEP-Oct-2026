"""Conversations: follow-ups see earlier turns and are rewritten into standalone questions, conversations are listed and
private to their owner, and attached files survive the loss of their in-memory workspace."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from backend.llm.scripted import ScriptedProvider, tool

H = {"X-Tenant-Id": "default", "X-User-Id": "conv-tester"}


@pytest.fixture(scope="module")
def client(pg_uri, uploads_dir):
    from backend.api import app as api
    from backend.engine import build_engine

    seen = []

    def intent_policy(system, messages, tools):
        text = json.dumps(messages[0]["content"])
        seen.append(text)
        if "invoices are overdue" in text and "and how many of those are overdue" in text:
            return tool("submit_intent", route="investigate", reason="follow-up about invoices",
                        standalone_question="How many invoices have settlement status OVERDUE?",
                        brief={"interpreted_question": "count OVERDUE invoices"})
        if "attached_files" in text:
            return tool("submit_intent", route="answer", reason="file question", answer="The file has an orders table.")
        return tool("submit_intent", route="answer", reason="greeting", answer="Hello! I can answer questions about invoices.")

    e = build_engine(llm_providers=(None, None, None, ScriptedProvider(intent_policy)))
    original = api.engine
    api.engine = lambda: e
    try:
        yield TestClient(api.app), e, seen
    finally:
        api.engine = original


def test_follow_up_uses_history_and_a_standalone_question(client):
    c, _e, seen = client
    first = c.post("/ask", json={"question": "hi, tell me which invoices are overdue", "use_memory": False}, headers=H).json()
    cid = first["conversation_id"]
    second = c.post("/ask", json={"question": "and how many of those are overdue?", "use_memory": False,
                                  "conversation_id": cid}, headers=H).json()
    assert second["conversation_id"] == cid
    assert "hi, tell me which invoices are overdue" in seen[-1]  # the earlier turn reached the intent agent
    assert second["intent"]["standalone_question"] == "How many invoices have settlement status OVERDUE?"
    assert second["question"] == "and how many of those are overdue?"  # the user's words are what the UI shows
    assert "13" in second["answer"] and second["verification_status"] == "verified_fact"


def test_conversations_are_listed_and_private(client):
    c, _e, _seen = client
    cid = c.post("/ask", json={"question": "hello", "use_memory": False}, headers=H).json()["conversation_id"]
    assert any(x["conversation_id"] == cid and x["title"] == "hello" for x in c.get("/conversations", headers=H).json())
    body = c.get(f"/conversations/{cid}", headers=H).json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"] and body["messages"][1]["meta"]["route"] == "answer"
    assert c.get(f"/conversations/{cid}", headers={"X-Tenant-Id": "default", "X-User-Id": "someone-else"}).status_code == 404
    assert c.post("/ask", json={"question": "x", "conversation_id": "conv-nope"}, headers=H).status_code == 404


def test_attached_file_survives_a_lost_workspace(client):
    c, e, _seen = client
    csv = b"order_id,region,amount\nO1,AMER,100\nO2,EMEA,250\n"
    fid = c.post("/files", files=[("files", ("orders.csv", io.BytesIO(csv), "text/csv"))], headers=H).json()[0]["file_id"]
    ws = c.post("/workspaces", json={"file_ids": [fid]}, headers=H).json()
    first = c.post("/ask", json={"question": "can you analyse the file?", "use_memory": False,
                                 "workspace_id": ws["workspace_id"]}, headers=H).json()
    cid = first["conversation_id"]
    assert c.get(f"/conversations/{cid}", headers=H).json()["conversation"]["files"][0]["filename"] == "orders.csv"
    from backend.gateway.registry import AccessContext

    e.workspaces.close(AccessContext(tenant_id="default", user_id="conv-tester"), ws["workspace_id"])  # e.g. a restart
    again = c.post("/ask", json={"question": "what is in the file?", "use_memory": False, "conversation_id": cid}, headers=H).json()
    assert again["workspace_id"] and again["workspace_id"] != ws["workspace_id"]
    assert again["answer"] == "The file has an orders table."  # the recreated workspace's file reached the intent agent
