"""Taking apart references that arrived together.

Two of the three inputs this feeds are outside the pack's control -- a socket
that accepts any type at all, and a bundle format belonging to another project
-- so most of what is worth pinning down here is what happens when something
turns up that nobody planned for.

Stand-ins rather than torch tensors: what the sorting reads is a ``shape``, a
``waveform`` key or a video's methods, and building those by hand keeps the
suite free of a heavyweight import for no gain in coverage.
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

references = importlib.import_module(f"{_PKG}.references")


class Batch:
    """An IMAGE: a tensor whose first axis is the frame count."""

    def __init__(self, frames=1, tag="batch"):
        self.shape = (frames, 64, 64, 3)
        self.tag = tag

    def __getitem__(self, window):
        one = Batch(1, self.tag)
        one.window = window
        return one


class Clip:
    """A VIDEO: ComfyUI's VideoInput, of which only the methods are read."""

    def get_components(self):
        return None

    def get_frame_count(self):
        return 30


def sound():
    """An AUDIO: a mapping with a waveform in it."""
    return {"waveform": object(), "sample_rate": 44100}


def counts(sorted_out):
    return {kind: len(values) for kind, values in sorted_out.items()}


def test_a_batch_is_a_picture():
    assert references.kind_of(Batch()) == "image"


def test_a_waveform_is_a_sound():
    assert references.kind_of(sound()) == "audio"


def test_a_video_object_is_a_clip():
    assert references.kind_of(Clip()) == "video"


@pytest.mark.parametrize(
    "value", ["a prompt", b"bytes", 7, 4.5, True, None, {"prompt": "x"}, object()]
)
def test_everything_else_is_not_a_reference(value):
    """The wildcard socket takes anything, so "not a reference" has to be an answer.

    ``universal.kind_of`` may call an unknown value a clip -- everything
    reaching it came off a typed socket. Here a string that fell through as a
    clip would be handed to a captioner and come back as prose about nothing.
    """
    assert references.kind_of(value) == ""


def test_a_widget_arrives_spelled_as_a_list_of_one():
    assert references.first([4.0]) == 4.0
    assert references.first(["7"]) == "7"


def test_an_unwrapped_value_is_left_alone():
    assert references.first(4.0) == 4.0


def test_nothing_at_all_unwraps_to_none():
    assert references.first([]) is None
    assert references.first(None) is None


def test_a_comfy_list_is_already_spread_out():
    first, second = Batch(tag="a"), Batch(tag="b")
    assert references.unpack([first, second]) == [first, second]


def test_a_plain_python_list_arrives_nested():
    """A pack that declares no output list sends the whole list as one value."""
    first, second = Batch(tag="a"), Batch(tag="b")
    assert references.unpack([[first, second]]) == [first, second]


def test_a_single_value_needs_no_unpacking():
    only = Batch()
    assert references.unpack(only) == [only]


def test_only_one_level_is_flattened():
    """A list of lists of lists is nobody's reference, and unwinding it would hide a mistake.

    What is left over is not a reference either, so ``sort_out`` counts it as
    something it could not use rather than passing a list to a captioner.
    """
    only = Batch()
    left = references.unpack([[[only]]])
    assert left == [[only]]
    _sorted, skipped, _over = references.sort_out(left, split_batches=True)
    assert skipped == 1


def test_a_batch_becomes_one_reference_a_frame():
    sorted_out, _skipped, _over = references.sort_out([Batch(6)], split_batches=True)
    assert counts(sorted_out) == {"image": 6, "video": 0, "audio": 0}


def test_each_frame_keeps_its_batch_axis():
    """A three-dimensional slice would have to be special-cased by everything downstream."""
    frames = references.frames_of(Batch(4))
    assert [frame.shape[0] for frame in frames] == [1, 1, 1, 1]


def test_a_batch_can_stay_one_reference():
    sorted_out, _skipped, _over = references.sort_out([Batch(6)], split_batches=False)
    assert counts(sorted_out) == {"image": 1, "video": 0, "audio": 0}


def test_a_single_picture_is_not_split_either_way():
    assert len(references.frames_of(Batch(1))) == 1


def test_something_with_an_unreadable_shape_is_passed_through_whole():
    class Odd:
        shape = ()

    only = Odd()
    assert references.frames_of(only) == [only]


