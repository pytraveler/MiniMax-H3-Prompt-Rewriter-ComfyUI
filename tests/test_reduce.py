"""The reduction's deterministic half, which is most of it.

What a model does with the paragraph cannot be tested here. What reaches the
model can, and that is where this node's behaviour actually lives: if the shot
markers, the reference tags or the sound sections are still in the text, no
system prompt saves the result, and if they are gone the instruction over them
is short enough that a small model manages.

The package is registered by hand rather than imported, the way the other tests
do it: running ``__init__.py`` would pull in ComfyUI, which a test run does not
have. ``reduce`` itself imports nothing but ``checks``, ``fields`` and
``constants``, all of which are equally free of it.
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

reduce = importlib.import_module(f"{_PKG}.reduce")


I2VA = """For the target video, at 0.00 seconds into the target video, <Picture 1> \
(from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a low-angle medium shot \
frames a sleek black cat walking steadily along the top of a weathered wooden fence in a quiet \
suburban yard at dusk. The camera tracks right with small amplitude at slow speed. [Shot 2] At \
0:04 the cat pauses to glance over the fence toward an open garden beyond, then resumes its \
unhurried pace.

overall_soundscape: A gentle evening breeze rustles through nearby grass while the cat's paws \
tap softly against the wooden fence.

non_diegetic_music: A sparse piano melody at a slow tempo."""

REF2VA = """subject_definitions: <Subject 1> is a young woman with long dark hair.

summary: [Ref2VA] The woman from <Picture 1> walks through a market.

retention_analysis: <Picture 1> retains the face and the blue cardigan.

detailed_description: [Shot 1] The woman, consistent with <Subject 1>, walks between the stalls \
of a covered market, matching <Picture 1>. She stops at a fruit stand and says <d>How much for \
these?</d> before moving on.

overall_soundscape: Market chatter and footsteps on stone.

non_diegetic_music: N/A"""


class TestStrip:
    def test_the_alignment_sentence_never_reaches_the_body(self):
        assert "0.00 seconds" not in reduce.strip(I2VA).body

    def test_shot_markers_come_off_and_are_counted(self):
        stripped = reduce.strip(I2VA)
        assert stripped.shots == 2
        assert "[Shot" not in stripped.body
        assert "0:04" not in stripped.body

    def test_reference_tags_come_off_with_the_preposition_that_carried_them(self):
        stripped = reduce.strip(REF2VA)
        assert "<Picture" not in stripped.body
        assert "<Subject" not in stripped.body
        assert "consistent with" not in stripped.body
        assert "matching" not in stripped.body
        assert "walks between the stalls" in stripped.body

    def test_a_citation_written_as_a_parenthetical_takes_both_its_commas(self):
        assert "The woman walks between" in reduce.strip(REF2VA).body

    def test_a_word_left_at_the_start_of_a_sentence_is_capitalised(self):
        body = reduce.strip(I2VA).body
        assert ". The cat pauses" in body
        assert ". the cat" not in body

    def test_no_double_spaces_or_stranded_punctuation_survive(self):
        for text in (I2VA, REF2VA):
            body = reduce.strip(text).body
            assert "  " not in body
            assert " ," not in body
            assert " ." not in body
            assert ",," not in body

    def test_the_body_of_a_ref_prompt_is_the_detailed_description(self):
        stripped = reduce.strip(REF2VA)
        assert "walks between the stalls" in stripped.body
        assert "long dark hair" not in stripped.body
        assert "blue cardigan" not in stripped.body

    def test_the_sound_is_held_aside_rather_than_left_in_the_body(self):
        stripped = reduce.strip(I2VA)
        assert "piano" not in stripped.body
        assert "piano" in stripped.audio
        assert "breeze" in stripped.audio

    def test_an_n_a_sound_field_is_not_carried_as_the_letters(self):
        assert "N/A" not in reduce.strip(REF2VA).audio

    def test_dialogue_is_kept_verbatim(self):
        assert reduce.strip(REF2VA).dialogue == ("How much for these?",)

    def test_a_prompt_with_no_field_names_is_read_whole(self):
        stripped = reduce.strip("A black cat walks along a fence.")
        assert stripped.body == "A black cat walks along a fence."
        assert not stripped.empty

    def test_an_empty_prompt_is_empty_rather_than_an_exception(self):
        assert reduce.strip("").empty
        assert reduce.strip(None).empty


class TestSystemPrompt:
    def test_the_example_matches_the_requested_length(self):
        for detail in reduce.DETAIL_ORDER:
            assert reduce.example_answer(detail, False, False, False) in (
                reduce.system_prompt(detail=detail)
            )

    def test_the_example_moves_with_the_axes_it_would_otherwise_contradict(self):
        assert "low-angle" in reduce.system_prompt(keep_camera=True).rsplit("answer:", 1)[1]
        assert "low-angle" not in reduce.system_prompt(keep_camera=False).rsplit("answer:", 1)[1]

        assert "Live-action" in reduce.system_prompt(keep_style=True).rsplit("answer:", 1)[1]
        assert "Live-action" not in reduce.system_prompt(keep_style=False).rsplit("answer:", 1)[1]

        assert "piano" in reduce.system_prompt(keep_audio=True).rsplit("answer:", 1)[1]
        assert "piano" not in reduce.system_prompt(keep_audio=False).rsplit("answer:", 1)[1]

    def test_the_example_is_shown_a_sound_block_only_when_sound_is_kept(self):
        assert "sound:" in reduce.system_prompt(keep_audio=True)
        assert "sound:" not in reduce.system_prompt(keep_audio=False)

    def test_every_combination_of_the_keeps_produces_an_answer(self):
        seen = set()
        for detail in reduce.DETAIL_ORDER:
            for camera in (False, True):
                for audio in (False, True):
                    for style in (False, True):
                        seen.add(reduce.example_answer(detail, camera, audio, style))
        assert len(seen) == len(reduce.DETAIL_ORDER) * 8

    def test_the_camera_rule_is_one_way_or_the_other_never_both(self):
        kept = reduce.system_prompt(keep_camera=True)
        dropped = reduce.system_prompt(keep_camera=False)
        assert reduce.KEEP_CAMERA in kept and reduce.DROP_CAMERA not in kept
        assert reduce.DROP_CAMERA in dropped and reduce.KEEP_CAMERA not in dropped

    def test_the_style_rule_is_one_way_or_the_other_never_both(self):
        kept = reduce.system_prompt(keep_style=True)
        dropped = reduce.system_prompt(keep_style=False)
        assert reduce.KEEP_STYLE in kept and reduce.DROP_STYLE not in kept
        assert reduce.DROP_STYLE in dropped and reduce.KEEP_STYLE not in dropped

    def test_the_sound_rule_appears_only_when_the_sound_does(self):
        assert reduce.KEEP_AUDIO in reduce.system_prompt(keep_audio=True)
        assert reduce.KEEP_AUDIO not in reduce.system_prompt(keep_audio=False)

    def test_the_dialogue_rule_appears_only_when_there_is_dialogue(self):
        assert reduce.KEEP_DIALOGUE in reduce.system_prompt(dialogue=True)
        assert reduce.KEEP_DIALOGUE not in reduce.system_prompt(dialogue=False)

    def test_an_empty_language_asks_for_the_input_language(self):
        assert "same language" in reduce.system_prompt(language="")

    def test_a_named_language_is_asked_for_by_name(self):
        assert "in Russian" in reduce.system_prompt(language="Russian")
        assert "in Russian" in reduce.system_prompt(language="  Russian  ")

    def test_every_subject_setting_says_something_different(self):
        texts = {reduce.system_prompt(subjects=name) for name in reduce.SUBJECT_ORDER}
        assert len(texts) == len(reduce.SUBJECT_ORDER)

    def test_an_unknown_axis_is_refused_rather_than_silently_defaulted(self):
        with pytest.raises(ValueError):
            reduce.system_prompt(detail="terse")
        with pytest.raises(ValueError):
            reduce.system_prompt(subjects="anonymous")


class TestMessages:
    def test_the_sound_reaches_the_model_only_when_it_is_kept(self):
        with_sound, _ = reduce.build_messages(I2VA, keep_audio=True)
        without, _ = reduce.build_messages(I2VA, keep_audio=False)
        assert "piano" in with_sound[1]["content"]
        assert "piano" not in without[1]["content"]

    def test_dialogue_is_labelled_and_still_fenced(self):
        messages, _ = reduce.build_messages(REF2VA)
        assert "spoken_lines:" in messages[1]["content"]
        assert "<d>How much for these?</d>" in messages[1]["content"]

    def test_a_given_system_prompt_replaces_the_instruction_but_not_the_parsing(self):
        messages, stripped = reduce.build_messages(I2VA, system="Shorten it.")
        assert messages[0]["content"] == "Shorten it."
        assert "[Shot" not in messages[1]["content"]
        assert stripped.shots == 2

    def test_an_empty_prompt_says_what_to_do_about_it(self):
        with pytest.raises(ValueError, match="nothing to shorten"):
            reduce.build_messages("   ")


class TestTranslate:
    def test_the_language_is_named_in_the_instruction(self):
        messages = reduce.translate_messages("A black cat walks.", "Russian")
        assert "into Russian" in messages[0]["content"]

    def test_the_text_arrives_with_no_label_over_it(self):
        messages = reduce.translate_messages("A black cat walks.", "Russian")
        assert messages[1]["content"] == "A black cat walks."
        assert "scene:" not in messages[1]["content"]

    def test_it_carries_no_worked_example(self):
        system = reduce.translate_messages("A black cat walks.", "Russian")[0]["content"]
        assert "answer:" not in system
        assert "black cat" not in system

    def test_no_language_is_a_programming_error_rather_than_a_silent_pass(self):
        with pytest.raises(ValueError):
            reduce.translate_messages("A black cat walks.", "  ")


class TestTidy:
    def test_a_label_the_model_added_comes_off(self):
        assert reduce.tidy("Short prompt: A black cat walks.") == "A black cat walks."
        assert reduce.tidy("**Answer:** A black cat walks.") == "A black cat walks."

    def test_a_fence_comes_off(self):
        assert reduce.tidy("```\nA black cat walks.\n```") == "A black cat walks."
        assert reduce.tidy("```text\nA black cat walks.\n```") == "A black cat walks."

    def test_a_reasoning_block_comes_off(self):
        answer = "<think>the subject is a cat</think>\nA black cat walks."
        assert reduce.tidy(answer) == "A black cat walks."

    def test_wrapping_quotes_come_off_straight_and_curly(self):
        assert reduce.tidy('"A black cat walks."') == "A black cat walks."
        assert reduce.tidy("\u201cA black cat walks.\u201d") == "A black cat walks."
        assert reduce.tidy("\u00abA black cat walks.\u00bb") == "A black cat walks."

    def test_a_quotation_mark_inside_the_sentence_is_left_alone(self):
        assert reduce.tidy('A cat says "hello" and walks.') == 'A cat says "hello" and walks.'

    def test_short_answers_come_back_on_one_line(self):
        assert "\n" not in reduce.tidy("A black cat\nwalks along a fence.", "sentence")

    def test_a_paragraph_answer_keeps_its_shape(self):
        answer = "A black cat walks.\n\nIt stops at the end."
        assert reduce.tidy(answer, "paragraph").count("\n") >= 1


class TestReport:
    def test_it_counts_what_the_parser_took_off(self):
        note = reduce.report(reduce.strip(I2VA), "A black cat walks along a fence.")
        assert "2 shot markers" in note
        assert "words in" in note

    def test_it_names_the_reference_fields_that_were_dropped(self):
        note = reduce.report(reduce.strip(REF2VA), "A woman walks through a market.")
        assert "subject_definitions" in note
        assert "1 spoken lines kept" in note

    def test_a_script_without_spaces_is_counted_in_characters(self):
        chinese = "\u4e00\u53ea\u9ed1\u732b\u6cbf\u7740\u6728\u6805\u680f\u884c\u8d70\u3002"
        assert reduce.measure(chinese).endswith("characters")
        assert reduce.measure("A black cat walks along a fence.") == "7 words"

    def test_with_no_answer_it_reports_the_scene_rather_than_zero_out(self):
        note = reduce.report(reduce.strip(I2VA))
        assert "of scene" in note
        assert " out" not in note
        assert "2 shot markers" in note
