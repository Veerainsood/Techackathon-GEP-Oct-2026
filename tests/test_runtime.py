"""Event-driven runtime: contract, consumer, scoped investigation, versions, snapshots, invalidation, adjudication,
sandbox, uploads and streaming.  Uses a dedicated ``udre_sync_lab`` database written by a simulated ingestion
service; external services (LLM, Daytona) are mocked here -- see tests/test_live_smoke.py for live runs."""

import io
import json
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from udre.events.contracts import SyncEvent, json_schema, sign
from udre.gateway.registry import AccessContext

TENANT = "t_runtime"
SECRET = "test-secret"
T0 = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)


def lab_reset(uri: str) -> None:
    with psycopg2.connect(uri) as c, c.cursor() as cur:
        cur.execute("""
            DROP SCHEMA IF EXISTS lab CASCADE; CREATE SCHEMA lab;
            CREATE TABLE lab.customers (customer_id VARCHAR(20) PRIMARY KEY, name VARCHAR(80) NOT NULL, region VARCHAR(20) NOT NULL,
                is_deleted BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL);
            CREATE TABLE lab.invoices (invoice_id VARCHAR(20) PRIMARY KEY, customer_id VARCHAR(20) NOT NULL REFERENCES lab.customers(customer_id),
                amount NUMERIC(12,2) NOT NULL, currency VARCHAR(3) NOT NULL, status VARCHAR(20) NOT NULL, updated_at TIMESTAMP NOT NULL);
            CREATE TABLE lab.payments (payment_id VARCHAR(20) PRIMARY KEY, invoice_id VARCHAR(20) NOT NULL REFERENCES lab.invoices(invoice_id),
                amount NUMERIC(12,2) NOT NULL, updated_at TIMESTAMP NOT NULL);
            CREATE TABLE lab.audit_notes (note_id VARCHAR(20) PRIMARY KEY, note TEXT NOT NULL, written VARCHAR(30) NOT NULL);
        """)
        base = datetime(2026, 10, 23, 8, 0)
        for i in range(20):
            cur.execute("INSERT INTO lab.customers VALUES (%s,%s,%s,FALSE,%s)", (f"C{i:03d}", f"Customer {i}", ["AMER", "EMEA"][i % 2],
                                                                                 base.replace(tzinfo=timezone.utc)))
        for i in range(100):
            cur.execute("INSERT INTO lab.invoices VALUES (%s,%s,%s,%s,%s,%s)",
                        (f"I{i:04d}", f"C{i % 20:03d}", 100 + i, ["USD", "EUR"][i % 2], ["OPEN", "PAID"][i % 3 == 0], base))
        for i in range(60):
            cur.execute("INSERT INTO lab.payments VALUES (%s,%s,%s,%s)", (f"P{i:04d}", f"I{i:04d}", 50 + i, base))
        cur.execute("INSERT INTO lab.audit_notes VALUES ('N1', 'created', '2026-10-23')")


@pytest.fixture(scope="module")
def rt(pg_uri, uploads_dir):
    from udre import pg_embedded
    from udre.engine import build_engine
    from udre.gateway.postgres_adapter import PostgresSourceAdapter

    lab_uri = pg_embedded.ensure_database("udre_sync_lab")
    lab_reset(lab_uri)
    e = build_engine(llm_providers=(None, None, None))
    e.runtime_store.reset_tenant(TENANT)
    e.kg.delete_tenant(TENANT)
    e.contexts.delete_tenant(TENANT)
    e.gateway.register(PostgresSourceAdapter("sync_lab", lab_uri, include_schemas=["lab"]), tenants=[TENANT])
    ctx = AccessContext(tenant_id=TENANT, user_id="analyst")
    e.prepare_source(ctx, "sync_lab")
    e.consumer.secret = SECRET
    e.consumer.allow_unsigned = False
    yield {"e": e, "ctx": ctx, "lab": lab_uri}
    e.kg.delete_tenant(TENANT)
    e.contexts.delete_tenant(TENANT)
    e.runtime_store.reset_tenant(TENANT)


_seq = iter(range(1, 10_000))