def test_the_ceiling_is_what_ref2va_can_hold():
    assert (references.MAX_PICTURES, references.MAX_VIDEOS, references.MAX_AUDIOS) == (9, 3, 3)


def test_what_fits_goes_out_and_the_rest_is_counted():
    sorted_out, _skipped, over = references.sort_out([Batch(12)], split_batches=True)
    assert len(sorted_out["image"]) == references.MAX_PICTURES
    assert over["image"] == 3


def test_a_kind_running_over_does_not_cost_another_kind():
    sorted_out, _skipped, over = references.sort_out(
        [Batch(12), Clip(), sound()], split_batches=True
    )
    assert counts(sorted_out) == {"image": 9, "video": 1, "audio": 1}
    assert over == {"image": 3, "video": 0, "audio": 0}


def test_arrival_order_is_kept():
    first, second, third = Batch(tag="a"), Batch(tag="b"), Batch(tag="c")
    sorted_out, _skipped, _over = references.sort_out(
        [first, second, third], split_batches=True
    )
    assert [picture.tag for picture in sorted_out["image"]] == ["a", "b", "c"]


def test_what_is_not_a_reference_is_counted_not_dropped_quietly():
    _sorted, skipped, _over = references.sort_out(
        [Batch(), "a prompt", 7, sound()], split_batches=True
    )
    assert skipped == 2


def test_a_bundle_is_read_by_its_four_keys():
    found, unreadable = references.from_bundle({
        "pictures": [Batch(), Batch()],
        "videos": [Clip()],
        "video_audios": [sound()],
        "audios": [sound()],
    })
    assert len(found) == 5
    assert unreadable == 0


def test_a_clips_own_audio_track_counts_as_a_sound():
    found, _unreadable = references.from_bundle({"video_audios": [sound()]})
    sorted_out, _skipped, _over = references.sort_out(found, split_batches=True)
    assert counts(sorted_out) == {"image": 0, "video": 0, "audio": 1}


def test_the_padding_the_bundle_carries_is_not_a_reference():
    """The lists come padded with None to a fixed length."""
    found, unreadable = references.from_bundle({"pictures": [Batch(), None, None]})
    assert len(found) == 1
    assert unreadable == 0


def test_a_missing_key_is_not_a_fault():
    found, unreadable = references.from_bundle({"pictures": [Batch()]})
    assert len(found) == 1 and unreadable == 0


def test_a_key_holding_one_value_rather_than_a_list():
    found, _unreadable = references.from_bundle({"pictures": Batch()})
    assert len(found) == 1


def test_a_bundle_that_is_not_a_mapping_costs_nothing():
    assert references.from_bundle(None) == ([], 0)
    assert references.from_bundle(["not", "a", "bundle"]) == ([], 0)


def test_a_bundle_whose_shape_changed_loses_references_not_the_run():
    """The format belongs to another project and is not ours to guarantee."""
    found, unreadable = references.from_bundle({"pictures": [Batch(), "a caption", 3]})
    assert len(found) == 1
    assert unreadable == 2


def test_a_renamed_key_loses_only_that_key():
    found, unreadable = references.from_bundle({"images": [Batch()], "audios": [sound()]})
    assert len(found) == 1
    assert unreadable == 0


def test_a_clean_run_says_what_came_out_and_warns_about_nothing():
    summary, warning = references.summarise(
        {"image": [1, 2], "video": [], "audio": [3]}, 0, {"image": 0, "video": 0, "audio": 0}, 0
    )
    assert summary == "2 picture(s), 0 clip(s), 1 sound(s)"
    assert warning == ""


def test_going_over_is_said_out_loud():
    summary, warning = references.summarise(
        {"image": [1], "video": [], "audio": []}, 0, {"image": 4, "video": 0, "audio": 0}, 0
    )
    assert "4 picture(s)" in warning
    assert warning in summary


def test_every_trouble_reaches_the_summary():
    summary, warning = references.summarise(
        {"image": [], "video": [], "audio": []}, 2, {"image": 1, "video": 0, "audio": 0}, 3
    )
    for expected in ("1 picture(s)", "2 item(s)", "3 entry/entries"):
        assert expected in summary and expected in warning
