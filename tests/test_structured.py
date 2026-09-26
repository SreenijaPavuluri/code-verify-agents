import pytest
from pydantic import ValidationError

from codeverify.llm import ScriptedLLM, StructuredOutputError, structured_call
from codeverify.llm.structured import extract_code_block, extract_json
from codeverify.schemas import PatchProposal, Plan


def test_extract_json_variants():
    assert extract_json('noise ```json\n{"a": 1}\n``` tail') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_extract_last_python_block():
    text = "```python\nx = 1\n```\nthen\n```python\nx = 2\n```"
    assert extract_code_block(text) == "x = 2\n"


def test_repair_loop_recovers_from_invalid_output():
    llm = ScriptedLLM([
        "I think the plan is to look around.",  # no JSON
        '{"bug_hypothesis": "x", "steps": [{"tool": "lint", "purpose": "check it"}]}',  # too short + no run_tests
        '{"bug_hypothesis": "off by one", "steps": [{"tool": "run_tests", "purpose": "reproduce"}]}',
    ])
    plan, rec = structured_call(llm, Plan, system="s", user="u", node="planner", max_repairs=2)
    assert plan.steps[0].tool == "run_tests" and rec.repairs == 2 and rec.success
    # The validation error is fed back to the model on the repair turn.
    assert "INVALID" in llm.calls[-1][1] and "run_tests" in llm.calls[-1][1]


def test_repair_loop_gives_up():
    llm = ScriptedLLM(["nope"] * 3)
    with pytest.raises(StructuredOutputError) as exc:
        structured_call(llm, Plan, system="s", user="u", node="planner", max_repairs=2)
    assert exc.value.record.success is False and exc.value.record.repairs == 2


def test_code_field_spliced_from_fence_and_extra_keys_forbidden():
    reply = '```json\n{"root_cause": "bad math", "change_summary": "fix it", "confidence": 0.9}\n```\n```python\ndef f():\n    return 1\n```'
    patch, _ = structured_call(ScriptedLLM([reply]), PatchProposal, system="s", user="u", node="p",
                               code_field="patched_code")  # fmt: skip
    assert patch.patched_code == "def f():\n    return 1\n"
    with pytest.raises(ValidationError):
        PatchProposal(root_cause="abc", change_summary="abc", patched_code="x", hallucinated=True)


def test_plan_requires_run_tests():
    with pytest.raises(ValidationError):
        Plan(bug_hypothesis="something", steps=[{"tool": "lint", "purpose": "lint it"}])
    with pytest.raises(ValidationError):
        Plan(bug_hypothesis="something", steps=[{"tool": "rm_rf", "purpose": "nope"}])
