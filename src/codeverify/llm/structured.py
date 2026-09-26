"""Structured output with Pydantic validation and an automatic repair loop.

Rather than relying on provider-specific JSON modes (unevenly supported across
open models), every agent asks for JSON matching a Pydantic schema, and we:

1. extract the JSON object (fenced or bare) from the reply;
2. optionally splice in a fenced ```python block for a large code field — code
   inside JSON strings is a top source of escaping errors for smaller models;
3. validate with Pydantic (``extra="forbid"``);
4. on failure, send the *validation error* back to the model and retry, up to
   ``max_repairs`` times.
"""

from __future__ import annotations

import json
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from codeverify.llm.base import LLMClient
from codeverify.observability import log_event
from codeverify.schemas import LLMCallRecord

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)


class StructuredOutputError(RuntimeError):
    def __init__(self, message: str, record: LLMCallRecord):
        super().__init__(message)
        self.record = record


def extract_json(text: str) -> dict:
    """Return the first JSON object in ``text`` (```json fence preferred)."""
    for lang, body in _FENCE.findall(text):
        if lang.lower() in ("json", "") and body.strip().startswith("{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start() :])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object found in response")


def extract_code_block(text: str) -> str | None:
    """Return the last ```python fenced block (the model's final answer)."""
    blocks = [body for lang, body in _FENCE.findall(text) if lang.lower() in ("python", "py")]
    return blocks[-1] if blocks else None


def schema_prompt(schema: type[BaseModel], code_field: str | None = None) -> str:
    js = schema.model_json_schema()
    if code_field:
        js.get("properties", {}).pop(code_field, None)
        if code_field in js.get("required", []):
            js["required"].remove(code_field)
    instructions = (
        "Respond with ONE JSON object in a ```json fenced block that validates against this "
        f"JSON Schema (no extra keys):\n{json.dumps(js, separators=(',', ':'))}"
    )
    if code_field:
        instructions += (
            f"\nThen, AFTER the JSON block, put the value of `{code_field}` — the COMPLETE corrected "
            "Python module — in a single ```python fenced block. Do not put code inside the JSON."
        )
    return instructions


def structured_call(
    client: LLMClient,
    schema: type[T],
    *,
    system: str,
    user: str,
    node: str,
    max_repairs: int = 2,
    code_field: str | None = None,
) -> tuple[T, LLMCallRecord]:
    """Call the LLM until it returns an object that validates against ``schema``."""
    system_full = f"{system}\n\n{schema_prompt(schema, code_field)}"
    prompt = user
    in_tok = out_tok = 0
    latency = 0.0
    model = "unknown"
    last_err = ""
    started = time.perf_counter()
    for attempt in range(max_repairs + 1):
        resp = client.complete(system_full, prompt)
        in_tok += resp.input_tokens
        out_tok += resp.output_tokens
        latency += resp.latency_ms
        model = resp.model
        try:
            data = extract_json(resp.text)
            if code_field:
                code = extract_code_block(resp.text)
                if code is not None:
                    data[code_field] = code
            obj = schema.model_validate(data)
            record = LLMCallRecord(
                node=node, model=model, input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=round(latency, 2), repairs=attempt, success=True,
            )  # fmt: skip
            log_event("llm_call", **record.model_dump(), wall_ms=round((time.perf_counter() - started) * 1000, 2))
            return obj, record
        except (ValueError, ValidationError) as exc:
            last_err = str(exc)[:1500]
            log_event("structured_output_repair", node=node, attempt=attempt, error=last_err[:300])
            prompt = (
                f"{user}\n\n---\nYour previous reply was INVALID and was rejected:\n{last_err}\n"
                "Reply again, strictly following the required output format."
            )
    record = LLMCallRecord(
        node=node, model=model, input_tokens=in_tok, output_tokens=out_tok,
        latency_ms=round(latency, 2), repairs=max_repairs, success=False,
    )  # fmt: skip
    log_event("llm_call", **record.model_dump())
    raise StructuredOutputError(f"{schema.__name__} validation failed: {last_err}", record)
