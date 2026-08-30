"""The self-check, rule by rule.

``checks.py`` is loaded straight from its file: importing the package would
pull in nodes.py and with it ComfyUI, which a test run does not have.
"""

import importlib.util
import pathlib
import sys

_SPEC = importlib.util.spec_from_file_location(
    "checks",
    pathlib.Path(__file__).resolve().parent.parent / "minimax_h3_rewriter" / "checks.py",
)
checks = importlib.util.module_from_spec(_SPEC)
sys.modules["checks"] = checks
_SPEC.loader.exec_module(checks)

FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_FIELDS = (
    "subject_definitions", "summary", "retention_analysis",
    "detailed_description", "overall_soundscape", "non_diegetic_music",
)


def review(body, task="T2VA", duration=10, having=(), **extra):
    """One good answer with the body swapped in, then reviewed."""
    sections = {
        "integrated_multimodal_description": body,
        "overall_soundscape": "Rain on leaves.",
        "non_diegetic_music": "N/A",
    }
    sections.update(extra)
    text = "\n\n".join(f"{name}: {sections[name]}" for name in FIELDS)
    return checks.review(text, sections, FIELDS, task=task, duration=duration, having=having)


def messages(issues):
    return [issue.message for issue in issues]


def test_clean_answer_says_nothing():
    body = "[Shot 1] A quiet street. [Shot 2] At 00:04.000, the camera cuts to a door."
    assert review(body) == []


def test_empty_answer_is_one_finding():
    issues = checks.review("", {}, FIELDS, task="T2VA")
    assert len(issues) == 1 and "empty" in issues[0].message


def test_missing_fields_are_named():
    issues = review("[Shot 1] A street.", overall_soundscape="")
    assert any("overall_soundscape" in m for m in messages(issues))


def test_no_shot_one():
    issues = review("A street with no structure at all.")
    assert any("[Shot 1]" in m for m in messages(issues))


def test_shot_one_must_not_carry_a_cut_time():
    issues = review("[Shot 1] At 00:00.000, a street.")
    assert any("unstamped" in m for m in messages(issues))


def test_shot_numbering_jump():
    body = "[Shot 1] A street. [Shot 3] At 00:04.000, a door."
    issues = review(body)
    assert any("jumps from 1 to 3" in m for m in messages(issues))


def test_back_reference_is_not_a_jump():
    body = ("[Shot 1] A street. [Shot 2] At 00:04.000, a door matching [Shot 1]. "
            "[Shot 3] At 00:07.000, inside.")
    assert review(body) == []


def test_missing_cut_time():
    body = "[Shot 1] A street. [Shot 2] The camera cuts to a door."
    issues = review(body)
    assert any("cut time" in m and "[Shot 2]" in m for m in messages(issues))


def test_cut_times_must_increase():
    body = ("[Shot 1] A street. [Shot 2] At 00:06.000, a door. "
            "[Shot 3] At 00:04.000, inside.")
    issues = review(body)
    assert any("not later" in m for m in messages(issues))


def test_cut_time_past_the_end():
    body = "[Shot 1] A street. [Shot 2] At 00:12.000, a door."
    issues = review(body, duration=10)
    assert any("past the 10s end" in m for m in messages(issues))


def test_dialogue_balance():
    body = "[Shot 1] (S1) says <d>[English] Hello.</d> and later <d>[English] Bye."
    issues = review(body)
    assert any("unbalanced" in m for m in messages(issues))


def test_dialogue_needs_language():
    body = "[Shot 1] (S1) says <d>Hello there.</d>"
    issues = review(body)
    assert any("[Language]" in m for m in messages(issues))


def test_t2va_takes_no_tags():
    body = "[Shot 1] <Picture 1> opens the scene."
    issues = review(body, task="T2VA")
    assert any("has no picture references" in m for m in messages(issues))


