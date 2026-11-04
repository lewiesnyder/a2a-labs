"""HITL conventions: building and reading prompts/answers, and reading a free-text reply."""

from __future__ import annotations

from a2a.helpers import new_message
from a2a_labs_common.hitl import (
    Answer,
    Interrupt,
    Option,
    Prompt,
    answer_part,
    answers_part,
    extract_answer,
    extract_answers,
    extract_interrupts,
    extract_prompt,
    interpret_reply,
    interrupts_part,
    prompt_part,
)
from a2a_labs_common.models.decision import FakeDecisionModel

APPROVAL = Prompt(
    kind="approval", prompt="Approve $5?", options=[Option(id="approve", label="Yes")]
)
CHOICE = Prompt(
    kind="choice",
    prompt="Seat?",
    options=[Option(id="aisle", label="Aisle"), Option(id="window", label="Window seat")],
)


def test_prompt_and_answer_round_trip_through_data_parts() -> None:
    msg = new_message([prompt_part(APPROVAL)])
    got = extract_prompt(list(msg.parts))
    assert got is not None and got.kind == "approval" and got.options[0].id == "approve"
    ans = Answer(decision="edit", edit={"amount": 3}, by="ana")
    back = extract_answer(list(new_message([answer_part(ans)]).parts))
    assert back == ans
    many = extract_answers(list(new_message([answers_part({"q1": ans})]).parts))
    assert many == {"q1": ans}


def test_readers_return_none_or_empty_for_other_messages() -> None:
    plain = list(new_message([prompt_part(APPROVAL)]).parts)
    assert extract_answer(plain) is None and extract_answers(plain) is None
    assert extract_interrupts(plain) == []
    assert extract_prompt([]) is None


def test_interrupts_list_round_trips() -> None:
    i = Interrupt(
        id="a:1",
        agent="a",
        task_id="t",
        context_id="c",
        state="INPUT_REQUIRED",
        prompt=CHOICE,
        deadline_ts=5.0,
    )
    got = extract_interrupts(list(new_message([interrupts_part([i])]).parts))
    assert got == [i]


async def test_a_choice_is_read_by_id_or_label_without_a_model() -> None:
    model = FakeDecisionModel()
    ans, _ = await interpret_reply("the window seat, thanks", CHOICE, model)
    assert ans is not None and ans.choice == "window"
    ans, _ = await interpret_reply("AISLE", CHOICE, model)
    assert ans is not None and ans.choice == "aisle"
    assert (await interpret_reply("anything", CHOICE, model))[0] is None


async def test_free_text_input_is_taken_as_is_but_not_when_empty() -> None:
    p = Prompt(kind="input", prompt="Name?")
    assert (await interpret_reply("Ada", p, FakeDecisionModel()))[0] == Answer(text="Ada")
    assert (await interpret_reply("  ", p, FakeDecisionModel()))[0] is None


async def test_an_approval_reply_goes_through_the_decision_model() -> None:
    for scripted, expect in (("approve", "approve"), ("reject", "reject")):
        model = FakeDecisionModel(answers={"reply": scripted})
        ans, probs = await interpret_reply("sure", APPROVAL, model)
        assert ans is not None and ans.decision == expect and probs[expect] == 1.0
    ans, probs = await interpret_reply(
        "???", APPROVAL, FakeDecisionModel(answers={"reply": "unclear"})
    )
    assert ans is None and probs["unclear"] == 1.0
