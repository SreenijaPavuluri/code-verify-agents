"""Submit a buggy snippet to a running codeverify API and print the live SSE stream.

    python -m codeverify.api.app            # terminal 1
    python examples/stream_client.py        # terminal 2  (add --review to exercise HITL)
"""

from __future__ import annotations

import argparse
import json

import httpx

TASK = {
    "task_id": "demo_chunk",
    "description": "chunk(xs, size) splits xs into consecutive lists of length size (last may be shorter).",
    "source_code": "def chunk(xs, size):\n    return [xs[i:size] for i in range(0, len(xs), size)]\n",
    "test_code": (
        "from solution import chunk\n\n"
        "def test_basic():\n    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]\n"
    ),
}


def stream(client: httpx.Client, run_id: str) -> str:
    status = "unknown"
    with client.stream("GET", f"/v1/runs/{run_id}/events", timeout=None) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev.get("type") == "node_update":
                print(f"  [{ev['data'].get('step')}] {ev['node']:<16} {json.dumps(ev['data'])[:160]}")
            elif "type" in ev:
                print(f"  -- {ev['type']}")
            status = ev.get("status", status)
    return status


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--review", action="store_true", help="require human review before finishing")
    args = p.parse_args()

    with httpx.Client(base_url=args.url, timeout=30) as client:
        run = client.post("/v1/runs", json={"task": TASK, "require_human_review": args.review}).json()
        print(f"run {run['run_id']} started")
        status = stream(client, run["run_id"])
        if status == "awaiting_review":
            pending = client.get(f"/v1/runs/{run['run_id']}").json()["pending_review"]
            print("\nPatch awaiting review:\n" + (pending.get("patch") or ""))
            decision = input("approve / reject / revise? ").strip() or "approve"
            feedback = input("feedback (optional): ") if decision == "revise" else ""
            client.post(f"/v1/runs/{run['run_id']}/review", json={"decision": decision, "feedback": feedback})
            stream(client, run["run_id"])
        final = client.get(f"/v1/runs/{run['run_id']}").json()
        print(f"\nstatus={final['status']}  attempts={final['patch_attempts']}")
        if final.get("final_code"):
            print(final["final_code"])


if __name__ == "__main__":
    main()