def event(**kw):
    n = next(_seq)
    ev = {"event_id": f"evt-{n}", "event_type": "database.sync.completed", "tenant_id": TENANT, "source_id": "sync_lab",
          "database_id": "lab", "window_start": T0.isoformat(), "window_end": (T0 + timedelta(minutes=15)).isoformat(),
          "data_version": f"v{n}", "status": "completed",
          "collections": [{"name": "lab.invoices", "timestamp_column": "updated_at"}]}
    ev.update(kw)
    return ev


def submit(rt, ev, secret=SECRET):
    body = json.dumps(ev).encode()
    return rt["e"].consumer.submit(body, sign(body, secret))


def process(rt, ev):
    assert submit(rt, ev)["status"] == "accepted"
    return [r for r in rt["e"].consumer.drain() if r["event_id"] == ev["event_id"]][-1]


def write(rt, sql, params=()):
    with psycopg2.connect(rt["lab"]) as c, c.cursor() as cur:
        cur.execute(sql, params)


def at(minutes, tz=False):
    t = T0 + timedelta(minutes=minutes)
    return t if tz else t.replace(tzinfo=None)


def summary(res):
    return res["summary"]


def coll(res, name):
    return next(c for c in summary(res)["collections"] if c["name"] == name)


# ------------------------------------------------------------------ contract + ingress
def test_contract_validation_signature_and_duplicates(rt):
    assert json_schema()["$id"] == "udre.sync-event.v1"
    with pytest.raises(ValueError):
        SyncEvent.model_validate(event(window_end=T0.isoformat()))
    bad = event()
    body = json.dumps(bad).encode()
    assert rt["e"].consumer.submit(body, "deadbeef")["status"] == "rejected"
    assert rt["e"].consumer.submit(b'{"event_id": 1}', sign(b'{"event_id": 1}', SECRET))["status"] == "rejected"
    ev = event()
    assert submit(rt, ev)["status"] == "accepted"
    assert submit(rt, ev)["status"] == "duplicate"
    rt["e"].consumer.drain()


def test_one_collection_updated_with_parents_and_dependents(rt):
    write(rt, "UPDATE lab.invoices SET status = 'PAID', updated_at = %s WHERE invoice_id IN ('I0001','I0002','I0003')", (at(5),))
    res = process(rt, event())
    assert res["event"] == "sync_event_processed"
    c = coll(res, "lab.invoices")
    assert c["method"] == "timestamp_window" and c["changed_rows"] == 3 and set(c["changed_keys_sample"]) == {"I0001", "I0002", "I0003"}
    assert any(p["collection"] == "lab.customers" for p in c["affected_parents"])
    dep = next(d for d in c["affected_dependents"] if d["collection"] == "lab.payments")
    assert dep["affected_rows"] == 3
    assert summary(res)["knowledge"]["action"] == "profiles_refreshed"
    assert "timestamp-only event" in " ".join(c["notes"])
    v = rt["e"].runtime_store.source_version(TENANT, "sync_lab")
    assert v["sync_status"] == "ok" and v["data_version"].startswith("v")


def test_multiple_collections_and_data_only_refresh(rt):
    e = rt["e"]
    schema_before = e.kg.source_version("sync_lab", TENANT)
    old_ctx = e.contexts.current(TENANT, "sync_lab")
    write(rt, "UPDATE lab.invoices SET amount = amount + 1, updated_at = %s WHERE invoice_id = 'I0010'", (at(20),))
    write(rt, "UPDATE lab.customers SET region = 'APAC', updated_at = %s WHERE customer_id = 'C005'", (at(21, tz=True),))
    res = process(rt, event(window_start=at(15, tz=True).isoformat(), window_end=at(30, tz=True).isoformat(),
                            collections=[{"name": "lab.invoices", "timestamp_column": "updated_at"},
                                         {"name": "lab.customers", "timestamp_column": "updated_at"}]))
    assert set(summary(res)["changed_collections"]) == {"lab.invoices", "lab.customers"}
    assert e.kg.source_version("sync_lab", TENANT) == schema_before  # ontology not rebuilt for data-only change
    new_ctx = e.contexts.current(TENANT, "sync_lab")
    assert new_ctx["version"] != old_ctx["version"]
    carried = (e.contexts.root / new_ctx["files"]["collections/lab.audit_notes"]).read_text(encoding="utf-8")
    assert "carried_forward_from" in carried
    refreshed = (e.contexts.root / new_ctx["files"]["collections/lab.customers"]).read_text(encoding="utf-8")
    assert "APAC" in refreshed and "carried_forward_from" not in refreshed


