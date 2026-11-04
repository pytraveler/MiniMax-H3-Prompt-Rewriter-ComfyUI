"""P-H3 frame selection must agree with the exact received batch and video trim."""

import gc
import importlib
import json
import pathlib
import sys
import types
import weakref
from fractions import Fraction

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if "minimax_h3_rewriter" not in sys.modules:
    package = types.ModuleType("minimax_h3_rewriter")
    package.__path__ = [str(ROOT / "minimax_h3_rewriter")]
    sys.modules[package.__name__] = package

frames = importlib.import_module("minimax_h3_rewriter.ph3_frames")
ph3 = importlib.import_module("minimax_h3_rewriter.ph3_prompt")
refmods = importlib.import_module("minimax_h3_rewriter.refmods")
torch = pytest.importorskip("torch")


def batch(count=20):
    return torch.stack([torch.full((16, 24, 3), index / 100) for index in range(count)])


def test_large_vhs_batch_is_sampled_and_explicit_indices_select_exact_frames():
    value = batch()
    automatic = ph3.prepare("Image2Image", {"ref_0": value})
    assert [p.slot for p in automatic] == ["ref_0[1]", "ref_0[11]", "ref_0[20]"]
    selected = ph3.prepare("Image2Image", {"ref_0": value}, frame_selection='{"ref_0":[17,3,6,3]}')
    assert [p.slot for p in selected] == ["ref_0[4]", "ref_0[7]", "ref_0[18]"]
    assert [round(p.image.mean().item(), 2) for p in selected] == [.03, .06, .17]
    request = ph3.request("Use the coat", "3:2", selected)
    assert request.count("<Picture") == len(selected)
    assert torch.equal(dict(ph3.preview_items(selected))["ref_0"], value[[3, 6, 17]])


@pytest.mark.parametrize("indices", [[], [False], [1.0], [-1], [20], "1,2", None])
def test_invalid_or_outdated_indices_are_not_clamped_or_ignored(indices):
    with pytest.raises(ValueError, match="selected frames"):
        ph3.prepare("Image2Image", {"ref_0": batch()}, frame_selection=json.dumps({"ref_0": indices}))


@pytest.mark.parametrize("selection", ["broken", "[]", "null"])
def test_broken_selection_json_is_reported(selection):
    with pytest.raises(ValueError, match="JSON object"):
        ph3.prepare("Image2Image", {"ref_0": batch()}, frame_selection=selection)


def test_refmod_manual_selection_and_notes_apply_to_the_same_selected_frames():
    value = refmods.RefMod("coat", "video", torch.zeros(1, 24, 10, 2, 3),
                          pictures=batch(10), source="stack")
    selected = ph3.prepare("Image2Image", {"ref_0": value},
                           frame_selection='{"ref_0":[1,8]}', instructions='{"ref_0":"Only the coat"}')
    assert [p.slot for p in selected] == ["ref_0[2]", "ref_0[9]"]
    assert all(p.instruction == "Only the coat" and "coat" in p.origin for p in selected)


def test_video_components_are_sampled_as_pictures_and_share_the_total_limit():
    class ComponentsVideo:
        def __init__(self):
            self.images = batch(10)
        def get_components(self):
            return types.SimpleNamespace(images=self.images)
    value = ComponentsVideo()
    selected = ph3.prepare("Image2Image", {"ref_0": value}, video_images=2)
    assert [p.slot for p in selected] == ["ref_0[1]", "ref_0[10]"]
    assert all(p.origin == "VIDEO" for p in selected)
    with pytest.raises(ValueError, match="including the base"):
        ph3.prepare("Image2Image", {"ref_0": value, "ref_1": batch(8)})


def test_t2i_description_uses_one_selected_video_frame_and_does_not_read_other_sources():
    class Video:
        def get_components(self):
            return types.SimpleNamespace(images=batch(20))
    value = Video()
    selected = ph3.prepare("Text2Image", {"ref_0": value, "ref_1": object()},
                           frame_selection='{"ref_0":[17,7,12]}', use_ref_description=True)
    assert [picture.slot for picture in selected] == ["ref_0[8]"]
    assert selected[0].origin == "VIDEO"
    assert selected[0].image.mean().item() == pytest.approx(.07)


@pytest.mark.parametrize("indices", [[], [False], [1.0], [-1], [20], "1,2", None])
def test_t2i_description_reports_stale_or_invalid_frame_choices(indices):
    with pytest.raises(ValueError, match="selected frames"):
        ph3.prepare("Text2Image", {"ref_0": batch()},
                    frame_selection=json.dumps({"ref_0": indices}), use_ref_description=True)


def test_cache_does_not_pin_frame_tensors_and_rejects_stale_tokens():
    value = batch()
    source = frames.source_of(value, node="frames-test", slot="ref_0")
    frames.remember("frames-test", "ref_0", source, [0, 10, 19], ["3", 0])
    assert frames.source_of(value, node="frames-test", slot="ref_0") is source
    meta = frames.metadata("frames-test")["ref_0"]
    assert meta["count"] == 20 and meta["link"] == ["3", 0]
    previews = frames.thumbnails("frames-test", "ref_0", meta["token"], [3, 17])
    assert [p["index"] for p in previews] == [3, 17]
    assert previews[0]["thumb"].startswith("data:image/jpeg;base64,")
    replacement = batch(5)
    frames.remember("frames-test", "ref_0", frames.source_of(replacement), [0, 2, 4])
    with pytest.raises(LookupError, match="expired"):
        frames.thumbnails("frames-test", "ref_0", meta["token"], [0])
    ref = weakref.ref(value)
    del value
    gc.collect()
    assert ref() is None
    with pytest.raises(LookupError, match="cache"):
        source.read([0])
    frames.SOURCES.pop(("frames-test", "ref_0"), None)


def test_picker_metadata_is_available_even_when_the_total_picture_limit_is_exceeded():
    values = {"ref_0": batch(8), "ref_1": batch(12)}
    with pytest.raises(ValueError, match="including the base"):
        ph3.prepare("Image2Image", values, node="over-limit")
    assert {name: info["count"] for name, info in frames.metadata("over-limit").items()} == {"ref_0": 8, "ref_1": 12}
    for slot in values:
        frames.SOURCES.pop(("over-limit", slot), None)


def test_file_video_uses_decoded_indices_and_pts_for_variable_rate_trim(tmp_path):
    av = pytest.importorskip("av")
    import numpy as np

    path = tmp_path / "vfr.mkv"
    times = [0, 40, 120, 130, 400]
    with av.open(str(path), "w") as output:
        stream = output.add_stream("ffv1", rate=25)
        stream.width, stream.height = 24, 16
        stream.pix_fmt = "bgr0"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for index, time in enumerate(times):
            picture = av.VideoFrame.from_ndarray(np.full((16, 24, 3), index * 40, dtype=np.uint8), format="rgb24")
            picture.pts, picture.time_base = time, Fraction(1, 1000)
            for packet in stream.encode(picture):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)

    class VideoFromFile:
        def get_components(self):
            raise AssertionError("The whole video was materialized")
        def get_stream_source(self):
            return str(path)
        def get_active_trim_window(self):
            return .05, .15
    value = VideoFromFile()
    source = frames.FrameSource(value)
    assert source.count == 2
    assert source.times == pytest.approx([.07, .08])
    assert [round(p.mean().item() * 255) for p in source.read([1, 0])] == [120, 80]
    selected = ph3.prepare("Image2Image", {"ref_0": value}, frame_selection='{"ref_0":[1]}')
    assert selected[0].slot == "ref_0[2]"
    assert round(selected[0].image.mean().item() * 255) == 120
