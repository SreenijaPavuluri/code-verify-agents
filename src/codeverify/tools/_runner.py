"""In-sandbox test runner (executed as a *separate* Python process).

This file is copied into a throw-away sandbox directory and launched with
``python -I -B -S _runner.py``. It is intentionally stdlib-only.

Defence-in-depth layers applied here (the parent adds rlimits + timeouts):

1. **Result authentication** — a random nonce is read from stdin *before* any
   untrusted code is imported; only the line prefixed with that nonce is
   trusted by the parent, so code under test cannot forge a passing report
   just by printing.
2. **PEP 578 audit hook** — blocks network sockets, process spawning,
   ``ctypes``, and any file write/delete outside the sandbox directory.
   Violations are recorded even if the untrusted code swallows the exception.
3. **Per-test wall-clock alarm** — an infinite loop fails one test instead of
   the whole run.
"""

import contextlib
import importlib
import inspect
import io
import json
import os
import signal
import sys
import time
import traceback
import unittest

NONCE = sys.stdin.readline().strip()
sys.stdin.close()
SANDBOX_DIR = os.path.realpath(os.getcwd())
PER_TEST_TIMEOUT = float(os.environ.get("CV_PER_TEST_TIMEOUT", "5"))
REAL_STDOUT = sys.stdout
sys.dont_write_bytecode = True
sys.path.insert(0, SANDBOX_DIR)

_violations: list[str] = []

_BLOCKED_EVENTS = (
    "socket.connect",
    "socket.bind",
    "socket.sendto",
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "os.kill",
    "ctypes.dlopen",
    "ctypes.dlsym",
    "webbrowser.open",
)
_PATH_EVENTS = {"os.remove", "os.rmdir", "os.rename", "os.mkdir", "shutil.rmtree", "os.chmod"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


class SandboxViolation(PermissionError):
    pass


def _inside(path: object) -> bool:
    if isinstance(path, int):
        return True  # already-open file descriptor
    if isinstance(path, bytes):
        path = path.decode(errors="replace")
    real = os.path.realpath(str(path))
    return real == SANDBOX_DIR or real.startswith(SANDBOX_DIR + os.sep)


def _deny(reason: str) -> None:
    _violations.append(reason)
    raise SandboxViolation(f"sandbox policy: {reason}")


def _audit(event: str, args: tuple) -> None:
    if event.startswith(_BLOCKED_EVENTS):
        _deny(f"blocked operation '{event}'")
    elif event == "open":
        path, mode, flags = args
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool(
            (flags or 0) & _WRITE_FLAGS
        )
        if writing and path is not None and not _inside(path):
            _deny(f"write outside sandbox: {path!r}")
    elif event in _PATH_EVENTS and args and not _inside(args[0]):
        _deny(f"'{event}' outside sandbox: {args[0]!r}")


class _Timeout(BaseException):
    # BaseException so a bare ``except Exception`` in user code can't swallow it.
    pass


def _alarm(signum, frame):  # noqa: ARG001
    raise _Timeout(f"test exceeded {PER_TEST_TIMEOUT}s (possible infinite loop)")


def _fmt_tb(exc: BaseException) -> str:
    # Hide runner frames; keep only frames from the code under test.
    frames = [
        f
        for f in traceback.extract_tb(exc.__traceback__)
        if _inside(f.filename) and not f.filename.endswith("_runner.py")
    ]
    lines = traceback.format_list(frames) + traceback.format_exception_only(type(exc), exc)
    return "".join(lines).replace(SANDBOX_DIR + os.sep, "")[-2500:]


def _run_case(name: str, fn) -> dict:
    signal.setitimer(signal.ITIMER_REAL, PER_TEST_TIMEOUT)
    try:
        fn()
        return {"name": name, "passed": True}
    except BaseException as exc:  # noqa: BLE001 - we must report *everything*
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {
            "name": name,
            "passed": False,
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": _fmt_tb(exc),
        }
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _collect(module) -> list[tuple[str, object]]:
    cases: list[tuple[str, object]] = []
    for name, obj in inspect.getmembers(module):
        if name.startswith("test") and inspect.isfunction(obj) and obj.__module__ == module.__name__:
            cases.append((name, obj))
        elif inspect.isclass(obj) and issubclass(obj, unittest.TestCase) and obj.__module__ == module.__name__:
            for meth in unittest.TestLoader().getTestCaseNames(obj):
                test = obj(meth)

                def _call(t=test):
                    t.setUp()
                    try:
                        getattr(t, t._testMethodName)()
                    finally:
                        t.tearDown()

                cases.append((f"{name}.{meth}", _call))
    cases.sort(key=lambda c: getattr(c[1], "__code__", None) and c[1].__code__.co_firstlineno or 0)
    return cases


def main() -> None:
    test_module = os.environ.get("CV_TEST_MODULE", "test_solution")
    signal.signal(signal.SIGALRM, _alarm)
    sys.addaudithook(_audit)

    result: dict = {"cases": [], "collection_error": None}
    captured = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        signal.setitimer(signal.ITIMER_REAL, PER_TEST_TIMEOUT)
        try:
            module = importlib.import_module(test_module)
            signal.setitimer(signal.ITIMER_REAL, 0)
            for name, fn in _collect(module):
                result["cases"].append(_run_case(name, fn))
        except BaseException as exc:  # noqa: BLE001
            signal.setitimer(signal.ITIMER_REAL, 0)
            result["collection_error"] = _fmt_tb(exc)
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["violations"] = _violations
    result["captured_output"] = captured.getvalue()[-2000:]
    REAL_STDOUT.write(NONCE + json.dumps(result) + "\n")
    REAL_STDOUT.flush()


if __name__ == "__main__":
    main()