def test_dependency_outside_changed_collections(rt):
    write(rt, "UPDATE lab.customers SET name = 'Renamed', updated_at = %s WHERE customer_id = 'C007'", (at(32, tz=True),))
    res = process(rt, event(window_start=at(30, tz=True).isoformat(), window_end=at(45, tz=True).isoformat(),
                            collections=[{"name": "lab.customers", "timestamp_column": "updated_at"}]))
    dep = next(d for d in coll(res, "lab.customers")["affected_dependents"] if d["collection"] == "lab.invoices")
    assert dep["affected_rows"] == 5 and "lab.invoices" in summary(res)["dependent_collections"]


def test_schema_update_rebuilds_knowledge_and_marks_stale(rt):
    e = rt["e"]
    from udre.contracts.models import NodeType, VerificationStatus

    before = e.kg.source_version("sync_lab", TENANT)
    write(rt, "ALTER TABLE lab.customers ADD COLUMN segment VARCHAR(20) DEFAULT 'SMB'")
    e.gateway.adapter(rt["ctx"], "sync_lab").refresh()
    write(rt, "UPDATE lab.customers SET segment = 'ENT', updated_at = %s WHERE customer_id = 'C001'", (at(50, tz=True),))
    res = process(rt, event(window_start=at(45, tz=True).isoformat(), window_end=at(60, tz=True).isoformat(),
                            collections=[{"name": "lab.customers", "timestamp_column": "updated_at"}]))
    assert summary(res)["schema_changed"] and summary(res)["knowledge"]["action"] == "ontology_rebuilt"
    assert e.kg.source_version("sync_lab", TENANT) != before
    assert e.kg.nodes(NodeType.COLUMN, "sync_lab", TENANT, status=VerificationStatus.STALE_SCHEMA) is not None
    assert any(n["label"].endswith(".segment") for n in e.kg.nodes(NodeType.COLUMN, "sync_lab", TENANT))


def test_out_of_order_and_overlapping_windows(rt):
    e = rt["e"]
    write(rt, "UPDATE lab.invoices SET updated_at = %s WHERE invoice_id = 'I0020'", (at(65),))
    newer = event(data_version="v900", sequence=900, window_start=at(60, tz=True).isoformat(), window_end=at(75, tz=True).isoformat())
    older = event(data_version="v899", sequence=899, window_start=at(58, tz=True).isoformat(), window_end=at(70, tz=True).isoformat())
    process(rt, newer)
    res = process(rt, older)
    assert res["event"] == "sync_event_processed" and "out-of-order" in " ".join(summary(res)["notes"])
    assert e.runtime_store.source_version(TENANT, "sync_lab")["data_version"] == "v900"  # never regressed
    wm = e.runtime_store.watermark(TENANT, "sync_lab", "lab.invoices")
    assert any(a <= at(58, tz=True).isoformat() and b >= at(75, tz=True).isoformat() for a, b in wm["intervals"])


def test_window_gap_is_reported_then_closed(rt):
    e = rt["e"]
    res = process(rt, event(sequence=1000, data_version="v1000", window_start=at(90, tz=True).isoformat(),
                            window_end=at(105, tz=True).isoformat()))
    gap = [at(75, tz=True).isoformat(), at(90, tz=True).isoformat()]
    assert gap in coll(res, "lab.invoices")["watermark"]["gaps"]  # [11:15, 11:30) not yet investigated
    res = process(rt, event(sequence=1001, data_version="v1001", window_start=gap[0], window_end=gap[1]))
    wm = coll(res, "lab.invoices")["watermark"]
    assert gap not in wm["gaps"]
    assert any(a <= at(58, tz=True).isoformat() and b >= at(105, tz=True).isoformat() for a, b in wm["intervals"])


