"""LoRA trigger words: what gets read, what gets found, and what gets written.

The package is registered by hand rather than imported: running its
``__init__.py`` would pull in nodes.py and with it ComfyUI, which a test run
does not have.

Two things here are worth more than the rest. The first is that a second run
adds nothing -- the node sits at the tail of a graph that gets run again and
again, and a word that stacked would be in the prompt four times by teatime.
The second is that nothing is ever taken out. That is not a convenience, it is
the difference between this node and the effect embeddings one: those tokens
are ten known strings and can be cut back out safely, these are somebody's own
words and a trigger spelled ``detail`` would cut a hole in the description.
"""

import importlib
import json
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

triggers = importlib.import_module(f"{_PKG}.triggers")
embeddings = importlib.import_module(f"{_PKG}.embeddings")
fields = importlib.import_module(f"{_PKG}.fields")

PROMPT = (
    "integrated_multimodal_description: A cat walks along a fence at dusk.\n\n"
    "overall_soundscape: Wind in the grass.\n\n"
    "non_diegetic_music: N/A"
)

REF_PROMPT = (
    "subject_definitions: <Subject 1> is a young woman.\n\n"
    "summary: She turns to the camera.\n\n"
    "retention_analysis: <Subject 1> keeps her coat.\n\n"
    "detailed_description: She walks into the room and stops.\n\n"
    "overall_soundscape: A door closes.\n\n"
    "non_diegetic_music: Low strings."
)

PLAIN = "A cat walks along a fence at dusk."


def row(words, where=triggers.BODY, on=True, name=""):
    return triggers.Trigger(name=name, words=words, where=where, on=on)


def messages(findings):
    return [message for _level, message in findings]


def test_the_widgets_own_shape_is_read():
    value = json.dumps([
        {"name": "Facial realism", "words": "ohwx face", "where": triggers.END, "on": True},
        {"name": "", "words": "neon", "where": triggers.TOP, "on": False},
    ])
    first, second = triggers.entries(value)
    assert (first.name, first.words, first.where, first.on) == (
        "Facial realism", "ohwx face", triggers.END, True,
    )
    assert second.on is False


def test_a_plain_array_of_strings_is_read_as_words():
    """What somebody driving the API by hand writes first."""
    first, second = triggers.entries('["ohwx man", "neon city"]')
    assert (first.words, second.words) == ("ohwx man", "neon city")
    assert first.where == triggers.BODY and first.on is True


def test_text_that_is_not_json_at_all_is_one_rows_words():
    only, = triggers.entries("ohwx man, neon city")
    assert only.words == "ohwx man, neon city"
    assert only.pieces == ("ohwx man", "neon city")


def test_an_empty_value_is_an_empty_list():
    assert triggers.entries("") == ()
    assert triggers.entries("[]") == ()
    assert triggers.entries(None) == ()


def test_a_row_with_no_words_is_dropped():
    """The list is allowed to hold a half-typed row. A run is not the moment."""
    assert triggers.entries('[{"name": "typing", "words": "   "}]') == ()


def test_on_is_assumed_when_it_is_not_written():
    only, = triggers.entries('[{"words": "ohwx man"}]')
    assert only.on is True


def test_an_unknown_placement_falls_back_rather_than_failing():
    only, = triggers.entries('[{"words": "ohwx man", "where": "somewhere else"}]')
    assert only.where == triggers.BODY


def test_the_placements_that_overlap_the_effects_node_are_worded_identically():
    """Two nodes naming the same place had better name it the same way."""
    for placement in (embeddings.BODY, embeddings.TOP, embeddings.END):
        assert placement in triggers.PLACEMENTS


def test_a_trigger_inside_a_longer_word_is_not_found():
    assert not triggers.present("She is a young woman.", "man")


def test_case_is_ignored():
    assert triggers.present("Ohwx man walks in.", "ohwx man")


def test_the_whitespace_inside_a_phrase_is_forgiven():
    assert triggers.present("ohwx\n  man walks in.", "ohwx man")


def test_a_trigger_that_opens_on_punctuation_is_still_findable():
    """Tag-trained triggers do this, and an unfindable one is added every run."""
    assert triggers.present("the style is <lora> here", "<lora>")


def test_the_description_is_opened_after_its_label():
    text, added, _ = triggers.insert(PROMPT, [row("ohwx man")])
    assert added == ("ohwx man",)
    assert text.startswith("integrated_multimodal_description: ohwx man. A cat walks")


def test_ref2va_opens_the_detailed_description_rather_than_the_first_field():
    text, _added, _ = triggers.insert(REF_PROMPT, [row("ohwx man")])
    assert "detailed_description: ohwx man. She walks into the room" in text
    assert "subject_definitions: <Subject 1> is a young woman." in text


def test_every_other_field_is_passed_through_character_for_character():
    text, _added, _ = triggers.insert(PROMPT, [row("ohwx man")])
    assert text.replace("ohwx man. ", "", 1) == PROMPT


def test_the_soundscape_and_the_music_have_their_own_placements():
    text, _added, _ = triggers.insert(
        PROMPT, [row("a hum", triggers.SOUND), row("a drone", triggers.MUSIC)]
    )
    assert "overall_soundscape: a hum. Wind in the grass." in text
    assert "non_diegetic_music: a drone. N/A" in text


def test_the_top_and_the_end_are_positions_rather_than_fields():
    top, _added, _ = triggers.insert(PROMPT, [row("ohwx man", triggers.TOP)])
    assert top.startswith("ohwx man\n\nintegrated_multimodal_description:")

    end, _added, _ = triggers.insert(PROMPT, [row("ohwx man", triggers.END)])
    assert end.endswith("non_diegetic_music: N/A\n\nohwx man")