def test_i2va_takes_one_picture():
    body = ("[Shot 1] <Picture 1> opens the scene, then <Picture 2> appears.")
    issues = review(body, task="I2VA", having=("image",))
    assert any("only takes picture 1" in m for m in messages(issues))


def test_cited_beyond_connected():
    body = "[Shot 1] <Picture 1> and <Picture 2> side by side."
    issues = review(body, task="FL2VA", having=("image",))
    assert any("only 1 picture(s) reached" in m for m in messages(issues))


def test_connected_but_never_cited():
    body = "[Shot 1] A street."
    issues = review(body, task="FL2VA", having=("image", "image"))
    assert any("never cited" in m for m in messages(issues))


def test_text_only_writers_skip_connected_rules():
    body = "[Shot 1] <Picture 1> opens the scene."
    issues = review(body, task="I2VA", having=None)
    assert not any("connected" in m or "reached this node" in m for m in messages(issues))
    assert all("alignment" in m for m in messages(issues))


def test_omni_task_spelling_is_understood():
    body = "[Shot 1] <Video 1> continues."
    issues = review(body, task="t2av")
    assert any("has no video references" in m for m in messages(issues))


def test_ref2va_subject_without_retention():
    sections = {
        "subject_definitions": "<Subject 1> is the diver. <Subject 2> is the whale.",
        "summary": "[reference generation] A dive.",
        "retention_analysis": "<Subject 1>: fully_preserved - kept.",
        "detailed_description": "[Shot 1] " + "word " * 400,
        "overall_soundscape": "Water.",
        "non_diegetic_music": "N/A",
    }
    text = "\n\n".join(f"{name}: {sections[name]}" for name in REF_FIELDS)
    issues = checks.review(text, sections, REF_FIELDS, task="Ref2VA", duration=10)
    assert any("<Subject 2>" in m and "retention_analysis" in m for m in messages(issues))


def test_ref2va_length_is_a_note():
    sections = {
        "subject_definitions": "<Subject 1> is the diver.",
        "summary": "[reference generation] A dive.",
        "retention_analysis": "<Subject 1>: fully_preserved - kept.",
        "detailed_description": "[Shot 1] Too short.",
        "overall_soundscape": "Water.",
        "non_diegetic_music": "N/A",
    }
    text = "\n\n".join(f"{name}: {sections[name]}" for name in REF_FIELDS)
    issues = checks.review(text, sections, REF_FIELDS, task="Ref2VA", duration=10)
    length = [issue for issue in issues if "words" in issue.message]
    assert length and length[0].level == checks.INFO


def test_alignment_line_expected_for_frame_tasks():
    body = "[Shot 1] <Picture 1> opens the scene."
    issues = review(body, task="I2VA", having=("image",))
    assert any("alignment line" in m for m in messages(issues))

    text = ("For the target video, at 0.00 seconds into the target video, <Picture 1> "
            "(from [Shot 1]) is fully referenced.\n\n"
            "integrated_multimodal_description: [Shot 1] <Picture 1> opens the scene.\n\n"
            "overall_soundscape: Rain.\n\nnon_diegetic_music: N/A")
    sections = {
        "integrated_multimodal_description": "[Shot 1] <Picture 1> opens the scene.",
        "overall_soundscape": "Rain.",
        "non_diegetic_music": "N/A",
    }
    assert checks.review(text, sections, FIELDS, task="I2VA", duration=10, having=("image",)) == []


def test_describe_orders_warnings_first():
    issues = [checks.Issue(checks.INFO, "a note"), checks.Issue(checks.WARN, "a warning")]
    told = checks.describe(sorted(issues, key=lambda issue: issue.level != checks.WARN))
    lines = told.splitlines()
    assert lines[0] == "self-check: 1 warning(s), 1 note(s)"
    assert lines[1] == "! a warning" and lines[2] == "- a note"


def test_describe_is_empty_when_clean():
    assert checks.describe([]) == ""
