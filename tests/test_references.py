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


def rebuilt(frames, sound):
    """What the adapter's clip factory is to these tests: a clip holding both."""
    clip = Clip()
    clip.frames, clip.sound = frames, sound
    return clip


def test_a_clip_that_arrives_as_frames_stays_a_clip():
    """The loader decodes clips to frames; read by value they are a hundred pictures."""
    found, unreadable = references.from_bundle(
        {"videos": [Batch(154)], "pictures": [Batch()]}, as_clip=rebuilt
    )
    sorted_out, _skipped, over = references.sort_out(found, split_batches=True)
    assert counts(sorted_out) == {"image": 1, "video": 1, "audio": 0}
    assert over == {"image": 0, "video": 0, "audio": 0}
    assert sorted_out["video"][0].frames.shape[0] == 154
    assert unreadable == 0


def test_a_paired_soundtrack_goes_inside_its_clip():
    track, other = sound(), sound()
    found, _unreadable = references.from_bundle(
        {"videos": [Batch(30)], "video_audios": [track], "audios": [other]},
        as_clip=rebuilt,
    )
    sorted_out, _skipped, _over = references.sort_out(found, split_batches=True)
    assert sorted_out["video"][0].sound is track
    assert sorted_out["audio"] == [other]


def test_soundtracks_pair_by_position():
    """The loader pads a clip without a paired track with None, keeping the rest aligned."""
    track = sound()
    found, _unreadable = references.from_bundle(
        {"videos": [Batch(30, "a"), Batch(30, "b")], "video_audios": [None, track]},
        as_clip=rebuilt,
    )
    sorted_out, _skipped, _over = references.sort_out(found, split_batches=True)
    assert [clip.sound for clip in sorted_out["video"]] == [None, track]
    assert sorted_out["audio"] == []


def test_a_real_clip_in_the_bundle_keeps_its_track_as_a_sound():
    clip, track = Clip(), sound()
    found, _unreadable = references.from_bundle(
        {"videos": [clip], "video_audios": [track]}, as_clip=rebuilt
    )
    assert found == [clip, track]


def test_a_clip_that_cannot_be_rebuilt_is_counted_and_its_track_kept():
    def broken(frames, sound):
        raise RuntimeError("no")

    track = sound()
    found, unreadable = references.from_bundle(
        {"videos": [Batch(30)], "video_audios": [track]}, as_clip=broken
    )
    assert found == [track]
    assert unreadable == 1


def test_without_a_clip_factory_frames_are_read_as_before():
    found, _unreadable = references.from_bundle({"videos": [Batch(6)]})
    sorted_out, _skipped, _over = references.sort_out(found, split_batches=False)
    assert counts(sorted_out) == {"image": 1, "video": 0, "audio": 0}


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


def decoder(frames=48, with_sound=True):
    """A stand-in for decoding a clip, recording what it was asked."""
    asked = []

    def clip(value, soundtracks):
        asked.append((value, soundtracks))
        return Batch(frames, tag="decoded"), (sound() if with_sound else None), f"{frames} frames"

    return clip, asked


def test_a_writers_order_is_kept_within_each_kind():
    """The order is the numbering: the second picture here is <Picture 2> on both sides."""
    first, second, voice = Batch(tag="a"), Batch(tag="b"), sound()
    bundle = references.slot_bundle(
        [("ref_3", "Picture", second), ("ref_0", "Audio", voice), ("ref_1", "Picture", first)]
    )
    assert [picture.tag for picture in bundle["pictures"]] == ["b", "a"]
    assert bundle["audios"] == [voice]
    assert bundle["sources"]["pictures"] == ["ref_3", "ref_1"]


def test_a_subject_goes_after_every_picture():
    """The writer gave it no picture number, so it may not take one a picture is using."""
    face, frame = Batch(tag="face"), Batch(tag="frame")
    bundle = references.slot_bundle([("ref_0", "Subject", face), ("ref_1", "Picture", frame)])
    assert [picture.tag for picture in bundle["pictures"]] == ["frame", "face"]
    assert bundle["sources"]["pictures"] == ["ref_1", "ref_0 (subject)"]


def test_a_batch_read_as_a_clip_is_a_video():
    batch = Batch(24)
    bundle = references.slot_bundle([("ref_0", "Video", batch)])
    assert bundle["videos"] == [batch] and bundle["pictures"] == []


def test_a_role_with_no_socket_and_an_empty_value_are_left_out():
    bundle = references.slot_bundle([("ref_0", "Storyboard", Batch()), ("ref_1", "Picture", None)])
    assert (bundle["pictures"], bundle["videos"], bundle["audios"]) == ([], [], [])


def test_every_socket_is_there_when_nothing_is():
    clip, asked = decoder()
    slots, summary, warning = references.fan_out(references.slot_bundle(()), False, clip)
    assert {group: len(values) for group, values in slots.items()} == {
        "pictures": 9, "videos": 3, "video_audios": 3, "audios": 3,
    }
    assert all(value is None for values in slots.values() for value in values)
    assert summary.startswith("0 picture(s), 0 clip(s), 0 sound(s)")
    assert warning == "" and asked == []


