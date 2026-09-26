import json
import time

import pytest
from fastapi.testclient import TestClient

from codeverify.api import create_app
from codeverify.llm import ScriptedLLM
from tests.conftest import FIXED, patch_reply


@pytest.fixture
def client(settings):
    app = create_app(settings, llm=ScriptedLLM(lambda s, u: patch_reply(FIXED)))
    with TestClient(app) as c:
        yield c


def _wait(client, run_id, statuses, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/v1/runs/{run_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run did not reach {statuses}: {body}")


def _sse(client, run_id):
    events = []
    with client.stream("GET", f"/v1/runs/{run_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_run_lifecycle_and_stream(client, task):
    resp = client.post("/v1/runs", json={"task": task.model_dump()})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    body = _wait(client, run_id, {"resolved"})
    assert body["final_code"] == FIXED and body["metrics"]["tool_calls"] == 6

    events = _sse(client, run_id)
    nodes = [e["node"] for e in events if e.get("type") == "node_update"]
    assert nodes == ["planner", "inspector", "patch_generator", "verifier", "human_review"]
    verifier = next(e for e in events if e.get("node") == "verifier")
    assert verifier["data"]["passed"] is True and verifier["data"]["latency_ms"] >= 0
    assert events[-1]["status"] == "resolved"

    metrics = client.get("/v1/metrics").json()
    assert metrics["runs_by_status"]["resolved"] >= 1 and metrics["tool_call_success_rate"] == 1.0


def test_human_review_flow(client, task):
    run_id = client.post("/v1/runs", json={"task": task.model_dump(), "require_human_review": True}).json()["run_id"]
    body = _wait(client, run_id, {"awaiting_review"})
    assert body["pending_review"]["patch"] == FIXED
    assert client.post(f"/v1/runs/{run_id}/review", json={"decision": "approve", "reviewer": "bob"}).status_code == 200
    assert _wait(client, run_id, {"resolved"})["final_code"] == FIXED
    # Reviewing a finished run is a conflict.
    assert client.post(f"/v1/runs/{run_id}/review", json={"decision": "approve"}).status_code == 409


def test_validation_and_404(client):
    assert client.get("/v1/runs/nope").status_code == 404
    bad = {"task": {"task_id": "x", "source_code": "x", "test_code": "y", "surprise": 1}}
    assert client.post("/v1/runs", json=bad).status_code == 422