def test_several_sections_at_once_all_land_where_they_were_asked_to():
    """Every offset is taken against the incoming text, so they must not shift."""
    text, added, _ = triggers.insert(
        PROMPT,
        [
            row("ohwx man", triggers.BODY),
            row("a hum", triggers.SOUND),
            row("a drone", triggers.MUSIC),
            row("neon", triggers.TOP),
        ],
    )
    assert added == ("ohwx man", "a hum", "a drone", "neon")
    assert text.startswith("neon\n\nintegrated_multimodal_description: ohwx man. A cat")
    assert "overall_soundscape: a hum. Wind in the grass." in text
    assert "non_diegetic_music: a drone. N/A" in text


def test_two_rows_wanting_the_same_section_are_one_insertion():
    text, _added, _ = triggers.insert(PROMPT, [row("ohwx man"), row("neon")])
    assert "integrated_multimodal_description: ohwx man, neon. A cat walks" in text


def test_a_row_holding_several_triggers_splits_on_the_commas():
    text, added, _ = triggers.insert(PROMPT, [row("ohwx man, neon city")])
    assert added == ("ohwx man", "neon city")
    assert "integrated_multimodal_description: ohwx man, neon city. A cat walks" in text


def test_the_same_word_asked_for_twice_goes_in_once():
    text, added, _ = triggers.insert(PROMPT, [row("ohwx man"), row("OHWX MAN")])
    assert added == ("ohwx man",)
    assert text.count("ohwx man") == 1


def test_a_field_that_says_nothing_is_pointed_out_rather_than_rewritten():
    """"non_diegetic_music: N/A" is everywhere, and the note is the honest answer.

    Replacing the marker would have the field claim there *is* music called
    "ohwx face", which the self-check then reads; dropping the words would be
    the node overruling a placement asked for on purpose.
    """
    text, added, findings = triggers.insert(PROMPT, [row("ohwx face", triggers.MUSIC)])
    assert "non_diegetic_music: ohwx face. N/A" in text
    assert added == ("ohwx face",)
    assert any("N/A" in message for message in messages(findings))


@pytest.mark.parametrize("said", ["N/A", "n/a", "none", "-", "None.", "nil"])
def test_the_usual_ways_of_writing_nothing_are_all_recognised(said):
    prompt = PROMPT.replace("non_diegetic_music: N/A", f"non_diegetic_music: {said}")
    _text, _added, findings = triggers.insert(prompt, [row("ohwx face", triggers.MUSIC)])
    assert any("none of it" in message for message in messages(findings))


def test_a_field_with_something_in_it_is_not_pointed_out():
    _text, _added, findings = triggers.insert(PROMPT, [row("a hum", triggers.SOUND)])
    assert not any("none of it" in message for message in messages(findings))


def test_a_placement_this_prompt_has_no_field_for_falls_back_and_says_so():
    text, added, findings = triggers.insert(PLAIN, [row("ohwx man", triggers.SOUND)])
    assert added == ("ohwx man",)
    assert text == "ohwx man\n\n" + PLAIN
    assert any(triggers.TOP in message for message in messages(findings))


def test_running_it_again_adds_nothing():
    once, _added, _ = triggers.insert(PROMPT, [row("ohwx man")])
    twice, added, findings = triggers.insert(once, [row("ohwx man")])
    assert twice == once
    assert added == ()
    assert any("already in the prompt" in message for message in messages(findings))


def test_a_word_the_writer_wrote_is_not_added_a_second_time():
    text, added, _ = triggers.insert(PROMPT, [row("a cat")])
    assert added == ()
    assert text == PROMPT


def test_switching_a_row_off_stops_it_being_added():
    text, added, _ = triggers.insert(PROMPT, [row("ohwx man", on=False)])
    assert added == ()
    assert text == PROMPT


def test_switching_a_row_off_does_not_take_the_word_out_of_the_prompt():
    """It may have come from the writer, and removing it is not this node's call."""
    written = PROMPT.replace("A cat walks", "ohwx man walks")
    text, added, _ = triggers.insert(written, [row("ohwx man", on=False)])
    assert text == written
    assert added == ()


def test_nothing_switched_on_is_the_prompt_untouched():
    text, added, findings = triggers.insert(PROMPT, [])
    assert (text, added, findings) == (PROMPT, (), [])


def test_an_empty_prompt_is_said_out_loud_rather_than_quietly_filled():
    text, added, findings = triggers.insert("", [row("ohwx man")])
    assert text == "ohwx man"
    assert added == ("ohwx man",)
    assert any(level == "warn" for level, _message in findings)


def test_field_offset_finds_a_named_field():
    offset = fields.field_offset(PROMPT, "overall_soundscape")
    assert PROMPT[offset:].startswith("Wind in the grass.")


def test_field_offset_is_none_for_a_field_this_prompt_has_not_got():
    assert fields.field_offset(PLAIN, "overall_soundscape") is None


def test_field_offset_takes_the_first_of_several_names_that_is_present():
    offset = fields.field_offset(REF_PROMPT, ("detailed_description", "summary"))
    assert REF_PROMPT[offset:].startswith("She walks into the room")


def test_body_offset_still_answers_what_it_always_did():
    assert fields.body_offset(PROMPT) == fields.field_offset(
        PROMPT, "integrated_multimodal_description"
    )
    assert fields.body_offset(REF_PROMPT) == fields.field_offset(
        REF_PROMPT, "detailed_description"
    )


@pytest.mark.parametrize("placement", triggers.PLACEMENTS)
def test_every_placement_works_on_a_prompt_with_the_full_field_set(placement):
    text, added, _ = triggers.insert(REF_PROMPT, [row("ohwx man", placement)])
    assert added == ("ohwx man",)
    assert "ohwx man" in text
    assert triggers.insert(text, [row("ohwx man", placement)])[1] == ()
