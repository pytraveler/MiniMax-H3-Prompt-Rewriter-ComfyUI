"""The self-check and the repair it feeds, rule by rule.

The package is registered by hand rather than imported: running its
``__init__.py`` would pull in nodes.py and with it ComfyUI, which a test run
does not have. Giving the name a ``__path__`` is enough for the submodules to
import each other normally, which is what ``repair`` needs.
"""

import importlib
import pathlib
import sys
import types

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

checks = importlib.import_module(f"{_PKG}.checks")
repair = importlib.import_module(f"{_PKG}.repair")

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


MIXED = [
    checks.Issue(checks.WARN, "a warning"),
    checks.Issue(checks.INFO, "a note"),
]


def test_report_all_keeps_everything():
    assert checks.reportable(MIXED, checks.REPORT_ALL) == MIXED


def test_report_warnings_drops_notes():
    kept = checks.reportable(MIXED, checks.REPORT_WARNINGS)
    assert [issue.message for issue in kept] == ["a warning"]


def test_report_none_says_nothing():
    assert checks.reportable(MIXED, checks.REPORT_NONE) == []


def test_unknown_setting_reports_everything():
    assert checks.reportable(MIXED, "whatever this is") == MIXED
    assert checks.reportable(MIXED, "") == MIXED


def test_report_none_still_leaves_the_findings_for_fix_once():
    issues = review("[Shot 1] A street. [Shot 2] At 00:12.000, a door.", duration=10)
    assert issues and checks.reportable(issues, checks.REPORT_NONE) == []


CLEAN = [checks.Issue(checks.WARN, "a warning", "shots")]


def test_fixable_keeps_only_mechanical_findings():
    issues = [
        checks.Issue(checks.WARN, "shots", "shots"),
        checks.Issue(checks.WARN, "alignment", "alignment"),
        checks.Issue(checks.INFO, "length", "length"),
        checks.Issue(checks.WARN, "tags", "tags"),
    ]
    assert [i.code for i in repair.fixable(issues)] == ["shots", "tags"]


def test_alignment_is_not_worth_a_generation():
    only = [checks.Issue(checks.WARN, "no alignment line", "alignment")]
    assert repair.fixable(only) == [] and repair.instruct(only) == ""


def test_instruct_lists_the_findings_as_rules():
    told = repair.instruct(CLEAN)
    assert told.startswith("\n\n") and "Write it" in told and "- a warning" in told


def test_hopeless_on_an_empty_answer():
    issues = checks.review("", {}, FIELDS, task="T2VA")
    assert repair.hopeless(issues, {}, FIELDS)


def test_hopeless_when_half_the_fields_are_gone():
    sections = {"integrated_multimodal_description": "[Shot 1] A street."}
    issues = checks.review("x", sections, FIELDS, task="T2VA")
    assert repair.hopeless(issues, sections, FIELDS)


def test_one_missing_field_is_not_hopeless():
    sections = {
        "integrated_multimodal_description": "[Shot 1] A street.",
        "overall_soundscape": "Rain.",
        "non_diegetic_music": "",
    }
    issues = checks.review("x", sections, FIELDS, task="T2VA")
    assert not repair.hopeless(issues, sections, FIELDS)


def test_better_needs_a_real_improvement():
    warn = checks.Issue(checks.WARN, "w", "shots")
    note = checks.Issue(checks.INFO, "n", "length")
    assert repair.better([warn, warn], [warn])
    assert repair.better([warn, note], [warn])
    assert not repair.better([warn], [warn])          # a tie keeps the first
    assert not repair.better([warn], [warn, note])
    assert not repair.better([warn], [checks.Issue(checks.WARN, "other", "tags")])


def test_restore_alignment_prepends_the_line():
    got = repair.restore_alignment("integrated_multimodal_description: [Shot 1] x", "LINE")
    assert got.startswith("LINE\n\nintegrated")


def test_restore_alignment_leaves_an_empty_answer_alone():
    assert repair.restore_alignment("", "LINE") == ""
    assert repair.restore_alignment("body", "") == "body"


def test_final_shot_reads_the_highest_number():
    assert repair.final_shot("[Shot 1] a [Shot 4] At 00:03.000, b [Shot 2] back to") == 4
    assert repair.final_shot("no shots here") == 1
