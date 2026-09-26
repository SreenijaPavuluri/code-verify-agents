"""FastAPI service exposing the multi-agent pipeline.

Endpoints
---------
``GET  /health``                    liveness probe
``POST /v1/runs``                   start a run (async, returns 202 + run id)
``GET  /v1/runs/{id}``              run status, final patch, metrics, pending review
``GET  /v1/runs/{id}/events``       Server-Sent Events stream of node transitions
``POST /v1/runs/{id}/review``       submit a human decision (approve / reject / revise)
``GET  /v1/metrics``                aggregate observability counters
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import AnyHttpUrl, BaseModel, Field

from codeverify import __version__
from codeverify.api.run_manager import TERMINAL, RunManager
from codeverify.config import Settings
from codeverify.llm import LLMClient
from codeverify.observability import compute_run_metrics, configure_logging
from codeverify.schemas import BugTask, ReviewDecision


class CreateRunRequest(BaseModel):
    task: BugTask
    require_human_review: bool = False
    webhook_url: AnyHttpUrl | None = Field(default=None, description="POSTed on completion / review request")


class RunView(BaseModel):
    run_id: str
    task_id: str
    status: str
    created_at: str
    termination_reason: str | None = None
    patch_attempts: int = 0
    final_code: str | None = None
    pending_review: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    wall_ms: float | None = None
    event_count: int = 0


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    configure_logging()
    settings = settings or Settings.from_env()
    manager = RunManager(settings, llm=llm)
    app = FastAPI(title="codeverify", version=__version__,
                  description="Autonomous code verification & bug-patching multi-agent system")  # fmt: skip
    app.state.manager = manager

    def _get(run_id: str):
        run = manager.runs.get(run_id)
        if run is None:
            raise HTTPException(404, f"run '{run_id}' not found")
        return run

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "model": settings.model}

    @app.post("/v1/runs", status_code=202, response_model=RunView)
    def create_run(req: CreateRunRequest) -> RunView:
        run = manager.start(req.task, req.require_human_review, str(req.webhook_url) if req.webhook_url else None)
        return view(run.run_id)

    @app.get("/v1/runs/{run_id}", response_model=RunView)
    def view(run_id: str) -> RunView:
        run = _get(run_id)
        fs = run.final_state or {}
        return RunView(
            run_id=run.run_id,
            task_id=run.task.task_id,
            status=run.status,
            created_at=run.created_at,
            termination_reason=fs.get("termination_reason"),
            patch_attempts=fs.get("patch_attempts", 0),
            final_code=fs.get("final_code") if run.status == "resolved" else None,
            pending_review=run.pending_review,
            metrics=compute_run_metrics(fs).model_dump() if fs else None,
            wall_ms=run.wall_ms,
            event_count=len(run.events),
        )

    @app.get("/v1/runs/{run_id}/events")
    async def events(run_id: str) -> StreamingResponse:
        run = _get(run_id)

        async def stream() -> AsyncIterator[str]:
            sent = 0
            while True:
                pending = run.events[sent:]
                for ev in pending:
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                sent += len(pending)
                if (run.status in TERMINAL or run.status == "awaiting_review") and sent >= len(run.events):
                    yield f"event: end\ndata: {json.dumps({'status': run.status})}\n\n"
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})  # fmt: skip

    @app.post("/v1/runs/{run_id}/review", response_model=RunView)
    def review(run_id: str, decision: ReviewDecision) -> RunView:
        _get(run_id)
        try:
            manager.submit_review(run_id, decision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return view(run_id)

    @app.get("/v1/metrics")
    def metrics() -> dict[str, Any]:
        return manager.aggregate_metrics()

    return app


def serve() -> None:
    import os

    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    serve()
