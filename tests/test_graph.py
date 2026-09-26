"""End-to-end graph behaviour with a deterministic scripted LLM."""

from langgraph.types import Command

from codeverify.graph import build_graph, graph_config, run_task
from codeverify.llm import LLMError, ScriptedLLM
from codeverify.schemas import BugTask
from tests.conftest import FIXED, TESTS, WRONG, patch_reply


def _nodes(state):
    return [t.node for t in state["transitions"]]


def test_resolves_on_first_attempt(task, settings):
    out = run_task(task, settings, llm=ScriptedLLM([patch_reply(FIXED)]))
    st = out["state"]
    assert st["status"] == "resolved" and st["final_code"] == FIXED
    assert _nodes(st) == ["planner", "inspector", "patch_generator", "verifier", "human_review"]
    assert out["metrics"].tool_calls == 6 and out["metrics"].tool_success_rate == 1.0


def test_failed_verification_routes_back_with_traceback(task, settings):
    llm = ScriptedLLM([patch_reply(WRONG), patch_reply(FIXED)])
    st = run_task(task, settings, llm=llm)["state"]
    assert st["status"] == "resolved" and st["patch_attempts"] == 2
    assert _nodes(st).count("patch_generator") == 2
    retry_prompt = llm.calls[1][1]
    assert "PREVIOUS ATTEMPT #1 FAILED" in retry_prompt and "AssertionError" in retry_prompt
    assert [h.passed for h in st["attempt_history"]] == [False, True]


def test_retry_loop_is_bounded(task, settings):
    st = run_task(task, settings, llm=ScriptedLLM(lambda s, u: patch_reply(WRONG)))["state"]
    assert st["status"] == "failed" and st["patch_attempts"] == settings.max_patch_attempts
    assert "max patch attempts" in st["termination_reason"]


def test_global_step_budget_terminates(task, settings):
    tight = settings.model_copy(update={"max_patch_attempts": 10, "max_graph_steps": 8})
    st = run_task(task, tight, llm=ScriptedLLM(lambda s, u: patch_reply(WRONG)))["state"]
    assert st["status"] == "failed" and "step budget" in st["termination_reason"]
    assert st["step_count"] <= tight.max_graph_steps


def test_already_passing_short_circuits(settings):
    ok = BugTask(task_id="ok", source_code=FIXED, test_code=TESTS)
    llm = ScriptedLLM([])
    st = run_task(ok, settings, llm=llm)["state"]
    assert st["status"] == "resolved" and _nodes(st) == ["planner", "inspector"] and not llm.calls


def test_unsafe_patch_is_blocked_before_execution(task, settings):
    evil = "import os\ndef avg(xs):\n    os.system('echo hi')\n    return sum(xs) / len(xs)\n"
    st = run_task(task, settings, llm=ScriptedLLM([patch_reply(evil), patch_reply(FIXED)]))["state"]
    first = st["attempt_history"][0]
    assert first.safety_violation and "import 'os'" in first.safety_violation
    assert st["status"] == "resolved"


def test_llm_outage_degrades_gracefully(task, settings):
    def down(system, user):
        raise LLMError("provider down")

    st = run_task(task, settings, llm=ScriptedLLM(down))["state"]
    assert st["status"] == "failed" and st["patch_attempts"] == settings.max_patch_attempts


def test_llm_planner_mode_falls_back_to_rules(task, settings):
    s = settings.model_copy(update={"planner_mode": "llm"})
    llm = ScriptedLLM(["garbage"] * 3 + [patch_reply(FIXED)])
    st = run_task(task, s, llm=llm)["state"]
    assert st["status"] == "resolved" and any(s.tool == "run_tests" for s in st["plan"].steps)
    assert st["llm_calls"][0].success is False


def test_human_in_the_loop_interrupt_and_revise(task, settings):
    s = settings.model_copy(update={"require_human_review": True})
    llm = ScriptedLLM([patch_reply(FIXED), patch_reply(FIXED, root_cause="revised per reviewer")])
    graph = build_graph(s, llm=llm)
    cfg = graph_config(s, "hitl-1")
    graph.invoke({"task": task, "step_count": 0}, cfg)
    snap = graph.get_state(cfg)
    assert snap.next == ("human_review",) and snap.tasks[0].interrupts[0].value["reason"] == "patch verified"

    graph.invoke(Command(resume={"decision": "revise", "feedback": "add a docstring"}), cfg)
    assert "add a docstring" in llm.calls[-1][1]  # feedback reached the Patch Generator
    graph.invoke(Command(resume={"decision": "approve", "reviewer": "alice"}), cfg)
    final = graph.get_state(cfg).values
    assert final["status"] == "resolved" and final["review"].reviewer == "alice"


def test_human_reject(task, settings):
    s = settings.model_copy(update={"require_human_review": True})
    graph = build_graph(s, llm=ScriptedLLM([patch_reply(FIXED)]))
    cfg = graph_config(s, "hitl-2")
    graph.invoke({"task": task, "step_count": 0}, cfg)
    graph.invoke(Command(resume={"decision": "reject", "feedback": "no"}), cfg)
    assert graph.get_state(cfg).values["status"] == "rejected"