def test_pictures_and_sounds_pass_through_as_they_came():
    picture, voice = Batch(), sound()
    clip, _asked = decoder()
    slots, _summary, _warning = references.fan_out(
        references.slot_bundle([("ref_0", "Picture", picture), ("ref_1", "Audio", voice)]),
        False, clip,
    )
    assert slots["pictures"][0] is picture and slots["audios"][0] is voice


def test_a_switched_off_reference_leaves_no_gap():
    """The far node closes up its numbering around an empty socket; there should be none."""
    clip, _asked = decoder()
    slots, _summary, _warning = references.fan_out(
        references.slot_bundle([("ref_0", "Audio", None), ("ref_1", "Audio", sound())]),
        False, clip,
    )
    assert slots["audios"][0] is not None and slots["audios"][1] is None


def test_a_clip_is_decoded_and_a_batch_of_frames_is_not():
    batch = Batch(30, tag="frames")
    clip, asked = decoder()
    slots, _summary, _warning = references.fan_out(
        references.slot_bundle([("ref_0", "Video", Clip()), ("ref_1", "Video", batch)]),
        False, clip,
    )
    assert slots["videos"][0].tag == "decoded"
    assert slots["videos"][1] is batch
    assert len(asked) == 1


def test_a_clips_sound_stays_off_by_default():
    clip, asked = decoder()
    slots, _summary, warning = references.fan_out(
        references.slot_bundle([("ref_0", "Video", Clip())]), False, clip
    )
    assert slots["video_audios"] == [None, None, None]
    assert asked[0][1] is False
    assert warning == ""


def test_a_clips_sound_is_paired_by_number_and_the_shift_is_said():
    """On the far side it takes an <Audio N> of its own, and the writers never counted it."""
    clip, _asked = decoder()
    slots, summary, warning = references.fan_out(
        references.slot_bundle(
            [("ref_0", "Audio", sound()), ("ref_1", "Video", Batch(30)), ("ref_2", "Video", Clip())]
        ),
        True, clip,
    )
    assert slots["video_audios"][0] is None
    assert slots["video_audios"][1] is not None
    assert "1 clip sound(s)" in summary
    assert "1 behind" in warning and warning in summary


def test_the_summary_says_where_each_output_came_from():
    clip, _asked = decoder()
    _slots, summary, _warning = references.fan_out(
        references.slot_bundle(
            [("ref_4", "Picture", Batch()), ("ref_2", "Subject", Batch()), ("ref_1", "Video", Clip())]
        ),
        False, clip,
    )
    assert "picture_1 <- ref_4" in summary
    assert "picture_2 <- ref_2 (subject)" in summary
    assert "video_1 <- ref_1 (48 frames)" in summary


def test_past_the_sockets_is_counted_and_subjects_are_the_first_left_out():
    entries = [(f"ref_{n}", "Picture", Batch(tag=str(n))) for n in range(9)]
    entries.insert(0, ("ref_face", "Subject", Batch(tag="face")))
    clip, _asked = decoder()
    slots, _summary, warning = references.fan_out(references.slot_bundle(entries), False, clip)
    assert [picture.tag for picture in slots["pictures"]] == [str(n) for n in range(9)]
    assert "1 picture(s)" in warning


def test_a_clip_too_short_for_the_generator_is_warned_about():
    clip, _asked = decoder(frames=3)
    _slots, _summary, warning = references.fan_out(
        references.slot_bundle([("ref_0", "Video", Clip())]), False, clip
    )
    assert "video_1" in warning and "5 frames" in warning


@pytest.mark.parametrize("value", [None, "a prompt", ["a", "list"]])
def test_something_that_is_not_a_bundle_hands_on_nothing(value):
    clip, asked = decoder()
    slots, _summary, warning = references.fan_out(value, True, clip)
    assert all(item is None for values in slots.values() for item in values)
    assert warning == "" and asked == []


class Wide:
    """A VIDEO whose frame is as wide as a three-screen strip, past 4:1."""

    def get_dimensions(self):
        return 5760, 1080


class Framed:
    """An IMAGE of a given size, read off its shape."""

    def __init__(self, width, height):
        self.shape = (1, height, width, 3)


def test_sounds_alone_are_said_once():
    notes = references.pipeline_notes("Ref2VA", [("ref_0", "Audio", sound())])
    assert len(notes) == 1
    assert "sounds alone" in notes[0]


def test_a_subject_beside_a_sound_says_nothing():
    entries = [("ref_0", "Subject", Framed(64, 64)), ("ref_1", "Audio", sound())]
    assert references.pipeline_notes("Ref2AV", entries) == []


def test_a_reference_past_four_to_one_is_named_with_its_size():
    entries = [("ref_0", "Picture", Framed(64, 64)), ("ref_1", "Video", Wide())]
    notes = references.pipeline_notes("Ref2VA", entries)
    assert notes == [notes[0]]
    assert "'ref_1' (5760x1080) is outside 1:4 to 4:1" in notes[0]
    assert "Crop it" in notes[0]


