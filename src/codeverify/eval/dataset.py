"""Benchmark dataset loader.

Layout (one directory per task)::

    benchmarks/dataset/<task_id>/
        buggy.py           # code handed to the agent
        test_visible.py    # tests the agent may see and run
        test_hidden.py     # held-out tests used ONLY for grading (Pass@1)
        reference_fix.py   # ground-truth fix used to validate the dataset
        meta.json          # category, difficulty, natural-language spec

Grading on *hidden* tests means Pass@1 measures genuine repair, not overfitting
to (or special-casing) the visible tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from codeverify.schemas import BugTask

DEFAULT_DATASET = Path(__file__).resolve().parents[3] / "benchmarks" / "dataset"


class BenchmarkItem(BaseModel):
    task: BugTask
    hidden_tests: str
    reference_fix: str
    category: str
    difficulty: str


def load_dataset(root: Path | str = DEFAULT_DATASET, ids: list[str] | None = None) -> list[BenchmarkItem]:
    root = Path(root)
    items: list[BenchmarkItem] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        meta = json.loads((d / "meta.json").read_text())
        if ids and meta["task_id"] not in ids:
            continue
        items.append(
            BenchmarkItem(
                task=BugTask(
                    task_id=meta["task_id"],
                    source_code=(d / "buggy.py").read_text(),
                    test_code=(d / "test_visible.py").read_text(),
                    description=meta["description"],
                ),
                hidden_tests=(d / "test_hidden.py").read_text(),
                reference_fix=(d / "reference_fix.py").read_text(),
                category=meta["category"],
                difficulty=meta["difficulty"],
            )
        )
    return items
