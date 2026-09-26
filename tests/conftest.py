from __future__ import annotations

import json

import pytest

from codeverify.config import Settings
from codeverify.schemas import BugTask

BUGGY = "def avg(xs):\n    return sum(xs) / (len(xs) - 1)\n"
FIXED = "def avg(xs):\n    return sum(xs) / len(xs)\n"
WRONG = "def avg(xs):\n    return sum(xs) / len(xs) + 1\n"
TESTS = (
    "from solution import avg\n\n"
    "def test_pair():\n    assert avg([2, 4]) == 3\n\n"
    "def test_single():\n    assert avg([5]) == 5\n"
)


def patch_reply(code: str, root_cause: str = "denominator is off by one") -> str:
    """Format a scripted LLM reply the way the Patch Generator expects it."""
    meta = {"root_cause": root_cause, "change_summary": "adjust the computation", "confidence": 0.8}
    return f"```json\n{json.dumps(meta)}\n```\n```python\n{code}```"


@pytest.fixture
def task() -> BugTask:
    return BugTask(task_id="avg", source_code=BUGGY, test_code=TESTS, description="avg returns the mean")


@pytest.fixture
def settings() -> Settings:
    return Settings(provider="scripted", model="scripted", max_patch_attempts=3, max_graph_steps=24)
