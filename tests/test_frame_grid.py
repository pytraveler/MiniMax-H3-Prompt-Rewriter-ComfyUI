"""Seconds into frames, the way MiniMax-H3 counts them.

H3 generates on a 17n+5 frame grid at 24 fps, so the Omni adapter is told the
length that actually exists rather than the one typed, and quotes it back in
the alignment line. That length has to be the one LightX2V's runner arrives
at -- a whole number of frames first, then the next grid point -- or the line
describes a video other than the one generated.
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

template = importlib.import_module(f"{_PKG}.prompt_template_omni")


def runner(seconds):
    """LightX2V's own arithmetic, restated: ``align_num_frames(round(seconds * 24))``."""
    frames = max(1, round(seconds * 24))
    while frames % 17 != 5:
        frames += 1
    return frames


def test_ten_seconds_is_243_frames():
    assert template.effective_duration(10) == (243, 10.125)


@pytest.mark.parametrize("seconds, frames", [(6.6, 158), (7.3, 175), (15.1, 362)])
def test_a_fraction_of_a_frame_past_the_grid_stays_on_it(seconds, frames):
    """These three went a whole step too far when the fraction was aligned."""
    assert template.effective_duration(seconds)[0] == frames


def test_a_length_on_the_grid_already_stays_where_it_is():
    for frames in range(22, 363, 17):
        assert template.effective_duration(frames / 24)[0] == frames


def test_every_length_the_widget_offers_agrees_with_the_runner():
    for tenths in range(1, 601):
        seconds = tenths / 10
        assert template.effective_duration(seconds)[0] == runner(seconds), seconds


def test_the_longest_video_h3_makes_is_not_overshot():
    assert template.effective_duration(15.0)[0] == 362


def test_the_turn_quotes_the_snapped_length():
    messages = template.build_messages("a fox", "fl2av", "16:9", 6.6, ("image", "image"))
    text = "".join(
        part.get("text", "") for part in messages[1]["content"] if part["type"] == "text"
    )
    assert "exact final frame at 6.58s" in text
    assert "effective_duration: 6.58s" in text
