"""P-H3 picture numbering, RefMod conversion and the native upsampler contract."""

import importlib
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if "minimax_h3_rewriter" not in sys.modules:
    package = types.ModuleType("minimax_h3_rewriter")
    package.__path__ = [str(ROOT / "minimax_h3_rewriter")]
    sys.modules[package.__name__] = package

ph3 = importlib.import_module("minimax_h3_rewriter.ph3_prompt")
refmods = importlib.import_module("minimax_h3_rewriter.refmods")
memory = importlib.import_module("minimax_h3_rewriter.memory")
library = importlib.import_module("minimax_h3_rewriter.library")
torch = pytest.importorskip("torch")


def image(count=1, height=32, width=48):
    return torch.zeros(count, height, width, 3)


def mod(count=5, pictures=True, strength=1):
    return refmods.RefMod(
        "hero", "video", torch.zeros(1, 24, count, 2, 3), source="stack", strength=strength,
        pictures=torch.stack([image()[0] + index / 10 for index in range(count)]) if pictures else None,
    )


def test_saved_order_is_deduplicated_and_disabled_references_are_absent():
    a, b, c = image(), image(), image()
    found = ph3.prepare("Image2Image", {"ref_10": a, "ref_2": b, "ref_0": c},
                        '{"order":["gone","ref_10","ref_10"],"off":["ref_0"]}')
    assert [p.slot for p in found] == ["ref_10", "ref_2"]
    assert found[0].image.data_ptr() == a.data_ptr()


@pytest.mark.parametrize("layout", ["null", "[]", "broken", '{"order":2,"off":false}'])
def test_old_or_invalid_layouts_use_numeric_socket_order(layout):
    assert [name for name, _ in ph3.ordered({"ref_10": 1, "ref_2": 2}, layout)] == ["ref_2", "ref_10"]


def test_image_batches_expand_instead_of_silently_losing_all_but_the_first():
    found = ph3.prepare("Image2Image", {"ref_0": image(2), "ref_1": image()})
    assert [p.slot for p in found] == ["ref_0[1]", "ref_0[2]", "ref_1"]


def test_refmod_uses_evenly_selected_stored_photos_and_keeps_instructions():
    found = ph3.prepare("Image2Image", {"ref_0": mod()}, instructions='{"ref_0":"Use this outfit"}')
    assert [p.slot for p in found] == ["ref_0[1]", "ref_0[3]", "ref_0[5]"]
    assert [round(p.image.mean().item(), 1) for p in found] == [0, .2, .4]
    assert all(p.instruction == "Use this outfit" and "hero" in p.origin for p in found)


def test_expanded_refmod_previews_use_the_original_socket_and_selected_photos():
    held = mod()
    pictures = ph3.prepare("Image2Image", {"ref_1": held, "ref_2": image()})
    found = dict(ph3.preview_items(pictures, {"ref_1": held, "ref_2": image()}))
    assert list(found) == ["ref_1", "ref_2"]
    assert found["ref_1"].shape[0] == 3
    assert [round(frame.mean().item(), 1) for frame in found["ref_1"]] == [0, .2, .4]
    previews = importlib.import_module("minimax_h3_rewriter.previews")
    data = previews.describe(found.items())
    assert data["ref_1"]["kind"] == "image"
    assert data["ref_1"]["thumb"].startswith("data:image/jpeg;base64,")
    assert len(data["ref_1"]["frames"]) == 3


def test_disabled_refmods_can_still_show_available_pictures_without_decoding(monkeypatch):
    held, undecoded = mod(), mod(pictures=False)
    def no_decode(*args):
        raise AssertionError("a disabled RefMod was decoded for its preview")
    monkeypatch.setattr(refmods, "decode_pictures", no_decode)
    supplied = {"ref_0": image(), "ref_1": held, "ref_2": undecoded}
    pictures = ph3.prepare("Image2Image", supplied, '{"off":["ref_1","ref_2"]}')
    found = dict(ph3.preview_items(pictures, supplied))
    assert found["ref_1"] is held.pictures
    assert found["ref_2"] is None


def test_refmod_without_photos_requests_the_original_video_vae():
    with pytest.raises(ValueError, match="original MiniMax-H3 video VAE"):
        ph3.prepare("Image2Image", {"ref_0": mod(pictures=False)})


