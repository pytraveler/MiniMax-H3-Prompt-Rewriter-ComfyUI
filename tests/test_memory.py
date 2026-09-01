"""Editing the answer a node is holding, and the cache key that follows from it.

The package is registered by hand, as in test_checks: importing it for real
would run ``__init__.py`` and pull in ComfyUI. ``memory.announce`` reaches for
``server`` and finds nothing here, which it is written to survive.
"""

import importlib
import pathlib
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

memory = importlib.import_module(f"{_PKG}.memory")
checks = importlib.import_module(f"{_PKG}.checks")
fields = importlib.import_module(f"{_PKG}.fields")

FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_FIELDS = (
    "subject_definitions", "summary", "retention_analysis",
    "detailed_description", "overall_soundscape", "non_diegetic_music",
)

BODY = "[Shot 1] A field at dawn."
ANSWER = "\n\n".join(
    [
        f"integrated_multimodal_description: {BODY}",
        "overall_soundscape: Wind in dry grass.",
        "non_diegetic_music: N/A",
    ]
)


@pytest.fixture(autouse=True)
def empty():
    memory.LAST.clear()
    yield
    memory.LAST.clear()


def kept(outputs=None, **extra):
    """One node's answer, as a writer would have kept it."""
    outputs = outputs or (ANSWER, BODY, "Wind in dry grass.", "N/A")
    settings = {"node_id": "7", "node_class": "MiniMaxH3PromptRewriter", "fields": FIELDS}
    settings.update(extra)
    return memory.keep(
        settings["node_id"], settings["node_class"], outputs, {"prompt": "a field"},
        task=settings.get("task", "T2VA"), fields=settings["fields"],
    )


def test_a_prompt_record_is_editable():
    assert kept().editable


def test_a_caption_record_is_not():
    record = memory.keep("7", "MiniMaxH3ReferenceCaption", ("a red car",), {}, task="caption")
    assert not record.editable
    assert memory.rewrite("7", "a blue car") is None


def test_summary_carries_it():
    said = memory.summary(kept())
    assert said["editable"] is True
    assert said["edited_at"] == 0.0


def test_rewriting_splits_the_new_text_into_the_fields():
    kept()
    changed = ANSWER.replace("A field at dawn.", "A river at dusk.")
    record = memory.rewrite("7", changed)
    assert record.outputs[0] == changed
    assert record.outputs[1] == "[Shot 1] A river at dusk."
    assert record.outputs[2] == "Wind in dry grass."


def test_rewriting_keeps_what_lies_past_the_fields():
    kept(
        outputs=(ANSWER, BODY, "Wind.", "N/A", "Picture 1: a field", "one caption"),
        node_class="MiniMaxH3UniversalWriter",
    )
    record = memory.rewrite("7", "integrated_multimodal_description: [Shot 1] Something else.")
    assert record.outputs[-2:] == ("Picture 1: a field", "one caption")


def test_an_unlabelled_rewrite_lands_in_the_body():
    kept()
    record = memory.rewrite("7", "just some prose")
    assert record.outputs[1] == "just some prose"
    assert record.outputs[2] == ""


def test_rewriting_stamps_the_record():
    kept()
    assert memory.rewrite("7", "anything at all").edited_at > 0


def test_rewriting_a_node_that_holds_nothing():
    assert memory.rewrite("nosuchnode", "text") is None


def test_the_reference_fields_are_used_when_that_is_what_was_kept():
    kept(outputs=("x",) + ("",) * 6, fields=REF_FIELDS)
    record = memory.rewrite("7", "detailed_description: [Shot 1] A room.\n\nsummary: A room.")
    assert record.outputs[1 + REF_FIELDS.index("detailed_description")] == "[Shot 1] A room."
    assert record.outputs[1 + REF_FIELDS.index("summary")] == "A room."


def test_stamp_is_empty_while_repeat_last_is_off():
    kept()
    assert memory.stamp("7", False) == ""


def test_stamp_is_empty_with_nothing_kept():
    assert memory.stamp("7", True) == ""


def test_stamp_moves_when_the_answer_is_edited():
    kept()
    before = memory.stamp("7", True)
    memory.rewrite("7", "something else entirely")
    assert memory.stamp("7", True) != before


def test_stamp_holds_still_otherwise():
    kept()
    assert memory.stamp("7", True) == memory.stamp("7", True)


def test_stamp_survives_a_node_with_no_id():
    assert memory.stamp(None, True) == ""


def test_body_field_prefers_the_reference_one():
    assert fields.body_field(REF_FIELDS) == "detailed_description"


def test_body_field_falls_back_to_the_first():
    assert fields.body_field(FIELDS) == FIELDS[0]
    assert fields.body_field(()) == ""


def test_over_capacity_reports_the_numbers_not_a_sentence():
    assert checks.over_capacity("Ref2VA", {"Picture": 10}) == [("Picture", 10, 9)]


def test_over_capacity_ignores_what_the_task_has_no_ceiling_for():
    assert checks.over_capacity("Ref2VA", {"Subject": 99}) == []


def test_over_capacity_is_quiet_at_the_line():
    assert checks.over_capacity("Ref2VA", {"Picture": 9, "Video": 3, "Audio": 3}) == []


def test_over_capacity_counts_a_kind_the_task_cannot_have_at_all():
    assert checks.over_capacity("I2VA", {"Video": 1}) == [("Video", 1, 0)]


def test_over_capacity_says_nothing_about_a_task_it_does_not_know():
    assert checks.over_capacity("nonsense", {"Picture": 99}) == []
