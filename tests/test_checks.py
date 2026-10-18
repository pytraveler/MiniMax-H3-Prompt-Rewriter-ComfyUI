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

import pytest

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


def test_a_character_flood_is_a_loop():
    unit, span = checks.looping("A cinematic shot. " + "0" * 1800)
    assert unit == "0" and span == checks.LOOP_TAIL


def test_a_repeating_phrase_is_a_loop():
    found = checks.looping("Opening line. " + "shot 1 " * 300)
    assert found and found[0].strip() == "shot 1"


def test_a_loop_is_reported_before_the_missing_fields():
    issues = review("[Shot 1] A street. " + "0" * 400, overall_soundscape="")
    assert issues[0].code == "loop"
    assert 'repeating "0"' in issues[0].message


def test_legitimate_repetition_is_not_a_loop():
    assert checks.looping("\n".join(
        f"<Subject {n}>: fully_preserved - kept." for n in range(1, 10)
    )) is None
    assert checks.looping("\n".join(
        f"[Shot {n}] At 00:0{n}.000, the camera cuts to a close-up." for n in range(1, 9)
    )) is None
    assert checks.looping("") is None


def test_embedding_tokens_are_not_a_loop():
    """A prompt carrying effects repeats a fixed unit at a short period.

    That is the exact shape this rule looks for, and getting it wrong costs
    more than a stray warning: ``looping`` is a live stop condition for the
    three engines, and "loop" is in ``repair.FIXABLE``, so the pack would burn
    a second generation trying to mend text a node wrote on purpose.
    """
    group = " ".join(f"embedding:minimaxh3_effect_{n}" for n in range(10)) + " "
    assert checks.looping("Opening line. " + group * 30) is None
    assert checks.looping("Opening line. " + "embedding:minimaxh3_bullet_time " * 40) is None
    assert checks.looping("EMBEDDING:Some_Other_Pack_Entirely " * 40) is None


def test_a_loop_beside_the_tokens_is_still_a_loop():
    """Taking them out must not take the cycle in the words with them."""
    found = checks.looping("embedding:minimaxh3_dark_magic " + "shot 1 " * 300)
    assert found and found[0].strip() == "shot 1"


def codes(text):
    return [(issue.level, issue.code) for issue in checks._embeddings(text)]


def test_a_working_embedding_token_is_not_worth_a_word():
    assert codes("A cat. embedding:minimaxh3_dark_magic walks along a fence.") == []
    assert codes("embedding:minimaxh3_dark_magic opens the prompt, which is allowed.") == []
    assert codes("A cat. embedding:minimaxh3_dark_magic, walks.") == []


def test_the_word_embedding_in_a_sentence_is_not_a_token():
    """The tokenizer needs a name straight after the colon, and this has none."""
    assert codes("An embedding: a learned vector of 5120 numbers per position.") == []


def test_a_capital_letter_is_a_warning():
    """word.startswith("embedding:") is case-sensitive, and nothing says so."""
    found = checks._embeddings("A cat. Embedding:minimaxh3_dark_magic walks.")
    assert [(issue.level, issue.code) for issue in found] == [(checks.WARN, "embedding")]
    assert "case-sensitive" in found[0].message


def test_a_token_stuck_to_the_word_in_front_is_a_warning():
    found = checks._embeddings("A cat.embedding:minimaxh3_dark_magic walks.")
    assert [(issue.level, issue.code) for issue in found] == [(checks.WARN, "embedding")]


def test_a_full_stop_on_the_name_is_a_warning_and_a_comma_is_not():
    assert codes("A cat. embedding:minimaxh3_dark_magic. Walks.") == [(checks.WARN, "embedding")]
    assert codes("A cat. embedding:minimaxh3_dark_magic, walks.") == []


def test_a_name_that_is_not_one_of_the_ten_is_only_a_note():
    """It may well be somebody's own textual inversion, which is their business."""
    assert codes("A cat. embedding:my_own_inversion walks.") == [(checks.INFO, "embedding")]


def test_the_same_mistake_ten_times_is_said_once():
    text = " ".join("Embedding:minimaxh3_dark_magic" for _ in range(10))
    found = checks._embeddings("Opening. " + text)
    assert len(found) == 1


def test_an_embedding_finding_reaches_the_review():
    issues = review("[Shot 1] A street.Embedding:minimaxh3_dark_magic cuts to a door.")
    assert any(issue.code == "embedding" for issue in issues)


def test_an_embedding_is_not_something_to_generate_again():
    """The token was put there by a node, deterministically.

    A regeneration would not reproduce it, and repair.instruct would quote the
    finding -- token and all -- back into the writer's prompt.
    """
    assert "embedding" not in repair.FIXABLE


def test_a_clean_answer_still_says_nothing():
    body = "[Shot 1] A quiet street. [Shot 2] At 00:04.000, the camera cuts to a door."
    assert review(body) == []


def test_a_loop_is_worth_one_more_attempt():
    sections = {"integrated_multimodal_description": "[Shot 1] a " + "0" * 400,
                "overall_soundscape": "", "non_diegetic_music": ""}
    text = "integrated_multimodal_description: " + sections["integrated_multimodal_description"]
    issues = checks.review(text, sections, FIELDS, task="T2VA")
    assert any(issue.code == "loop" for issue in issues)
    assert not repair.hopeless(issues, sections, FIELDS)