def test_refmod_decode_is_reused_and_strength_is_applied(monkeypatch):
    held = mod(pictures=False, strength=.5)
    decoded = []
    def decode(value, vae):
        decoded.append(vae)
        value.pictures = image(2)
    monkeypatch.setattr(refmods, "decode_pictures", decode)
    monkeypatch.setattr(refmods, "soften_pictures", lambda pictures, strength, h, w: pictures + strength)
    first = ph3.prepare("Image2Image", {"ref_0": held}, vae="H3")
    second = ph3.prepare("Image2Image", {"ref_0": held})
    assert decoded == ["H3"]
    assert first[0].image.mean().item() == second[0].image.mean().item() == .5


def test_audio_refmods_are_rejected_even_when_they_have_a_saved_caption():
    held = refmods.RefMod("voice", "audio", torch.zeros(1, 32, 2, 40), voice="a voice")
    with pytest.raises(ValueError, match="audio RefMod"):
        ph3.prepare("Image2Image", {"ref_0": held})


def test_refmod_and_regular_images_share_one_limit():
    with pytest.raises(ValueError, match="including the base"):
        ph3.prepare("Image2Image", {"ref_0": image(6), "ref_1": mod()}, refmod_images=3)
    assert len(ph3.prepare("Image2Image", {"ref_0": image(8)})) == 8


@pytest.mark.parametrize("selection", ["", "broken", '{"ref_1":[999]}'])
def test_text_to_image_ignores_connected_references_without_reading_frames(monkeypatch, selection):
    def no_read(*args, **kwargs):
        raise AssertionError("Text2Image tried to read a reference")
    monkeypatch.setattr(ph3.ph3_frames, "source_of", no_read)
    supplied = {"ref_0": image(8), "ref_1": mod(pictures=False),
                "ref_2": types.SimpleNamespace(get_components=no_read)}
    pictures = ph3.prepare("Text2Image", supplied, instructions='{"ref_0":"Use the hat"}',
                           frame_selection=selection)
    assert pictures == []
    assert ph3.canvas("16:9", 4, pictures) == (2720, 1536, "16:9")
    assert ph3.request("A fox in snow", "16:9", pictures, "Keep the face") == (
        "Aspect ratio: 16:9\nRequest: A fox in snow"
    )
    previews = dict(ph3.preview_items(pictures, supplied))
    assert previews["ref_1"] is previews["ref_2"] is None


def test_image_to_image_still_requires_an_enabled_reference():
    for supplied, layout in [(None, ""), ({"ref_0": image()}, '{"off":["ref_0"]}')]:
        with pytest.raises(ValueError, match="at least one"):
            ph3.prepare("Image2Image", supplied, layout)
    assert ph3.prepare("Text2Image", {"ref_0": image()}, '{"off":["ref_0"]}') == []


def test_t2i_description_uses_the_first_active_socket_and_earliest_selected_frame(monkeypatch):
    frames = torch.stack([image()[0] + index / 10 for index in range(10)])
    source = ph3.ph3_frames.source_of(frames)
    reads = []
    original = source.read
    def read(indices):
        reads.append(indices)
        return original(indices)
    monkeypatch.setattr(source, "read", read)
    def first_only(value, *args):
        assert value is frames, "an ignored reference was read"
        return source
    monkeypatch.setattr(ph3.ph3_frames, "source_of", first_only)
    supplied = {"ref_0": mod(pictures=False), "ref_1": frames, "ref_2": object()}
    found = ph3.prepare("Text2Image", supplied, '{"order":["ref_2","ref_1"],"off":["ref_2"]}',
                        instructions='{"ref_1":{"text":"Only the clothing"}}',
                        frame_selection='{"ref_1":[8,2,5],"ref_0":[999]}', use_ref_description=True)
    assert reads == [[2]]
    assert [picture.slot for picture in found] == ["ref_1[3]"]
    assert found[0].instruction == "Only the clothing"
    assert found[0].image.mean().item() == pytest.approx(.2)
    assert supplied["ref_0"].pictures is None


@pytest.mark.parametrize("count", [1, 8, 20])
def test_t2i_description_automatically_uses_one_first_frame(count):
    found = ph3.prepare("Text2Image", {"ref_0": image(count)}, use_ref_description=True)
    assert len(found) == 1
    assert found[0].slot == ("ref_0" if count == 1 else "ref_0[1]")


def test_t2i_description_uses_a_visual_refmods_selected_picture():
    found = ph3.prepare("Text2Image", {"ref_0": mod()}, frame_selection='{"ref_0":[3,4]}',
                        use_ref_description=True)
    assert found[0].slot == "ref_0[4]" and "hero" in found[0].origin
    assert found[0].image.mean().item() == pytest.approx(.3)


