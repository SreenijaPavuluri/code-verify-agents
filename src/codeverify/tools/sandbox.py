"""Isolated execution backends for untrusted (LLM-generated) Python code.

Two interchangeable backends implement :class:`Sandbox`:

* :class:`SubprocessSandbox` — a fresh ``python -I -B -S`` process per run in a
  throw-away temp dir with a scrubbed environment, POSIX rlimits (CPU, memory,
  file size, fds), a hard wall-clock timeout, and the in-process audit-hook
  policy from :mod:`codeverify.tools._runner`.
* :class:`DockerSandbox` — the same runner inside a container with
  ``--network none``, a read-only root FS, and cgroup memory/CPU/pid caps.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Protocol

from codeverify.config import Settings
from codeverify.schemas import TestCaseResult, TestReport

_RUNNER = Path(__file__).with_name("_runner.py")


class Sandbox(Protocol):
    def run_tests(self, source_code: str, test_code: str, module_name: str = "solution") -> TestReport:
        """Execute ``test_code`` against ``source_code`` and return a structured report."""
        ...


def _prepare_workdir(source_code: str, test_code: str, module_name: str) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix="cv_sandbox_"))
    (workdir / f"{module_name}.py").write_text(source_code, encoding="utf-8")
    (workdir / "test_solution.py").write_text(test_code, encoding="utf-8")
    shutil.copy(_RUNNER, workdir / "_runner.py")
    return workdir


def _parse_output(nonce: str, stdout: str, stderr: str, duration_ms: float) -> TestReport:
    """Only trust the stdout line carrying our nonce; everything else is noise."""
    payload = None
    for line in stdout.splitlines():
        if line.startswith(nonce):
            payload = json.loads(line[len(nonce) :])
    if payload is None:
        tail = (stderr or stdout)[-1500:]
        return TestReport(
            collection_error=f"sandbox produced no result (crash / resource limit?)\n{tail}",
            duration_ms=duration_ms,
        )
    cases = [TestCaseResult(**c) for c in payload["cases"]]
    passed = sum(c.passed for c in cases)
    errors = sum(
        1 for c in cases if not c.passed and c.error_type not in ("AssertionError", None)
    )
    violations = payload.get("violations") or []
    return TestReport(
        passed=passed,
        failed=len(cases) - passed - errors,
        errors=errors,
        total=len(cases),
        cases=cases,
        collection_error=payload.get("collection_error"),
        sandbox_violation="; ".join(dict.fromkeys(violations)) or None,
        timed_out=any(c.error_type == "_Timeout" for c in cases),
        duration_ms=duration_ms,
    )


class SubprocessSandbox:
    """Process-level sandbox. Works everywhere Python does; no Docker required."""

    def __init__(self, timeout_s: float = 10.0, memory_mb: int = 512, per_test_timeout_s: float = 3.0):
        self.timeout_s = timeout_s
        self.memory_mb = memory_mb
        self.per_test_timeout_s = per_test_timeout_s

    def _limits(self) -> None:  # runs in the child between fork and exec
        import resource

        def _set(res: int, soft: int) -> None:
            try:
                _, hard = resource.getrlimit(res)
                cap = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
                resource.setrlimit(res, (cap, hard))
            except (ValueError, OSError):
                pass  # e.g. RLIMIT_AS is not enforceable on macOS

        _set(resource.RLIMIT_CPU, int(self.timeout_s) + 1)
        _set(resource.RLIMIT_FSIZE, 5 * 1024 * 1024)
        _set(resource.RLIMIT_NOFILE, 256)
        if sys.platform.startswith("linux"):
            _set(resource.RLIMIT_AS, self.memory_mb * 1024 * 1024)
        os.setsid()  # own process group so the whole tree dies on timeout

    def run_tests(self, source_code: str, test_code: str, module_name: str = "solution") -> TestReport:
        workdir = _prepare_workdir(source_code, test_code, module_name)
        nonce = f"@@{secrets.token_hex(16)}@@"
        env = {
            "CV_PER_TEST_TIMEOUT": str(self.per_test_timeout_s),
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "LANG": "C.UTF-8",
        }
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-B", "-S", "_runner.py"],
                input=nonce + "\n",
                capture_output=True,
                text=True,
                cwd=workdir,
                env=env,
                timeout=self.timeout_s,
                preexec_fn=self._limits if os.name == "posix" else None,  # noqa: PLW1509
            )
            duration = (time.perf_counter() - started) * 1000
            return _parse_output(nonce, proc.stdout, proc.stderr, round(duration, 2))
        except subprocess.TimeoutExpired:
            return TestReport(
                timed_out=True,
                collection_error=f"hard timeout after {self.timeout_s}s",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


class DockerSandbox:
    """Container-level sandbox: no network, read-only rootfs, cgroup limits."""

    def __init__(self, image: str = "python:3.11-slim", timeout_s: float = 20.0, memory_mb: int = 512):
        if shutil.which("docker") is None:
            raise RuntimeError("docker CLI not found; use sandbox_backend='subprocess'")
        self.image, self.timeout_s, self.memory_mb = image, timeout_s, memory_mb

    def run_tests(self, source_code: str, test_code: str, module_name: str = "solution") -> TestReport:
        workdir = _prepare_workdir(source_code, test_code, module_name)
        nonce = f"@@{secrets.token_hex(16)}@@"
        cmd = [
            "docker", "run", "--rm", "-i",
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=16m",
            "--memory", f"{self.memory_mb}m", "--cpus", "1", "--pids-limit", "64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-v", f"{workdir}:/sandbox:rw", "-w", "/sandbox",
            self.image, "python", "-I", "-B", "-S", "_runner.py",
        ]  # fmt: skip
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd, input=nonce + "\n", capture_output=True, text=True, timeout=self.timeout_s
            )
            duration = round((time.perf_counter() - started) * 1000, 2)
            return _parse_output(nonce, proc.stdout, proc.stderr, duration)
        except subprocess.TimeoutExpired:
            return TestReport(timed_out=True, collection_error="docker sandbox timeout")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def get_sandbox(settings: Settings) -> Sandbox:
    if settings.sandbox_backend == "docker":
        return DockerSandbox(settings.docker_image, settings.sandbox_timeout_s * 2, settings.sandbox_memory_mb)
    return SubprocessSandbox(settings.sandbox_timeout_s, settings.sandbox_memory_mb)