def test_missing_or_wrong_timestamp_column_and_missing_collection(rt):
    res = process(rt, event(sequence=1100, data_version="v1100",
                            collections=[{"name": "lab.audit_notes", "timestamp_column": "updated_at"},
                                         {"name": "lab.payments", "timestamp_column": "amount"},
                                         {"name": "lab.not_there", "timestamp_column": "updated_at"}]))
    assert res["event"] == "sync_event_processed"
    assert coll(res, "lab.audit_notes")["status"] == "unverifiable_window"
    assert coll(res, "lab.payments")["status"] == "unverifiable_window" and "non-temporal" in coll(res, "lab.payments")["notes"][0]
    assert coll(res, "lab.not_there")["status"] == "missing_collection"
    assert rt["e"].runtime_store.watermark(TENANT, "sync_lab", "lab.audit_notes") is None


def test_deleted_records_cdc_visibility_and_tombstones(rt):
    e = rt["e"]
    ev = event(sequence=1200, data_version="v1200", collections=[{"name": "lab.payments", "changes": [
        {"op": "delete", "key": {"payment_id": "P0059"}}, {"op": "update", "key": {"payment_id": "P0001"}, "changed_columns": ["amount"],
                                                          "before": {"amount": 51}}]}])
    assert submit(rt, ev)["status"] == "accepted"
    first = [r for r in e.consumer.drain() if r["event_id"] == ev["event_id"]][-1]
    assert first["event"] == "sync_event_deferred" and "still present" in first["error"]  # delete not yet visible
    write(rt, "DELETE FROM lab.payments WHERE payment_id = 'P0059'")
    e.runtime_store._exec("UPDATE udre_runtime.sync_events SET next_attempt_at = now() WHERE event_id = %s", (ev["event_id"],))
    res = [r for r in e.consumer.drain() if r["event_id"] == ev["event_id"]][-1]
    c = coll(res, "lab.payments")
    assert res["event"] == "sync_event_processed" and c["method"] == "cdc_keys" and c["deleted_rows"] == 1
    assert c["operations"] == {"delete": 1, "update": 1} and c["changed_columns"] == ["amount"]
    write(rt, "UPDATE lab.customers SET is_deleted = TRUE, updated_at = %s WHERE customer_id = 'C019'", (at(122, tz=True),))
    res = process(rt, event(sequence=1201, data_version="v1201", window_start=at(120, tz=True).isoformat(),
                            window_end=at(135, tz=True).isoformat(), collections=[{"name": "lab.customers", "timestamp_column": "updated_at"}]))
    assert coll(res, "lab.customers")["deleted_rows"] == 1


def test_visibility_expected_changes_and_sync_failure(rt):
    e = rt["e"]
    ev = event(sequence=1300, data_version="v1300", window_start=at(140, tz=True).isoformat(), window_end=at(155, tz=True).isoformat(),
               collections=[{"name": "lab.invoices", "timestamp_column": "updated_at", "expected_changes": 2}])
    write(rt, "UPDATE lab.invoices SET updated_at = %s WHERE invoice_id = 'I0030'", (at(141),))
    assert submit(rt, ev)["status"] == "accepted"
    assert [r for r in e.consumer.drain() if r["event_id"] == ev["event_id"]][-1]["event"] == "sync_event_deferred"
    write(rt, "UPDATE lab.invoices SET updated_at = %s WHERE invoice_id = 'I0031'", (at(142),))
    e.runtime_store._exec("UPDATE udre_runtime.sync_events SET next_attempt_at = now() WHERE event_id = %s", (ev["event_id"],))
    assert [r for r in e.consumer.drain() if r["event_id"] == ev["event_id"]][-1]["event"] == "sync_event_processed"
    failed = process(rt, event(sequence=1301, data_version="v1301", event_type="database.sync.failed", status="failed",
                               collections=[{"name": "lab.invoices", "status": "failed", "error": "upstream timeout"}]))
    assert summary(failed)["kind"] == "sync_failed"
    v = e.runtime_store.source_version(TENANT, "sync_lab")
    assert v["sync_status"] == "sync_failed" and v["data_version"] == "v1300"


def test_restart_safe_lease_recovery(rt):
    e = rt["e"]
    ev = event(sequence=1400, data_version="v1400")
    submit(rt, ev)
    claimed = e.runtime_store.claim_next()
    assert claimed["event_id"] == ev["event_id"]  # simulated worker crash: never finished
    assert e.runtime_store.claim_next() is None  # same source is blocked while the lease is live
    e.runtime_store._exec("UPDATE udre_runtime.sync_events SET lease_until = now() - interval '1 second' WHERE event_id = %s",
                          (ev["event_id"],))
    assert [r for r in e.consumer.drain() if r["event_id"] == ev["event_id"]][-1]["event"] == "sync_event_processed"
    assert e.runtime_store.get_event(ev["event_id"])["attempts"] == 2


