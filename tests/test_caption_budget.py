"""How much of the context a captioner's pictures may take.

A high-resolution clip overflowed an 8k context on the captioning path: mtmd
charges a frame by its resolution, and nothing there shrank one. What is pinned
down here is the arithmetic that now does, and the one call that has to pass it
on to the files.

The package is registered by hand, as in test_server: importing it for real
would run ``__init__.py`` and pull in ComfyUI.
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

mtmd_engine = importlib.import_module(f"{_PKG}.mtmd_engine")
media = mtmd_engine.media

GRID = media.PATCH ** 2


class Batch:
    """An IMAGE stand-in: only its shape is read before the files are written."""

    def __init__(self, frames):
        self.shape = (frames, 1080, 1920, 3)


def test_a_clip_shares_what_the_context_has_left():
    assert mtmd_engine.pixels_each(4000, 8, mtmd_engine.FRAME_MAX_TOKENS) == 500 * GRID


def test_a_frame_never_gets_more_than_the_video_processor_allows():
    assert mtmd_engine.pixels_each(100_000, 8, mtmd_engine.FRAME_MAX_TOKENS) == 768 * GRID


def test_a_picture_alone_may_use_all_the_room():
    assert mtmd_engine.pixels_each(6000, 1) == 6000 * GRID


def test_too_many_frames_stop_at_a_floor_rather_than_at_smudges():
    floor = mtmd_engine.MEDIA_MIN_TOKENS * GRID
    assert mtmd_engine.pixels_each(1000, 64, mtmd_engine.FRAME_MAX_TOKENS) == floor


def test_an_unknown_context_still_holds_frames_to_the_ceiling():
    assert mtmd_engine.pixels_each(None, 8, mtmd_engine.FRAME_MAX_TOKENS) == 768 * GRID


def test_an_unknown_context_leaves_a_picture_as_it_came():
    assert mtmd_engine.pixels_each(None, 1) == 0


def test_the_answer_and_the_text_are_set_aside():
    room = mtmd_engine.media_room(8192, "", "x" * 300, "", max_new_tokens=1024)
    assert room == 8192 - 1024 - 100 - mtmd_engine.TEXT_SLACK


def test_a_context_of_zero_is_the_models_own(monkeypatch):
    monkeypatch.setattr(mtmd_engine.discovery, "gguf_header", lambda path: {"context": 32768})
    room = mtmd_engine.media_room(0, "model.gguf", max_new_tokens=0)
    assert room == 32768 - mtmd_engine.TEXT_SLACK


def test_a_context_nobody_can_read_is_unknown(monkeypatch):
    monkeypatch.setattr(mtmd_engine.discovery, "gguf_header", lambda path: {"context": None})
    assert mtmd_engine.media_room(0, "model.gguf") is None


def test_the_count_is_known_before_anything_is_decoded():
    assert mtmd_engine.expected_attachments(video=object(), max_frames=8) == 8
    assert mtmd_engine.expected_attachments(image=Batch(3), audio={}, max_frames=8) == 4
    assert mtmd_engine.expected_attachments(image=Batch(40), max_frames=8) == 8


def test_eight_full_hd_frames_now_fit_an_8k_context():
    """The report: a large clip, eight frames, a context of 8192."""
    image = pytest.importorskip("PIL.Image")
    room = mtmd_engine.media_room(
        8192, "", "Describe the clip. " * 20, mtmd_engine.DEFAULT_SYSTEM, 1024
    )
    assert media.token_cost(1920, 1080) * 8 > 8192
    pixels = mtmd_engine.pixels_each(room, 8, mtmd_engine.FRAME_MAX_TOKENS)
    frame = media.fit_pixels(image.new("RGB", (1920, 1080)), pixels)
    assert media.token_cost(*frame.size) * 8 <= room


def test_the_clip_is_written_at_its_share(monkeypatch):
    seen = {}

    def frames(video, workspace, max_frames, max_pixels=0):
        seen["max_pixels"] = max_pixels
        return ["frame.png"] * max_frames, 200, 8.0

    monkeypatch.setattr(media, "video_frames", frames)
    mtmd_engine.attachments_from(None, video=object(), max_frames=8, room=4000)
    assert seen["max_pixels"] == 500 * GRID


def test_a_sound_is_paid_for_before_the_pictures_share_the_rest(monkeypatch):
    seen = {}

    def files(image, workspace, max_frames, prefix="frame", max_pixels=0, patch=media.PATCH):
        seen["max_pixels"] = max_pixels
        return ["picture.png"]

    monkeypatch.setattr(media, "image_files", files)
    monkeypatch.setattr(media, "audio_file", lambda audio, workspace: "audio.wav")
    monkeypatch.setattr(media, "wav_seconds", lambda path: 40.0)
    attachments, _notes, _note = mtmd_engine.attachments_from(
        None, image=Batch(1), audio={}, room=3000
    )
    assert seen["max_pixels"] == (3000 - 40 * media.AUDIO_TOKENS_PER_SECOND) * GRID
    assert [kind for kind, _path in attachments] == ["image", "audio"]