def test_two_out_of_shape_are_named_together():
    entries = [("a", "Picture", Framed(100, 500)), ("b", "Subject", Framed(900, 200))]
    notes = references.pipeline_notes("Ref2VA", entries)
    assert "'a' (100x500), 'b' (900x200) are outside" in notes[0]
    assert "Crop them" in notes[0]


def test_the_frame_tasks_are_not_this_rules_business():
    entries = [("first_frame", "Picture", Framed(5000, 1000))]
    assert references.pipeline_notes("I2VA", entries) == []


def test_a_reference_set_inside_the_rules_says_nothing():
    entries = [("ref_0", "Picture", Framed(1920, 1080)), ("ref_1", "Audio", sound())]
    assert references.pipeline_notes("Ref2VA", entries) == []


def test_nothing_moves_when_no_clip_has_its_sound_on():
    entries = [("Audio", False), ("Video", False), ("Picture", False)]
    assert references.turn_order(entries) == [(0, False), (1, False), (2, False)]


def test_a_clips_sound_goes_in_front_of_it():
    """<Audio k>: and then <Video m>:, the order the generator writes them in."""
    entries = [("Picture", False), ("Video", True)]
    assert references.turn_order(entries) == [(0, False), (1, True), (1, False)]


def test_a_standalone_sound_ahead_of_a_sounding_clip_moves_after_it():
    entries = [("Audio", False), ("Video", True), ("Picture", False)]
    assert references.turn_order(entries) == [(1, True), (1, False), (0, False), (2, False)]


def test_sounds_wait_for_the_last_sounding_clip_and_keep_their_order():
    entries = [("Audio", False), ("Video", True), ("Audio", False), ("Video", True), ("Audio", False)]
    assert references.turn_order(entries) == [
        (1, True), (1, False), (3, True), (3, False), (0, False), (2, False), (4, False),
    ]


def test_counted_in_turn_the_clip_sounds_take_the_first_numbers():
    """What the writer numbers, read in turn order, is what the generator numbers."""
    entries = [("Audio", False), ("Video", False), ("Video", True), ("Audio", False), ("Video", True)]
    sounds = []
    for index, is_sound in references.turn_order(entries):
        if is_sound:
            sounds.append(f"clip {index}")
        elif entries[index][0] == "Audio":
            sounds.append(f"sound {index}")
    assert sounds == ["clip 2", "clip 4", "sound 0", "sound 3"]


def test_the_bundle_says_which_clips_sound_was_counted():
    batch = Batch(24)
    bundle = references.slot_bundle(
        [("ref_0", "Video", Clip(), True), ("ref_1", "Video", batch), ("ref_2", "Audio", sound(), True)]
    )
    assert bundle["video_sounds"] == [True, False]


def test_a_counted_clip_sound_goes_on_its_socket_without_a_word():
    clip, asked = decoder()
    slots, summary, warning = references.fan_out(
        references.slot_bundle(
            [("ref_0", "Audio", sound()), ("ref_1", "Video", Clip(), True)]
        ),
        False, clip,
    )
    assert asked[0][1] is True
    assert slots["video_audios"][0] is not None
    assert "1 clip sound(s)" in summary
    assert warning == ""


def test_soundtracks_on_says_only_the_clips_the_writer_left_silent():
    clip, _asked = decoder()
    slots, _summary, warning = references.fan_out(
        references.slot_bundle(
            [("ref_0", "Video", Clip(), True), ("ref_1", "Video", Clip())]
        ),
        True, clip,
    )
    assert slots["video_audios"][0] is not None and slots["video_audios"][1] is not None
    assert "1 clip sound(s) are on video_audio because 'soundtracks' is on" in warning
    assert "1 behind" in warning


def test_a_counted_clip_with_no_sound_track_hands_on_none():
    clip, _asked = decoder(with_sound=False)
    slots, _summary, warning = references.fan_out(
        references.slot_bundle([("ref_0", "Video", Clip(), True)]), False, clip
    )
    assert slots["video_audios"] == [None, None, None]
    assert warning == ""


class Item:
    def __init__(self, slot, sound, value=None):
        self.slot, self.sound, self.value = slot, sound, value


def test_only_the_clips_with_their_sound_on_are_listened_to(monkeypatch):
    asked = []

    def soundtrack(video, workspace, max_seconds=15.0):
        asked.append(video)
        return None if video == "silent" else {"waveform": video, "sample_rate": 32000}

    monkeypatch.setattr(references.media, "clip_soundtrack", soundtrack)
    sounds, note = references.clip_sounds(
        [Item("ref_0", True, "loud"), Item("ref_1", False, "other"), Item("ref_2", True, "silent")]
    )
    assert asked == ["loud", "silent"]
    assert list(sounds) == ["ref_0"]
    assert "'ref_2' has no sound track" in note


def test_no_sound_switched_on_reads_nothing(monkeypatch):
    monkeypatch.setattr(references.media, "clip_soundtrack", lambda *a, **k: 1 / 0)
    assert references.clip_sounds([Item("ref_0", False)]) == ({}, "")