# ------------------------------------------------------------------ versions, snapshots, invalidation
def test_concurrent_sync_during_question_uses_consistent_snapshot(rt):
    e, ctx = rt["e"], rt["ctx"]
    q = "SELECT COUNT(*) AS n FROM lab.invoices WHERE status = 'VOIDED'"

    def on_event(ev):  # the ingestion service commits a change while the agents are running
        if ev.get("event") == "manifest":
            write(rt, "UPDATE lab.invoices SET status = 'VOIDED', updated_at = %s WHERE invoice_id = 'I0040'", (at(160),))
            process(rt, event(sequence=1500, data_version="v1500", window_start=at(155, tz=True).isoformat(),
                              window_end=at(170, tz=True).isoformat()))

    state = e.ask("How many invoices have status VOIDED?", ctx=AccessContext(tenant_id=TENANT, user_id="analyst"),
                  use_memory=False, on_event=on_event)
    assert state["manifest"]["sources"]["sync_lab"]["consistency"] == "pg_snapshot"
    assert state["consistency"]["status"] == "snapshot_consistent_but_outdated"
    assert state["explorer_candidate"]["structured_value"] == 0  # snapshot taken before the concurrent commit
    assert any("advanced to v1500" in a for a in state["decision"]["unresolved_assumptions"])
    assert e.gateway.execute_query(ctx, "sync_lab", q).rows[0]["n"] == 1  # outside the snapshot the change is visible


def test_stale_context_and_answer_invalidation(rt):
    e = rt["e"]
    ctx = AccessContext(tenant_id=TENANT, user_id="analyst")
    state = e.ask("What is the total amount of invoices with currency EUR?", ctx=ctx, use_memory=False)
    assert state["answer_id"]
    e.compiled_context(ctx, use_memory=False)
    assert any(k[0] == TENANT for k in e._ctx_cache)
    write(rt, "UPDATE lab.invoices SET amount = 999, updated_at = %s WHERE invoice_id = 'I0041'", (at(175),))
    res = process(rt, event(sequence=1600, data_version="v1600", window_start=at(170, tz=True).isoformat(), window_end=at(185, tz=True).isoformat()))
    assert summary(res)["answers_marked_stale"] >= 1
    assert not any(k[0] == TENANT for k in e._ctx_cache)
    ans = next(a for a in e.runtime_store.answers(TENANT) if a["answer_id"] == state["answer_id"])
    assert ans["stale"] and "v1600" in ans["stale_reason"]


# ------------------------------------------------------------------ Stage B policy
def test_stage_b_is_policy_gated(rt):
    from udre.runtime.policy import WatchedAnalysis

    e = rt["e"]
    write(rt, "UPDATE lab.invoices SET updated_at = %s WHERE invoice_id = 'I0050'", (at(190),))
    res = process(rt, event(sequence=1700, data_version="v1700", window_start=at(185, tz=True).isoformat(), window_end=at(200, tz=True).isoformat()))
    assert summary(res)["stage_b"]["ran"] is False and "ordinary" in summary(res)["stage_b"]["reason"]
    e.investigator.policy.watched = [WatchedAnalysis("open-ar", "How many invoices have status OPEN?", ["sync_lab"], ["lab.invoices"])]
    try:
        write(rt, "UPDATE lab.invoices SET updated_at = %s WHERE invoice_id = 'I0051'", (at(205),))
        res = process(rt, event(sequence=1701, data_version="v1701", window_start=at(200, tz=True).isoformat(), window_end=at(215, tz=True).isoformat()))
        assert summary(res)["stage_b"]["reason"] == "automatic analysis disabled by policy"
        e.investigator.policy.auto_analysis = True
        write(rt, "UPDATE lab.invoices SET updated_at = %s WHERE invoice_id = 'I0052'", (at(220),))
        res = process(rt, event(sequence=1702, data_version="v1702", window_start=at(215, tz=True).isoformat(), window_end=at(230, tz=True).isoformat()))
        run = summary(res)["stage_b"]["runs"][0]
        assert summary(res)["stage_b"]["ran"] and run["status"] == "verified_fact" and "Count" in run["answer"]
    finally:
        e.investigator.policy.watched, e.investigator.policy.auto_analysis = [], False


