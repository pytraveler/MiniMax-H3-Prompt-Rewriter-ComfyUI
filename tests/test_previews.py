"""What a strip square is sent about its reference.

The pictures themselves need torch and PIL and are left to a real run. What is
pinned down here is the part that fails quietly: the shape of a sound, which
draws as a flat line when it is computed wrong and looks deliberate, and the
promise that a reference which cannot be read costs its preview and nothing
else.
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

pytest.importorskip("numpy")
previews = importlib.import_module(f"{_PKG}.previews")


class Batch:
    """An IMAGE stand-in: it has a shape, and nothing that can be drawn."""

    shape = (1, 64, 64, 3)


def test_the_loudest_stretch_is_the_full_bar():
    shape = previews.peaks([0.1, -0.2, 0.05, 0.4, -0.1, 0.2, 0.0, 0.1], bars=4)
    assert shape == [0.5, 1.0, 0.5, 0.25]


def test_a_peak_is_not_averaged_away():
    """One loud word in a quiet stretch has to show."""
    quiet = [0.01] * 99 + [0.9]
    loud = [0.9] * 100
    assert previews.peaks(quiet + loud, bars=2) == [1.0, 1.0]


def test_the_sign_of_a_sample_does_not_matter():
    assert previews.peaks([-0.8, 0.4], bars=2) == [1.0, 0.5]


def test_silence_is_flat_rather_than_a_division_by_zero():
    assert previews.peaks([0.0] * 50, bars=5) == [0.0] * 5


def test_nothing_to_hear_is_no_bars():
    assert previews.peaks([], bars=24) == []


def test_fewer_samples_than_bars_still_gives_every_bar():
    assert len(previews.peaks([0.5, 0.25], bars=6)) == 6


def test_a_sound_is_described_by_its_shape_and_length():
    wave = [[0.0, 0.5, -1.0, 0.25] * 11025]  # one channel, one second at 44.1 kHz
    found = previews.describe([("ref_0", {"waveform": [wave], "sample_rate": 44100})])
    assert found["ref_0"]["kind"] == "audio"
    assert found["ref_0"]["seconds"] == 1.0
    assert len(found["ref_0"]["peaks"]) == previews.BARS


def test_what_plays_on_hover_is_the_opening_seconds():
    rate = 100
    sound = [[0.5] * (rate * 10)]
    assert previews.opening(sound, rate, seconds=6).shape == (rate * 6,)


def test_a_short_sound_plays_whole():
    assert previews.opening([[0.5] * 30], 100, seconds=6).shape == (30,)


def test_the_channels_are_mixed_rather_than_one_picked():
    """A voice panned hard to one side would otherwise play as silence."""
    left, right = [0.8, 0.8], [0.0, 0.0]
    assert previews.opening([left, right], 100).tolist() == pytest.approx([0.4, 0.4])


def test_a_sound_that_cannot_be_encoded_still_has_its_wave(monkeypatch):
    def broken(samples, rate):
        raise RuntimeError("no encoder")

    monkeypatch.setattr(previews, "_mp3", broken)
    wave = [[0.0, 0.5, -1.0, 0.25] * 11025]
    found = previews.describe([("ref_0", {"waveform": [wave], "sample_rate": 44100})])
    assert "listen" not in found["ref_0"]
    assert len(found["ref_0"]["peaks"]) == previews.BARS


def test_the_opening_is_a_small_mp3():
    pytest.importorskip("av")
    import numpy

    rate = 44100
    seconds = numpy.arange(rate * 10) / rate
    tone = (0.3 * numpy.sin(2 * numpy.pi * 220 * seconds)).astype(numpy.float32)
    found = previews._mp3(previews.opening(tone, rate), rate)
    assert found.startswith("data:audio/mpeg;base64,")
    assert len(found) < 80_000


def test_a_reference_that_cannot_be_read_keeps_its_kind_and_loses_its_picture():
    found = previews.describe([("ref_0", Batch())])
    assert found == {"ref_0": {"kind": "image"}}


def test_an_empty_slot_is_not_a_reference():
    assert previews.describe([("ref_0", None)]) == {}


def test_announcing_without_a_server_still_keeps_them_for_a_reloaded_page():
    previews.LAST.clear()
    previews.announce("42", [("ref_1", Batch())])
    assert previews.LAST == {"42": {"ref_1": {"kind": "image"}}}


def test_a_node_without_an_id_announces_nothing():
    previews.LAST.clear()
    previews.announce(None, [("ref_1", Batch())])
    assert previews.LAST == {}