def test_t2i_description_has_no_source_when_every_reference_is_off():
    assert ph3.prepare("Text2Image", {"ref_0": mod(pictures=False)}, '{"off":["ref_0"]}',
                       frame_selection="broken", use_ref_description=True) == []


def test_t2i_description_flag_does_not_change_image_to_image_references():
    found = ph3.prepare("Image2Image", {"ref_0": image(2), "ref_1": image()}, use_ref_description=True)
    assert [picture.slot for picture in found] == ["ref_0[1]", "ref_0[2]", "ref_1"]


def test_description_request_is_standalone_and_keeps_user_and_role_instructions():
    picture = ph3.Picture("ref_2[4]", image(), "Take only the clothing")
    caption_request = ph3.description_request(picture)
    assert "what is visibly present" in caption_request
    assert "Take only the clothing" in caption_request
    request = ph3.inspiration_request("Use an autumn forest and a blue scarf", "3:2",
                                      "A red fox with orange fur", picture.instruction)
    assert request.startswith("Aspect ratio: 3:2\nRequest:")
    assert "not an image available to the generator" in request
    assert "user request takes priority" in request
    assert "Visual description: A red fox with orange fur" in request
    assert "User request: Use an autumn forest and a blue scarf" in request
    assert "What to take from the visual description: Take only the clothing" in request
    assert "Keep unchanged:" not in request
    assert not ph3.PICTURE.search(request)
    assert "Create the scene described above" in ph3.inspiration_request("", "1:1", "A fox")


@pytest.mark.parametrize("thinking", [False, True])
def test_reference_generation_uses_vision_only_for_the_caption(monkeypatch, thinking):
    calls, stages = [], []
    picture = ph3.Picture("ref_0[3]", image(), "Only the animal")
    def generate(clip, request, pictures, max_length, thinking):
        calls.append((clip, request, pictures, max_length, thinking))
        if pictures:
            return "A red fox with a blue scarf", "caption reasoning" if thinking else ""
        return "A fox in an autumn forest with a red scarf", "prompt reasoning" if thinking else ""
    monkeypatch.setattr(ph3, "generate", generate)
    text, reasoning, description = ph3.generate_from_reference(
        "clip", "Use an autumn forest and a red scarf", "16:9", picture, 1536, thinking, stages.append,
    )
    assert text == "A fox in an autumn forest with a red scarf"
    assert description == "A red fox with a blue scarf"
    assert len(calls) == 2 and calls[0][2] == [picture] and calls[1][2] == []
    assert all(call[0] == "clip" and call[3:] == (1536, thinking) for call in calls)
    assert "Visual description: A red fox with a blue scarf" in calls[1][1]
    assert "User request: Use an autumn forest and a red scarf" in calls[1][1]
    assert "Only the animal" in calls[0][1] and "Only the animal" in calls[1][1]
    assert not ph3.PICTURE.search(calls[1][1])
    assert len(stages) == 2
    assert ("caption reasoning" in reasoning and "prompt reasoning" in reasoning) if thinking else reasoning == ""


def test_text_to_image_reports_implicit_references_to_unavailable_images():
    text = "A clay figurine modeled after the animal in the reference picture."
    assert any(issue.code == "tags" for issue in ph3.review(text, "Text2Image", []))
    assert ph3.review(text, "Image2Image", ["image"]) == []


def test_request_has_one_label_per_picture_and_no_video_contract():
    pictures = ph3.prepare("Image2Image", {"ref_0": image(), "ref_1": image()},
                           instructions='{"ref_1":{"text":"Use the hat"}}')
    request = ph3.request("Change the hat", "3:2", pictures, "The woman's face")
    assert request.startswith("Aspect ratio: 3:2\n<Picture 1>\n<Picture 2>\nRequest:")
    assert "For <Picture 2>: Use the hat" in request
    assert "Keep unchanged: The woman's face" in request
    assert "duration" not in request and "soundscape" not in request
    assert "Keep unchanged" not in ph3.request("A new scene", "1:1", keep="An old edit's face")


def test_canvas_follows_the_base_or_the_explicit_preset():
    pictures = ph3.prepare("Image2Image", {"ref_0": image(height=60, width=40)})
    width, height, ratio = ph3.canvas("16:9", 4, pictures)
    assert ratio == "2:3" and width < height
    assert width % 32 == height % 32 == 0
    assert ph3.canvas("16:9", 8, pictures, False) == (3872, 2176, "16:9")
    assert ph3.canvas("1:1", 8) == (2912, 2912, "1:1")