# ------------------------------------------------------------------ adjudication with a scripted GPT stand-in
def _cand(agent, value, sql, err=False):
    from udre.agents.planner import QueryPlan
    from udre.contracts.models import AgentReasoningCandidate, ValidationFinding

    plan = QueryPlan(source_id="sync_lab", primary_collection="lab.invoices", aggregation="COUNT")
    return AgentReasoningCandidate(agent_name=agent, interpreted_question="count", query_or_plan=sql, source_id="sync_lab",
                                   final_answer_candidate=f"Count: {value}", structured_value=value, observed_results=[{"value": value}],
                                   resolved_entities_and_metrics={"plan": plan.model_dump()},
                                   verification_results=[ValidationFinding(validator_name="x", passed=not err,
                                                                           severity="error" if err else "info", message="m")])


def test_llm_adjudicator_targeted_verification_and_guards(rt):
    from udre.contracts.models import AdjudicationDecision
    from udre.llm.scripted import ScriptedProvider, last_tool_result, tool
    from udre.verification.llm_adjudicator import LLMAdjudicator

    e, ctx = rt["e"], AccessContext(tenant_id=TENANT, user_id="analyst")
    a = _cand("Explorer", 40, "SELECT COUNT(*) AS value FROM lab.invoices WHERE status = 'OPEN'")
    b = _cand("Hercules", 100, "SELECT COUNT(*) AS value FROM lab.invoices")
    det = AdjudicationDecision(question="How many open invoices?", selected_path="Abstained_With_Uncertainty",
                               agreement_status="material_uncertainty", final_answer="disagree", winning_query="", winning_source_id="sync_lab")

    def policy(system, messages, tools):
        n = sum(1 for m in messages if m["role"] == "assistant")
        if n == 0:
            assert "Do not average conflicting financial numbers" in system
            return tool("submit_verdict", status="needs_verification", disagreement="filter on status",
                        verification_action={"source_id": "sync_lab", "sql": "SELECT status, COUNT(*) AS n FROM lab.invoices GROUP BY status",
                                             "purpose": "check status distribution", "discriminates": "OPEN count vs all"},
                        rationale="Hercules ignores the OPEN filter")
        assert "OPEN" in (messages[-1]["content"][0]["text"])
        return tool("submit_verdict", status="selected", selected_agent="Explorer", evidence_ids=["x"], rationale="Explorer applies OPEN")

    d = LLMAdjudicator(ScriptedProvider(policy), e.gateway).adjudicate(ctx, det.question, {"Explorer": a, "Hercules": b},
                                                                        {"Explorer": [], "Hercules": []}, det, {"status": "consistent"}, {})
    assert d.selected_path == "Explorer" and d.final_structured_data == [{"value": 40}] and d.verification_actions[0]["succeeded"]
    assert d.agreement_status == "resolved_after_verification"

    def picks_broken(system, messages, tools):
        return tool("submit_verdict", status="selected", selected_agent="Hercules", rationale="looks confident")

    bad = _cand("Hercules", 100, "SELECT COUNT(*) AS value FROM lab.invoices", err=True)
    d2 = LLMAdjudicator(ScriptedProvider(picks_broken), e.gateway).adjudicate(ctx, det.question, {"Explorer": a, "Hercules": bad},
                                                                               {"Explorer": [], "Hercules": []}, det, {}, {})
    assert d2.selected_path == "Abstained_With_Uncertainty" and "rejected by guards" in d2.llm_verdict["overridden"]


