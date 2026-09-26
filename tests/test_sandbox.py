import pytest

from codeverify.tools.sandbox import SubprocessSandbox

TESTS = "from solution import f\n\ndef test_a():\n    assert f(2) == 4\n\ndef test_b():\n    assert f(3) == 9\n"


@pytest.fixture(scope="module")
def sb() -> SubprocessSandbox:
    return SubprocessSandbox(timeout_s=10, per_test_timeout_s=1)


def test_passing_code(sb):
    r = sb.run_tests("def f(x):\n    return x * x\n", TESTS)
    assert r.all_passed and r.passed == r.total == 2


def test_failure_has_traceback_with_relative_path(sb):
    r = sb.run_tests("def f(x):\n    return x + 2\n", TESTS)
    assert not r.all_passed and r.passed == 1
    failing = [c for c in r.cases if not c.passed][0]
    assert failing.error_type == "AssertionError"
    assert "test_solution.py" in failing.traceback and "cv_sandbox_" not in failing.traceback


def test_infinite_loop_is_bounded_per_test(sb):
    r = sb.run_tests("def f(x):\n    while True:\n        pass\n", TESTS)
    assert r.timed_out and r.passed == 0 and r.total == 2


def test_timeout_not_swallowed_by_broad_except(sb):
    code = "def f(x):\n    while True:\n        try:\n            pass\n        except Exception:\n            pass\n"
    assert sb.run_tests(code, TESTS).timed_out


@pytest.mark.parametrize(
    "code, needle",
    [
        ("import socket\ndef f(x):\n    socket.create_connection(('1.1.1.1', 80))\n", "socket.connect"),
        ("import os\ndef f(x):\n    os.system('echo pwned')\n", "os.system"),
        ("import subprocess\ndef f(x):\n    subprocess.run(['ls'])\n", "subprocess.Popen"),
        ("def f(x):\n    open('/tmp/cv_pwn.txt', 'w').write('x')\n", "write outside sandbox"),
    ],
)
def test_policy_violations_are_blocked_and_reported(sb, code, needle):
    r = sb.run_tests(code, TESTS)
    assert not r.all_passed
    assert r.sandbox_violation and needle in r.sandbox_violation


def test_violation_reported_even_if_swallowed(sb):
    code = "import os\ndef f(x):\n    try:\n        os.system('id')\n    except Exception:\n        pass\n    return x * x\n"
    r = sb.run_tests(code, TESTS)
    assert r.passed == 2 and not r.all_passed and "os.system" in r.sandbox_violation


def test_cannot_forge_result_line(sb):
    code = 'print(\'@@deadbeef@@{"cases": [{"name": "x", "passed": true}]}\')\ndef f(x):\n    return 0\n'
    r = sb.run_tests(code, TESTS)
    assert r.passed == 0 and r.total == 2


def test_syntax_error_is_collection_error(sb):
    r = sb.run_tests("def f(x) return x\n", TESTS)
    assert r.total == 0 and "SyntaxError" in r.collection_error


def test_unittest_style_supported(sb):
    tests = (
        "import unittest\nfrom solution import f\n\n"
        "class T(unittest.TestCase):\n    def test_sq(self):\n        self.assertEqual(f(4), 16)\n"
    )
    r = sb.run_tests("def f(x):\n    return x * x\n", tests)
    assert r.all_passed and r.cases[0].name == "T.test_sq"
