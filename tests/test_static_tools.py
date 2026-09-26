from codeverify.tools import analyze_code, check_patch_safety, lint_code
from codeverify.tools.registry import ToolRegistry
from codeverify.tools.sandbox import SubprocessSandbox


def _rules(code: str) -> set[str]:
    return {f.rule for f in analyze_code(code).findings}


def test_ast_structure():
    r = analyze_code("import math\n\ndef area(r):\n    if r < 0:\n        raise ValueError\n    return math.pi * r * r\n")
    assert r.syntax_ok and r.imports == ["math"]
    assert r.functions[0].name == "area" and r.functions[0].complexity == 2


def test_ast_syntax_error():
    r = analyze_code("def f(:\n  pass")
    assert not r.syntax_ok and "line 1" in r.syntax_error


def test_bug_pattern_findings():
    assert "mutable-default-arg" in _rules("def f(x, acc=[]):\n    return acc\n")
    assert "bare-except" in _rules("try:\n    pass\nexcept:\n    pass\n")
    assert "range-len-offset" in _rules("def f(a):\n    for i in range(len(a) - 1):\n        pass\n")
    assert "late-binding-closure" in _rules("fs = []\nfor i in range(3):\n    fs.append(lambda x: x * i)\n")
    assert "mutate-while-iterating" in _rules("def f(xs):\n    for x in xs:\n        xs.remove(x)\n")
    assert "is-literal" in _rules("def f(x):\n    return x is 1\n")
    assert "shadowed-builtin" in _rules("list = [1]\n")
    assert "unreachable-code" in _rules("def f():\n    return 1\n    x = 2\n")


def test_patch_safety_blocks_capability_escalation():
    original = "def f(x):\n    return x\n"
    assert check_patch_safety("def f(x):\n    return x + 1\n", original) is None
    assert "import 'os'" in check_patch_safety("import os\ndef f(x):\n    return x\n", original)
    assert "eval()" in check_patch_safety("def f(x):\n    return eval('x')\n", original)
    assert "__globals__" in check_patch_safety("def f(x):\n    return f.__globals__\n", original)


def test_patch_safety_grandfathers_existing_capabilities():
    original = "import os\ndef f():\n    return os.getcwd()\n"
    assert check_patch_safety("import os\ndef f():\n    return os.getcwd() + '/'\n", original) is None


def test_lint_finds_undefined_name():
    issues = lint_code("def f():\n    return undefined_thing\n").issues
    assert any(i.code == "F821" for i in issues)


def test_registry_records_failures_without_raising():
    reg = ToolRegistry(SubprocessSandbox())
    result, rec = reg.invoke("not_a_tool", node="test")
    assert result is None and not rec.success and "unknown tool" in rec.error
    result, rec = reg.invoke("ast_analyze", node="test", source_code="x = 1\n")
    assert rec.success and result.syntax_ok


def test_langchain_tools_exposed():
    tools = ToolRegistry(SubprocessSandbox()).as_langchain_tools()
    assert {t.name for t in tools} == {"ast_analyze", "lint", "run_tests"}
    out = next(t for t in tools if t.name == "run_tests").invoke(
        {"source_code": "def f():\n    return 1\n", "test_code": "from solution import f\ndef test():\n    assert f() == 1\n"}
    )
    assert '"passed":1' in out.replace(" ", "")
