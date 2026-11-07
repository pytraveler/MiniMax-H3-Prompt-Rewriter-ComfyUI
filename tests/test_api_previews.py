"""API reference strips use prepared copies, including RefMods without sidecars."""

import base64
import io
import sys
import types

import numpy as np
import pytest
from PIL import Image

from test_api_text import config, node_environment, run_node, writer
from test_api_writer import fake_server, profiles
from test_api_images import picture
from test_api_multimedia import omni, prepare, refmods


@pytest.fixture(autouse=True)
def preview_state(monkeypatch):
    monkeypatch.setattr(writer.previews, "LAST", {})


def pixels(uri):
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as image:
        return np.asarray(image).copy()


def test_refmod_stack_has_a_bounded_video_preview_before_the_api_call(node_environment, monkeypatch):
    directory, _ = node_environment
    frames = picture(frames=64)
    frames[32:] = (0, 0, 1)
    mod = refmods.RefMod("No-sidecar", "video", object(), source="stack", pictures=frames)
    monkeypatch.setattr(writer.previews, "describe", lambda *_args: pytest.fail("Source media decoded twice"))
    events = []
    monkeypatch.setitem(sys.modules, "server", types.SimpleNamespace(
        PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(send_sync=lambda *args: events.append(args)))))
    complete = writer.write_async

    async def inspect_before_request(*args, **kwargs):
        found = writer.previews.LAST["refmod-preview"]["ref_0"]
        assert found["kind"] == "video"
        assert len(found["frames"]) == writer.previews.FRAMES
        assert pixels(found["thumb"]).shape == (96, 96, 3)
        assert pixels(found["frames"][0])[0, 0, 0] > 200
        assert pixels(found["frames"][-1])[0, 0, 2] > 200
        assert events[-1] == (writer.previews.EVENT, {"node": "refmod-preview", "slots": {"ref_0": found}})
        return await complete(*args, **kwargs)

    monkeypatch.setattr(writer, "write_async", inspect_before_request)
    with fake_server() as api:
        profile = omni(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        output = run_node(profile.id, "Ref2VA", "Describe this reference.", "16:9", 10, 128,
                          system_prompt="Caption.", validation="custom", ref_0=mod, unique_id="refmod-preview")
        assert output[-1]["videos"][0] is mod and len(api.calls) == 1
        assert sum(part["type"] == "image_url" for part in api.calls[0]["body"]["messages"][1]["content"]) == 8


def test_each_preview_keeps_its_slot_kind_and_original_dimensions():
    mod = refmods.RefMod("Stack", "video", object(), source="stack", pictures=picture(frames=6))
    assets, copied = prepare({"ref_0": mod, "ref_1": picture(width=200, height=100)}, max_side=64)
    writer._announce_previews("mixed", assets, copied)
    found = writer.previews.LAST["mixed"]
    assert found["ref_0"]["kind"] == "video"
    assert found["ref_1"]["kind"] == "image"
    assert (found["ref_1"]["width"], found["ref_1"]["height"]) == (200, 100)
    assert "frames" not in found["ref_1"]


def test_corrupt_preview_copy_does_not_fail_or_hide_other_slots():
    assets, copied = prepare({"ref_0": picture(), "ref_1": picture((0, 1, 0))})
    damaged = types.SimpleNamespace(slot="ref_0", jpeg=b"not an image")
    writer._announce_previews("damaged", assets, types.SimpleNamespace(images=(damaged, copied.images[1])))
    found = writer.previews.LAST["damaged"]
    assert found["ref_0"] == {"kind": "image"}
    assert found["ref_1"]["thumb"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("mode", ["descriptions", "bypass"])
def test_text_only_modes_do_not_read_refmod_pixels_for_a_thumbnail(node_environment, monkeypatch, mode):
    directory, _ = node_environment
    mod = refmods.RefMod("Undecoded", "video", object(), appearance="A character.")
    monkeypatch.setattr(writer, "_announce_previews", lambda *_args: pytest.fail("Unrequested pixels read"))
    with fake_server() as api:
        profile = omni(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        run_node(profile.id, "Ref2VA", "A character.", "16:9", 10, 128,
                 system_prompt="Caption.", validation="custom", ref_0=mod, unique_id="text-only",
                 media_mode="descriptions" if mode == "descriptions" else "native", bypass=mode == "bypass")
        assert len(api.calls) == (1 if mode == "descriptions" else 0)
    assert "text-only" not in writer.previews.LAST