def test_dual_path_disagreement_reaches_adjudicator_in_workflow(rt):
    from udre.engine import build_engine
    from udre.gateway.postgres_adapter import PostgresSourceAdapter
    from udre.llm.scripted import ScriptedProvider, tool

    def exp(system, messages, tools):
        if sum(1 for m in messages if m["role"] == "assistant") == 0:
            return tool("execute_query", gap="count open", source_id="sync_lab", query="SELECT COUNT(*) AS value FROM lab.invoices WHERE status = 'OPEN'")
        return tool("submit_candidate", interpreted_question="open invoices", source_id="sync_lab", metric_interpretation="count",
                    sql="SELECT COUNT(*) AS value FROM lab.invoices WHERE status = 'OPEN'", answer_summary="open count")

    def her(system, messages, tools):
        return tool("submit_candidate", interpreted_question="all invoices", source_id="sync_lab", metric_interpretation="count",
                    sql="SELECT COUNT(*) AS value FROM lab.invoices", answer_summary="all")

    seen = {}

    def adj(system, messages, tools):
        seen["payload"] = messages[0]["content"][0]["text"]
        return tool("submit_verdict", status="selected", selected_agent="Explorer", rationale="OPEN filter matches the question")

    e2 = build_engine(llm_providers=(ScriptedProvider(exp), ScriptedProvider(her), ScriptedProvider(adj)))
    e2.gateway.register(PostgresSourceAdapter("sync_lab", rt["lab"], include_schemas=["lab"]), tenants=[TENANT])
    state = e2.ask("How many invoices have status OPEN?", ctx=AccessContext(tenant_id=TENANT, user_id="analyst"), use_memory=False)
    d = state["decision"]
    assert d["llm_verdict"]["status"] == "selected" and d["selected_path"] == "Explorer"
    assert "result_rows" in seen["payload"] and "blocking_errors" in seen["payload"]
    assert any(ev.get("event") == "node" and ev.get("node") == "llm_adjudicate" for ev in state["events"])


# ------------------------------------------------------------------ sandbox
class FakeDaytona:
    def __init__(self, fail_create=False, corrupt=False):
        self.fail_create, self.corrupt, self.deleted, self.store = fail_create, corrupt, False, {}

    def create(self, timeout=60):
        if self.fail_create:
            raise RuntimeError("quota exceeded")
        outer = self

        class R:
            def __init__(self, result, code=0):
                self.result, self.exit_code = result, code

        class FS:
            def upload_file(self, data, dst):
                outer.store[dst] = (data if isinstance(data, bytes) else data.encode()) + (b"X" if outer.corrupt and "data/" in dst else b"")

            def download_file(self, path):
                return outer.store.get(path)

        class Proc:
            def exec(self, cmd, cwd=None, timeout=None):
                import hashlib
                if cmd.startswith("sha256sum"):
                    p = cmd.split("'")[1]
                    return R(hashlib.sha256(outer.store[p]).hexdigest() + "  " + p)
                if cmd.startswith("find"):
                    return R("\n".join(k for k in outer.store if "/out/" in k))
                if cmd.startswith("python3"):
                    outer.store["/w/out/report.csv"] = b"a,b\n1,2\n"
                    return R("computed 42")
                return R("")

        class SB:
            fs, process = FS(), Proc()

            def get_work_dir(self):
                return "/w"

        return SB()

    def delete(self, sb):
        self.deleted = True


def test_sandbox_daytona_transfer_integrity_artifacts_and_failures(tmp_path):
    from udre.tools.files import FileStore
    from udre.tools.sandbox import SandboxManager

    fs = FileStore(tmp_path)
    rec = fs.save_bytes("t", "u", "ledger.csv", b"x,y\n1,2\n")
    ok = SandboxManager(fs, provider="daytona", daytona_client=FakeDaytona())
    out = ok.run("run1", "Explorer", "print(42)", [rec], "t", "u")
    assert out["provider"] == "daytona" and out["stdout"] == "computed 42" and out["artifacts"][0]["filename"] == "report.csv"
    assert fs.read_bytes(fs.get("t", "u", out["artifacts"][0]["file_id"], kind="artifact")) == b"a,b\n1,2\n"
    ok.close_run("run1")
    corrupt = SandboxManager(fs, provider="daytona", daytona_client=FakeDaytona(corrupt=True))
    with pytest.raises(Exception, match="integrity check failed"):
        corrupt.run("run2", "Hercules", "print(1)", [rec], "t", "u")
    from udre.skills.tools import ToolBox

    tb = ToolBox(None, None, None, str(tmp_path), sandbox=SandboxManager(fs, provider="daytona", daytona_client=FakeDaytona(fail_create=True)),
                 files=fs)
    res = tb.run_python(AccessContext(tenant_id="t", user_id="u"), "print(1)", [rec.file_id], run_id="run3", agent="Explorer")
    assert not res["succeeded"] and "quota exceeded" in res["error_message"]  # Daytona failure is an observation, not a crash