@pytest.mark.parametrize("given", ["16:9", "3840x2160", "1.78", "16:9 (Landscape)"])
@pytest.mark.parametrize("megapixels", [4, 8])
def test_aspect_input_overrides_the_picker_and_base_image(given, megapixels):
    pictures = [ph3.Picture("ref_0", image(height=60, width=40))]
    expected = (*ph3.SIZES[megapixels]["16:9"], "16:9")
    assert ph3.canvas("1:1", megapixels, pictures, True, given) == expected
    assert ph3.canvas("1:1", megapixels, aspect_ratio=given) == expected
    assert ph3.request("A fox", expected[2]).startswith("Aspect ratio: 16:9\n")


@pytest.mark.parametrize("given,ratio", [("5:4", "5:4"), ("3840x1080", "32:9"), ("2.5", "2.50:1")])
@pytest.mark.parametrize("megapixels", [4, 8])
def test_custom_aspect_input_keeps_the_pixel_budget_and_alignment(given, ratio, megapixels):
    width, height, found = ph3.canvas("1:1", megapixels, aspect_ratio=given)
    assert found == ratio
    assert width % 32 == height % 32 == 0
    assert width * height == pytest.approx(megapixels * 1024 * 1024, rel=.02)
    a, b = map(float, ratio.split(":"))
    assert width / height == pytest.approx(a / b, rel=.02)


@pytest.mark.parametrize("given", [None, "", "  "])
def test_empty_aspect_input_preserves_the_previous_canvas_behaviour(given):
    pictures = [ph3.Picture("ref_0", image(height=60, width=40))]
    assert ph3.canvas("16:9", 4, pictures, True, given) == ph3.canvas("16:9", 4, pictures)
    assert ph3.canvas("16:9", 4, pictures, False, given) == (2720, 1536, "16:9")


def test_invalid_aspect_input_is_reported_instead_of_using_the_picker():
    with pytest.raises(ValueError, match="not an aspect ratio"):
        ph3.canvas("1:1", 8, aspect_ratio="not a ratio")


def test_library_checks_image_prompts_without_requiring_video_fields():
    assert library.inspect("A photograph of a fox in snow.", "Text2Image") == []
    assert library.inspect("Edit <Picture 1>. The hat is red. Keep the face.", "Image2Image", having=["image"]) == []
    assert library.inspect("subject_definitions: <Picture 1> is the source.\ndetailed_description: [Shot 1] A red hat.", "Image2Image", having=["image"]) == []
    assert any(i["code"] == "tags" for i in library.inspect("Edit <Picture 2>.", "Image2Image", having=["image"]))


def test_editing_a_plain_prompt_keeps_reasoning_and_changes_the_cache_stamp():
    record = memory.keep("ph3-test", "MiniMaxH3PH3Writer", ("old", "reasoning"), {}, plain_text=True)
    before = memory.stamp("ph3-test", True)
    assert record.editable
    memory.rewrite("ph3-test", "new")
    assert record.outputs == ("new", "reasoning")
    assert memory.stamp("ph3-test", True) != before
    memory.forget("ph3-test")


def test_native_generation_uses_default_template_and_separate_vision_copies(monkeypatch):
    calls = []
    class Native:
        @staticmethod
        def execute(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(result=("Edit <Picture 1>. Keep the face.", "thinking"))
    module = types.ModuleType("comfy_extras.nodes_textgen")
    module.TextGenerateV2 = Native
    monkeypatch.setitem(sys.modules, "comfy_extras", types.ModuleType("comfy_extras"))
    monkeypatch.setitem(sys.modules, "comfy_extras.nodes_textgen", module)
    pictures = [ph3.Picture("ref_0", image(height=1200, width=1600)), ph3.Picture("ref_1", image())]
    assert ph3.generate("clip", "request", pictures) == ("Edit <Picture 1>. Keep the face.", "thinking")
    call = calls[0]
    assert call["use_default_template"] and call["thinking"]
    assert call["sampling_mode"] == {"sampling_mode": "off"}
    assert list(call["images"]) == ["image1", "image2"]
    assert call["images"]["image1"].shape[1:3].numel() <= 1_000_000
    assert pictures[0].image.shape[1:3] == (1200, 1600)
    Native.execute = lambda **kw: types.SimpleNamespace(result=("", "budget exhausted"))
    with pytest.raises(RuntimeError, match="max_length"):
        ph3.generate("clip", "request", pictures)


def test_image_text_encoder_is_not_mistaken_for_the_upsampler():
    wrong = types.SimpleNamespace(tokenizer=type("PH3ImageTokenizer", (), {})())
    with pytest.raises(ValueError, match="not a prompt writer"):
        ph3.generate(wrong, "request", [])
