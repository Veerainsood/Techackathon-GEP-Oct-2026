"""LIVE smoke tests against the configured Microsoft Foundry deployments (and Daytona if DAYTONA_API_KEY is set).

Skipped unless BACKEND_LIVE_SMOKE=1.  They spend real model tokens.  Run:
    $env:BACKEND_LIVE_SMOKE=1; python -m pytest tests/test_live_smoke.py -q -s
"""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("BACKEND_LIVE_SMOKE") != "1", reason="live smoke tests are opt-in (BACKEND_LIVE_SMOKE=1)")


@pytest.fixture(scope="module")
def live_engine(pg_uri, uploads_dir):
    from backend.config import Settings
    from backend.engine import build_engine

    os.environ["BACKEND_REASONING_MODE"] = "llm"
    s = Settings()
    s.reasoning_mode = "llm"
    e = build_engine(s)
    yield e
    os.environ["BACKEND_REASONING_MODE"] = "offline"


def test_live_models_are_the_configured_foundry_deployments(live_engine):
    cfg = live_engine.model_config()
    print("\nMODELS", json.dumps(cfg))
    assert cfg["explorer"]["model"] and cfg["hercules"]["model"] and cfg["adjudicator"]["model"]


def test_live_dual_path_with_gpt_adjudication(live_engine):
    from backend.gateway.registry import AccessContext

    q = "What is the total gross invoice amount in USD for invoices billed in EUR currency?"
    events = []
    state = live_engine.ask(q, ctx=AccessContext(allowed_sources={"finance_pg"}), use_memory=False, on_event=events.append)
    d = state["decision"]
    ex, he = state.get("explorer_candidate") or {}, state.get("hercules_candidate") or {}
    print("\nEXPLORER", ex.get("reasoning_mode"), ex.get("structured_value"), ex.get("llm_usage"), ex.get("query_or_plan"))
    print("HERCULES", he.get("reasoning_mode"), he.get("structured_value"), he.get("llm_usage"), he.get("query_or_plan"))
    print("ADJUDICATOR", json.dumps(d.get("llm_verdict"), default=str)[:800])
    print("DECISION", d["selected_path"], d["agreement_status"], d["verification_status"], d["final_answer"][:200])
    print("TOOLS", sum(1 for e in events if e.get("event") == "tool"), "events streamed:", len(events), "ms:", state["total_ms"])
    assert ex.get("reasoning_mode") == "llm" and he.get("reasoning_mode") == "llm"
    truth = 4958011.24
    assert any(abs(float(c.get("structured_value") or 0) - truth) < 0.01 for c in (ex, he))
    assert d["llm_verdict"].get("model")
