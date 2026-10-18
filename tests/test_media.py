"""A reference clip as MiniMaxH3ReferenceToVideo reads it: which frames, at what size.

Decoding itself needs PyAV and a file, and is left to a real run. What is worth
pinning down here is the arithmetic around it, because both halves of it fail
silently: a clip resampled wrong still plays, only faster or slower than the
prompt says, and a canvas that disagrees with the generator's own is resized a
second time without anyone being told.
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

media = importlib.import_module(f"{_PKG}.media")


def clip(rate, seconds=1.0, start=0.0):
    """``(time, frame)`` pairs for a clip, the frame being its own index."""
    count = int(round(rate * seconds))
    return [(start + index / rate, index) for index in range(count)]


def test_a_clip_already_at_24_comes_through_frame_for_frame():
    assert list(media.at_rate(clip(24), 24, 1000)) == list(range(24))


def test_a_slower_clip_shows_each_frame_for_as_long_as_it_lasted():
    shown = list(media.at_rate(clip(12), 24, 1000))
    assert shown == [index // 2 for index in range(24)]


def test_a_faster_clip_keeps_its_length_and_loses_frames():
    shown = list(media.at_rate(clip(30), 24, 1000))
    assert len(shown) == 24
    assert shown[0] == 0 and shown[-1] == 28
    assert shown == sorted(shown)


def test_time_is_counted_from_the_first_frame():
    """A trimmed clip starts at its trim, not at zero."""
    assert list(media.at_rate(clip(24, start=5.0), 24, 1000)) == list(range(24))


def test_nothing_past_the_limit_is_taken_from_the_decoder():
    pulled = []

    def frames():
        for pair in clip(24, seconds=10):
            pulled.append(pair[1])
            yield pair

    shown = list(media.at_rate(frames(), 24, 48))
    assert len(shown) == 48
    assert len(pulled) <= 50


def test_no_frames_is_no_frames():
    assert list(media.at_rate([], 24, 100)) == []


def test_a_single_frame_is_held_for_one_tick():
    assert list(media.at_rate([(0.0, "only")], 24, 100)) == ["only"]


@pytest.mark.parametrize(
    "size, canvas",
    [
        ((1920, 1080), (1344, 768)),
        ((3840, 2160), (1344, 768)),
        ((1080, 1920), (768, 1344)),
        ((1024, 1024), (768, 768)),
    ],
)
def test_a_large_clip_gets_the_generators_own_canvas(size, canvas):
    assert media.reference_canvas(*size) == canvas


def test_a_clip_on_the_canvas_already_is_left_there():
    """Scaling to what the generator picks has to be a fixed point, or it resizes twice."""
    once = media.reference_canvas(1920, 1080)
    assert media.reference_canvas(*once) == once


def test_a_small_clip_is_not_scaled_up():
    assert media.reference_canvas(640, 360) == (640, 360)


def test_a_shape_whose_canvas_moves_again_is_left_to_the_generator():
    """286x3713 gets 288x3648, which the generator would take to 288x3616 -- a second resize."""
    assert media.reference_canvas(286, 3713) == (286, 3713)


def generator_size(width, height):
    """What MiniMaxH3ReferenceToVideo resizes a clip of this size to."""
    canvas = media._canvas(width, height)
    if width * height < canvas[0] * canvas[1]:
        grid = media.CANVAS_MULTIPLE
        return (max(grid, round(width / grid) * grid), max(grid, round(height / grid) * grid))
    return canvas


@pytest.mark.parametrize(
    "size",
    [(1920, 1080), (1280, 720), (720, 1280), (2560, 1080), (4096, 512), (286, 3713), (640, 360)],
)
def test_the_generator_ends_on_the_same_size_as_from_the_original(size):
    """Scaling here is only a saving: what the generator finally encodes must not change."""
    assert generator_size(*media.reference_canvas(*size)) == generator_size(*size)


def test_an_untrimmed_video_has_no_window():
    assert media.trim_window(object()) == (0.0, 0.0)


def test_a_trimmed_video_says_where_it_starts_and_how_long_it_runs():
    class Trimmed:
        def get_active_trim_window(self):
            return 2.5, 4

    assert media.trim_window(Trimmed()) == (2.5, 4.0)


def test_a_trim_that_cannot_be_read_is_no_trim():
    class Broken:
        def get_active_trim_window(self):
            raise RuntimeError("probe failed")

    assert media.trim_window(Broken()) == (0.0, 0.0)


class Sized:
    """A VIDEO that answers ``get_dimensions`` as ComfyUI's does: width, then height."""

    def __init__(self, width, height):
        self.size = (width, height)

    def get_dimensions(self):
        return self.size


class Unreadable:
    def get_dimensions(self):
        raise OSError("no such file")


class Shaped:
    def __init__(self, *shape):
        self.shape = shape


def test_an_image_batch_is_measured_off_its_shape():
    assert media.frame_size(Shaped(3, 1080, 1920, 3)) == (1920, 1080)


def test_a_video_is_asked_for_its_dimensions():
    assert media.frame_size(Sized(1280, 720)) == (1280, 720)


def test_something_that_cannot_say_its_size_is_none():
    assert media.frame_size(Unreadable()) is None
    assert media.frame_size(Shaped(16)) is None
    assert media.frame_size({"waveform": None}) is None
    assert media.frame_size(None) is None


def test_an_untouched_clip_is_read_whole():
    assert media.clip_window(480, 24) == (0, 480)


def test_a_clip_stops_at_the_length_of_the_video():
    """Ten seconds asked for is 243 frames made; frames past that are never shown to H3."""
    assert media.clip_window(480, 24, max_seconds=243 / 24) == (0, 243)


def test_a_trimmed_clip_is_read_from_its_trim():
    assert media.clip_window(480, 24, start=5.0) == (120, 360)
    assert media.clip_window(480, 24, start=5.0, trimmed=4.0) == (120, 96)


def test_the_tighter_of_the_trim_and_the_video_wins():
    assert media.clip_window(480, 24, start=5.0, trimmed=12.0, max_seconds=243 / 24) == (120, 243)
    assert media.clip_window(480, 24, start=5.0, trimmed=4.0, max_seconds=243 / 24) == (120, 96)


def test_a_clip_shorter_than_the_video_is_read_to_its_end():
    assert media.clip_window(100, 24, max_seconds=15.0) == (0, 100)


def test_the_window_is_counted_at_the_clips_own_rate():
    assert media.clip_window(900, 30, max_seconds=243 / 24) == (0, 304)


def test_a_trim_past_the_end_still_leaves_a_frame():
    assert media.clip_window(100, 24, start=99.0) == (99, 1)


def test_a_clip_that_does_not_say_its_length_is_read_as_before():
    assert media.clip_window(0, 24, max_seconds=5.0) == (0, 0)
    assert media.clip_window(100, 0, max_seconds=5.0) == (0, 100)