def test_the_loop_rule_never_quotes_the_loop_back():
    issues = [checks.Issue(checks.WARN, 'gets stuck repeating "0" for 400 characters', "loop")]
    said = repair.instruct(issues)
    assert "0" * 4 not in said
    assert "Write each field once" in said


STRIP_BLOCK = "\n".join(
    [
        "Picture 1: a black cat against a white wall.",
        "Picture 2: a wooden fence with a gate.",
        "Video 1: a man walking down a wet street.",
        "Picture 3: a man beside a vintage car.",
        "Video 2: a person in a blue dinosaur mask beside a car.",
        "Picture 4: a turtle with a gemstone shell.",
        "Audio 1: a deep male voice.",
        "Audio 2: a synthetic voice.",
        "Audio 3: an assertive male voice.",
    ]
)

STRIP_ANSWER = " ".join(
    f"<{word} {number}>"
    for word, top in (("Picture", 4), ("Video", 2), ("Audio", 3))
    for number in range(1, top + 1)
)


def ref_tags(having):
    sections = {name: "" for name in REF_FIELDS}
    sections["subject_definitions"] = STRIP_ANSWER
    text = "\n\n".join(f"{name}: {sections[name]}" for name in REF_FIELDS)
    issues = checks.review(text, sections, REF_FIELDS, task="Ref2VA", having=having)
    return [issue.message for issue in issues if issue.code == "tags"]


def test_a_badge_is_counted_as_what_the_block_calls_it():
    assert sorted(checks.block_kinds(STRIP_BLOCK)) == sorted(
        ["image"] * 4 + ["video"] * 2 + ["audio"] * 3
    )
    assert ref_tags(checks.block_kinds(STRIP_BLOCK)) == []


def test_counting_sockets_is_what_went_wrong():
    """The same answer, judged by the kinds on the sockets: two findings, both false."""
    sockets = ["image"] * 5 + ["video"] + ["audio"] * 3
    said = " ".join(ref_tags(sockets))
    assert "<Video 2> is cited" in said
    assert "<Picture 5>" in said


def test_a_subject_line_is_not_an_asset():
    assert checks.block_kinds("Subject 1: a red kite.\nPicture 1: a beach.") == ["image"]


def test_labels_carried_in_on_previous_count_once_each():
    block = "Picture 1: a beach.\nPicture 2: a pier.\npicture 2: the pier again.\nAudio 1: gulls."
    assert sorted(checks.block_kinds(block)) == ["audio", "image", "image"]


def test_no_block_means_nothing_was_shown():
    assert checks.block_kinds("") == []
    assert checks.block_kinds(None) == []


def test_sounds_alone_are_an_audio_only_set():
    assert checks.audio_only("Ref2VA", {"Audio": 2})
    assert checks.audio_only("ref2av", {"Audio": 1, "Picture": 0})


def test_anything_to_see_makes_it_not_audio_only():
    assert not checks.audio_only("Ref2VA", {"Audio": 1, "Picture": 1})
    assert not checks.audio_only("Ref2VA", {"Audio": 1, "Video": 1})


def test_a_subject_is_something_to_see():
    """The generator receives a subject as one more picture."""
    assert not checks.audio_only("Ref2VA", {"Audio": 1, "Subject": 1})


def test_no_sound_at_all_is_not_audio_only():
    assert not checks.audio_only("Ref2VA", {})
    assert not checks.audio_only("Ref2VA", {"Picture": 2})


def test_audio_only_is_a_reference_rule():
    assert not checks.audio_only("T2VA", {"Audio": 1})
    assert not checks.audio_only("nonsense", {"Audio": 1})


@pytest.mark.parametrize("width, height", [(4000, 1000), (1000, 4000), (1920, 1080), (64, 64)])
def test_a_frame_inside_one_to_four_is_in_shape(width, height):
    assert not checks.out_of_shape(width, height)


@pytest.mark.parametrize("width, height", [(4001, 1000), (1000, 4001), (5760, 1080)])
def test_a_frame_past_one_to_four_is_out_of_shape(width, height):
    assert checks.out_of_shape(width, height)


def test_a_size_that_is_not_one_is_not_out_of_shape():
    assert not checks.out_of_shape(0, 100)
    assert not checks.out_of_shape(100, 0)


def ref_answer_citing(*labels):
    sections = {name: "" for name in REF_FIELDS}
    sections["subject_definitions"] = " ".join(f"{label}: a sound." for label in labels)
    return "\n\n".join(f"{name}: {sections[name]}" for name in REF_FIELDS), sections


def test_a_clips_sound_may_be_cited_past_the_three_sockets():
    """Three standalone sounds and two clip sounds are five <Audio N>, every one real."""
    text, sections = ref_answer_citing(*(f"<Audio {n}>" for n in range(1, 6)))
    issues = checks.review(text, sections, REF_FIELDS, task="Ref2VA", having=["audio"] * 5)
    assert [issue for issue in issues if issue.code == "tags"] == []


def test_more_sounds_than_three_sockets_and_three_clips_is_still_too_many():
    text, sections = ref_answer_citing("<Audio 7>")
    issues = checks.review(text, sections, REF_FIELDS, task="Ref2VA", having=None)
    assert any("only takes audio 1-6" in issue.message for issue in issues)


def test_the_sockets_themselves_still_take_three_sounds():
    assert checks.over_capacity("Ref2VA", {"Audio": 4}) == [("Audio", 4, 3)]
