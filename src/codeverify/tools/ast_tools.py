"""Static analysis over the Python AST — never executes the code.

Provides three things to the agents:

* structural facts (functions, arguments, imports, cyclomatic complexity);
* **bug-pattern findings** — cheap, high-signal hints such as mutable default
  arguments or ``range(len(x) - 1)`` that ground fault localisation;
* a **patch safety policy** — rejects LLM patches that introduce dangerous
  capabilities (``os``, ``subprocess``, ``eval`` …) absent from the original.
"""

from __future__ import annotations

import ast
import builtins

from codeverify.schemas import AstFinding, AstReport, FunctionInfo

#: Modules/names a *patch* may not introduce (they are allowed if the original had them).
FORBIDDEN_MODULES = frozenset(
    {"os", "sys", "subprocess", "socket", "shutil", "ctypes", "importlib", "builtins",
     "inspect", "gc", "signal", "pathlib", "multiprocessing", "threading", "pickle",
     "marshal", "urllib", "http", "requests"}
)  # fmt: skip
FORBIDDEN_CALLS = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "globals", "setattr", "delattr", "breakpoint"}
)
_SHADOWABLE = frozenset(n for n in dir(builtins) if not n.startswith("_") and n.islower())
_BRANCHES = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.BoolOp, ast.IfExp,
             ast.comprehension, ast.ExceptHandler, ast.Match)  # fmt: skip


def _complexity(fn: ast.AST) -> int:
    return 1 + sum(isinstance(n, _BRANCHES) for n in ast.walk(fn))


def _imports(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[0])
    return sorted(set(names))


class _BugPatternVisitor(ast.NodeVisitor):
    """Collect heuristic bug findings. Each rule is intentionally conservative."""

    def __init__(self) -> None:
        self.findings: list[AstFinding] = []

    def _add(self, rule: str, node: ast.AST, msg: str) -> None:
        self.findings.append(AstFinding(rule=rule, line=getattr(node, "lineno", 1), message=msg))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            if isinstance(default, ast.List | ast.Dict | ast.Set) or (
                isinstance(default, ast.Call) and getattr(default.func, "id", "") in {"list", "dict", "set"}
            ):
                self._add("mutable-default-arg", default,
                          f"'{node.name}' uses a mutable default argument shared across calls")  # fmt: skip
        body = node.body
        for i, stmt in enumerate(body[:-1]):
            if isinstance(stmt, ast.Return | ast.Raise):
                self._add("unreachable-code", body[i + 1], "statement after return/raise is unreachable")
                break
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self._add("bare-except", node, "bare 'except:' swallows every error incl. KeyboardInterrupt")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op, right in zip(node.ops, node.comparators, strict=False):
            if isinstance(op, ast.Is | ast.IsNot) and isinstance(right, ast.Constant) and right.value is not None:
                self._add("is-literal", node, "identity comparison with a literal; use '=='")
            if isinstance(op, ast.Eq | ast.NotEq) and isinstance(right, ast.Constant) and right.value is None:
                self._add("eq-none", node, "compare to None with 'is'")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # range(len(x) - 1) / range(1, len(x)) are classic off-by-one suspects.
        if getattr(node.func, "id", None) == "range":
            for arg in node.args:
                if (
                    isinstance(arg, ast.BinOp)
                    and isinstance(arg.op, ast.Sub | ast.Add)
                    and isinstance(arg.left, ast.Call)
                    and getattr(arg.left.func, "id", None) == "len"
                ):
                    self._add("range-len-offset", node, "range bound is len(...) +/- k; check for off-by-one")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        # Mutating a dict/list while iterating over it.
        if isinstance(node.iter, ast.Name):
            target = node.iter.id
            for sub in ast.walk(node):
                if isinstance(sub, ast.Delete) and any(
                    isinstance(t, ast.Subscript) and getattr(t.value, "id", None) == target for t in sub.targets
                ):
                    self._add("mutate-while-iterating", sub, f"'{target}' is mutated while being iterated")
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and getattr(sub.func.value, "id", None) == target
                    and sub.func.attr in {"pop", "remove", "append", "insert", "clear"}
                ):
                    self._add("mutate-while-iterating", sub, f"'{target}.{sub.func.attr}' while iterating")
        # Lambdas defined in a loop that close over the loop variable (late binding).
        if isinstance(node.target, ast.Name):
            var = node.target.id
            for sub in ast.walk(node):
                if isinstance(sub, ast.Lambda):
                    params = {a.arg for a in sub.args.args}
                    if var not in params and any(
                        isinstance(n, ast.Name) and n.id == var for n in ast.walk(sub.body)
                    ):
                        self._add("late-binding-closure", sub, f"lambda captures loop variable '{var}' by reference")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in _SHADOWABLE:
                self._add("shadowed-builtin", node, f"assignment shadows builtin '{t.id}'")
        self.generic_visit(node)


def analyze_code(source: str) -> AstReport:
    """Parse ``source`` and return structure, bug-pattern findings, and imports."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return AstReport(syntax_ok=False, syntax_error=f"line {exc.lineno}: {exc.msg}")
    functions = [
        FunctionInfo(
            name=n.name,
            lineno=n.lineno,
            end_lineno=n.end_lineno or n.lineno,
            args=[a.arg for a in n.args.args],
            complexity=_complexity(n),
        )
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    visitor = _BugPatternVisitor()
    visitor.visit(tree)
    imports = _imports(tree)
    return AstReport(
        syntax_ok=True,
        functions=functions,
        imports=imports,
        findings=visitor.findings,
        forbidden_imports=[m for m in imports if m in FORBIDDEN_MODULES],
    )


def check_patch_safety(patched: str, original: str) -> str | None:
    """Return a violation message if the patch adds a forbidden capability, else ``None``.

    Capabilities already present in ``original`` are grandfathered in, so we
    only block *escalation* by the model (e.g. tampering with the test harness
    via ``sys``/``inspect`` or shelling out with ``os``).
    """
    try:
        new_tree, old_tree = ast.parse(patched), ast.parse(original)
    except SyntaxError:
        return None  # syntax problems are reported by the verifier, not the policy

    def calls(tree: ast.AST) -> set[str]:
        return {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }

    def dunders(tree: ast.AST) -> set[str]:
        return {
            n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in {"__globals__", "__builtins__", "__code__", "__subclasses__"}
        }  # fmt: skip

    added_mods = (set(_imports(new_tree)) - set(_imports(old_tree))) & FORBIDDEN_MODULES
    added_calls = (calls(new_tree) - calls(old_tree)) & FORBIDDEN_CALLS
    added_dunder = dunders(new_tree) - dunders(old_tree)
    problems = [f"import '{m}'" for m in sorted(added_mods)]
    problems += [f"call '{c}()'" for c in sorted(added_calls)]
    problems += [f"attribute '{d}'" for d in sorted(added_dunder)]
    return ("patch introduces forbidden capability: " + ", ".join(problems)) if problems else None
