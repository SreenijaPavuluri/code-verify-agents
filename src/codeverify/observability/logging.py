"""Structured JSON logging.

Every log line is a single JSON object, so it can be shipped straight to
Loki / Datadog / CloudWatch and queried by field (``event``, ``node``,
``latency_ms``, ``input_tokens`` …). Use :func:`log_event` for telemetry.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

#: The run id of the currently executing pipeline, attached to every record.
current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None) or current_run_id.get()
        if run_id:
            payload["run_id"] = run_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "run_id" and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def configure_logging(level: str | None = None, log_file: str | os.PathLike[str] | None = None) -> None:
    """Install the JSON formatter on the ``codeverify`` logger (idempotent).

    ``level`` applies to the stderr handler; when ``log_file`` is given, every
    INFO+ event is additionally written there as JSON Lines.
    """
    global _configured
    if _configured:
        return
    root = logging.getLogger("codeverify")
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(JsonFormatter())
    stderr.setLevel(level or os.environ.get("CODEVERIFY_LOG_LEVEL", "INFO"))
    root.handlers[:] = [stderr]
    if log_file:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler.setLevel(logging.INFO)
        root.addHandler(file_handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"codeverify.{name}")


_events = get_logger("events")


def log_event(event: str, **fields: Any) -> None:
    """Emit a structured telemetry event, e.g. ``log_event("tool_call", tool="lint", ...)``."""
    _events.info(event, extra={"event": event, **fields})