def test_local_sandbox_real_computation_and_separate_agents(tmp_path):
    from udre.tools.files import FileStore
    from udre.tools.sandbox import SandboxManager

    fs = FileStore(tmp_path)
    rec = fs.save_bytes("t", "u", "sales.csv", b"amount\n10\n20\n30\n")
    sm = SandboxManager(fs, provider="local")
    code = ("import csv, glob\np = glob.glob('data/*sales.csv')[0]\nt = sum(float(r['amount']) for r in csv.DictReader(open(p)))\n"
            "print(t)\nopen('out/total.txt', 'w').write(str(t))\n")
    a = sm.run("r", "Explorer", code, [rec], "t", "u")
    b = sm.run("r", "Hercules", "import os\nprint(sorted(os.listdir('out')))", [], "t", "u")
    assert a["exit_code"] == 0 and a["stdout"].strip() == "60.0" and a["artifacts"][0]["filename"] == "total.txt"
    assert b["stdout"].strip() == "[]" and a["workdir"] != b["workdir"]  # separate writable workspaces per agent
    sm.close_run("r")


# ------------------------------------------------------------------ API: streaming, uploads, events
def test_api_streams_real_events_and_analyses_uploaded_file(rt):
    from fastapi.testclient import TestClient

    from udre.api import app as api

    e = rt["e"]
    original = api.engine
    api.engine = lambda: e
    try:
        c = TestClient(api.app)
        h = {"X-Tenant-Id": TENANT, "X-User-Id": "analyst"}
        csv = b"order_id,region,amount\nO1,AMER,100\nO2,EMEA,250\nO3,AMER,50\nO4,APAC,75\n"
        up = c.post("/files", files=[("files", ("orders.csv", io.BytesIO(csv), "text/csv"))], headers=h).json()
        assert up[0]["size"] == len(csv) and len(up[0]["sha256"]) == 64
        assert c.post("/files", files=[("files", ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream"))], headers=h).status_code == 400
        ws = c.post("/workspaces", json={"file_ids": [up[0]["file_id"]]}, headers=h).json()
        assert ws["collections"] == ["orders"]
        with c.stream("POST", "/ask/stream", json={"question": "What is the total amount of orders for region AMER?",
                                                     "workspace_id": ws["workspace_id"], "use_memory": False, "export_formats": ["csv"]},
                      headers=h) as r:
            events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
        result = events[-1]["result"]
        assert events[-1]["event"] == "result" and result["candidates_summary"]["Explorer"]["value"] == 150.0
        nodes = [ev for ev in events if ev.get("event") == "node"]
        assert {"resolve_sources", "explorer", "hercules", "verify", "adjudicate", "finalize"} <= {n["node"] for n in nodes}
        tools = [ev for ev in events if ev.get("event") == "tool"]
        assert len(tools) == sum(cs["tool_calls"] for cs in result["candidates_summary"].values())  # 1:1 with real tool calls
        assert any(t.get("query") and t.get("rows") is not None for t in tools)
        art = result["artifacts"][0]
        assert c.get(art["download_url"], headers=h).status_code == 200
        assert c.get(art["download_url"], headers={"X-Tenant-Id": "other", "X-User-Id": "x"}).status_code == 404
        assert e.kg.nodes(source_id=ws["source_id"], tenant=TENANT) == []  # uploads never touch the tenant ontology
        body = json.dumps(event(sequence=1800, data_version="v1800")).encode()
        assert c.post("/events/sync", content=body, headers={"X-UDRE-Signature": sign(body, SECRET)}).json()["status"] == "accepted"
        assert c.post("/events/sync", content=body, headers={"X-UDRE-Signature": "bad"}).status_code == 401
        src = next(s for s in c.get("/runtime/sources", headers=h).json() if s["source_id"] == "sync_lab")
        assert src["sync_status"] == "ok" and src["data_version"]
        assert c.get("/runtime/investigations", headers=h).json()
        c.delete(f"/workspaces/{ws['workspace_id']}", headers=h)
    finally:
        api.engine = original
