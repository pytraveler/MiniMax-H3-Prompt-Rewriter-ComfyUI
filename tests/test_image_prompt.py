"""Context IR payloads and image order at the model boundary."""

import asyncio
import base64
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types

import numpy as np
from PIL import Image
import pytest

PKG = "minimax_h3_rewriter"
if PKG not in sys.modules:
    package = types.ModuleType(PKG)
    package.__path__ = [str(Path(__file__).resolve().parents[1] / PKG)]
    sys.modules[PKG] = package

ir = importlib.import_module(f"{PKG}.image_context_ir")
image = importlib.import_module(f"{PKG}.image_prompt")
profiles = importlib.import_module(f"{PKG}.api_profiles")
writer = importlib.import_module(f"{PKG}.image_api_writer")
client = importlib.import_module(f"{PKG}.api_client")
options = importlib.import_module(f"{PKG}.api_options")


def test_text_to_image_sends_only_prompt_and_model(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-context-ir-key")

    async def request(url, data, token, timeout, deadline, **_):
        assert url == "https://api.minimax.io/v1/h3_image_context_ir"
        assert token == "test-context-ir-key"
        assert deadline >= 600
        assert json.loads(data) == {"model": "MiniMax-H3-Image", "prompt": "Красная чашка"}
        return b'{"revised_prompt":"A red mug.","aspect_ratio":"3:2","usage":{"input_tokens":17}}', None

    monkeypatch.setattr(ir, "_request", request)
    answer, ratio, report = asyncio.run(ir.expand("Красная чашка", (), "auto", None, ""))
    assert answer == "A red mug."
    assert image.canvas(ratio, None, "2K") == (2496, 1664)
    assert report == ["Input tokens billed: 17"]


def test_edit_jpeg_order_matches_enabled_picture_labels():
    red = np.zeros((2, 160, 224, 3), dtype=np.float32)
    red[0, ..., 0] = 1
    blue = np.zeros((1, 128, 192, 3), dtype=np.float32)
    blue[..., 2] = 1
    selected = image.pictures({"ref_0": red, "ref_1": red, "ref_15": blue},
                              '{"order":["ref_15"],"off":["ref_1"]}',
                              '{"ref_15":{"text":"Use the blue color."}}')
    request = image.instruction("Recolor <Picture 1>.", selected)
    payload = json.loads(asyncio.run(ir._payload(request, selected, "16:9", None, ir.MAX_BODY, None)))
    assert payload["prompt"].endswith("For <Picture 1>: Use the blue color.")
    assert payload["aspect_ratio"] == "16:9"
    assert "size" not in payload
    decoded = [Image.open(io.BytesIO(base64.b64decode(one["image_url"].split(",", 1)[1])))
               for one in payload["images"]]
    assert [one.size for one in decoded] == [(192, 128), (224, 160)]
    assert decoded[0].getpixel((0, 0))[2] > 240
    assert decoded[1].getpixel((0, 0))[0] > 240
    assert selected[1].value.shape == (1, 160, 224, 3)


def test_exact_size_and_api_ratio_are_mutually_exclusive():
    ratio, size = image.canvas_selection("auto", "2720x1536", "2K")
    pictures = image.pictures({"ref_0": np.zeros((1, 128, 128, 3), dtype=np.float32)}, "", "")
    payload = json.loads(asyncio.run(ir._payload("Edit.", pictures, ratio, size, ir.MAX_BODY, None)))
    assert payload["size"] == "2720x1536"
    assert "aspect_ratio" not in payload and "area" not in payload
    assert ir._read(b'{"revised_prompt":"Edit the picture."}', ratio, "key-for-test")[1] == ratio
    with pytest.raises(image.ContractError, match="aspect_ratio to auto"):
        image.canvas_selection("16:9", "2720x1536", "2K")


def test_profile_recipient_is_validated_before_key_lookup(monkeypatch):
    profile = types.SimpleNamespace(network="cloud", base_url="https://example.com/v1",
                                    allowed_models=("MiniMax-H3-Image",), secret=profiles.SecretRef("vault", "minimax"))
    monkeypatch.setattr(ir.api_config, "load_profiles", lambda: types.SimpleNamespace(resolve=lambda _: profile))

    def no_secret():
        pytest.fail("The credential resolver should not run for an unrelated provider.")

    monkeypatch.setattr(ir, "SecretResolver", no_secret)
    with pytest.raises(image.ContractError, match="MiniMax HTTPS profile"):
        ir._connection("other")


def test_context_ir_does_not_return_echoed_credential():
    with pytest.raises(ir.ApiError) as error:
        ir._read(b'{"revised_prompt":"Echo: fake-key","aspect_ratio":"3:2"}', "auto", "fake-key")
    assert error.value.code == "credential_echo"
    assert "fake-key" not in str(error.value)


@pytest.fixture
def chat_profile(monkeypatch):
    vision = profiles.ModelSettings(
        "gemma-vision", profiles.ModelCapabilities(modalities={"text", "image"}, formats={"text", "json_schema"}),
        profiles.ProfileLimits(references=16),
    )
    profile = profiles.ApiProfile(
        "llama-local", "Local LLM", "http://127.0.0.1:8081/v1", "default-text", "loopback",
        stream=False, models=(vision, profiles.ModelSettings("ornith-text", limits=profiles.ProfileLimits(output_tokens=1024))),
    )
    monkeypatch.setattr(writer.api_config, "load_profiles", lambda: profiles.ProfileSet((profile,)))
    return profile


def chat_answer(caption, ratio_name, ratio):
    return {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": json.dumps({"expanded_caption": caption, ratio_name: ratio}),
        "reasoning_content": "Separate reasoning.",
    }}]}


def test_api_writer_uses_selected_text_model_with_image_prompt_instructions(monkeypatch, chat_profile):
    async def exchange(profile, data, token, timeout, deadline):
        payload = json.loads(data)
        assert profile.completions_url == chat_profile.completions_url
        assert token is None and payload["model"] == "ornith-text"
        assert payload["max_tokens"] == 1024 and "temperature" not in payload
        assert "response_format" not in payload
        assert payload["messages"][0] == {"role": "system", "content": image.SYSTEM_PROMPT}
        assert "Красная чашка" in payload["messages"][1]["content"]
        assert "aspect_ratio" in payload["messages"][1]["content"]
        return chat_answer("A red mug.", "aspect_ratio", "3:2")

    monkeypatch.setattr(client, "_exchange", exchange)
    result = asyncio.run(writer.expand("Красная чашка", (), "auto", chat_profile.id, "ornith-text", None, 1024))
    assert result == ("A red mug.", "Separate reasoning.", "3:2", ["API profile: llama-local | model: ornith-text"])


def test_api_writer_sends_ordered_edit_images_with_api_options(monkeypatch, chat_profile):
    red = np.zeros((1, 160, 224, 3), dtype=np.float32)
    blue = red.copy()
    red[..., 0] = 1
    blue[..., 2] = 1
    pictures = image.pictures({"ref_0": red, "ref_1": red, "ref_15": blue},
                              '{"order":["ref_15"],"off":["ref_1"]}', '{"ref_15":"Preserve the shape."}')
    request = image.instruction("Add the red from <Picture 2> to <Picture 1>.", pictures)
    settings = options.ApiWriterOptions(max_tokens=512, temperature=0.5, response_format="json_schema", timeout_seconds=12)

    async def exchange(profile, data, token, timeout, deadline):
        payload = json.loads(data)
        assert payload["model"] == "gemma-vision"
        assert deadline == 12 and timeout == (5.0, 12)
        assert payload["max_tokens"] == 512 and payload["temperature"] == 0.5
        assert payload["response_format"]["json_schema"]["schema"]["required"] == ["expanded_caption", "target_aspect_ratio"]
        parts = payload["messages"][1]["content"]
        assert "For <Picture 1>: Preserve the shape." in parts[-2]["text"]
        labels, colors = [], []
        for index, part in enumerate(parts):
            if part["type"] == "image_url":
                labels.append(parts[index - 1]["text"].split(" has ")[0])
                data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                copy = Image.open(io.BytesIO(data))
                assert max(copy.size) == 64
                colors.append(copy.getpixel((0, 0)))
        assert labels == ["<Picture 1>", "<Picture 2>"]
        assert colors[0][2] > 240 and colors[1][0] > 240
        return chat_answer("A recolored object from <Picture 1>.", "target_aspect_ratio", "3:2")

    monkeypatch.setattr(client, "_exchange", exchange)
    result = asyncio.run(writer.expand(request, pictures, "16:9", chat_profile.id, "gemma-vision", settings, 64))
    assert result[:3] == ("A recolored object from <Picture 1>.", "Separate reasoning.", "16:9")


def test_api_writer_checks_selected_model_vision_before_image_copies(monkeypatch, chat_profile):
    def no_images(*_, **__):
        pytest.fail("Image copies must not be prepared for a text-only model.")

    monkeypatch.setattr(writer, "prepare_images_async", no_images)
    pictures = image.pictures({"ref_0": np.zeros((1, 128, 128, 3), dtype=np.float32)}, "", "")
    with pytest.raises(image.ContractError, match="Image Edit requires image input"):
        asyncio.run(writer.expand("Edit.", pictures, "auto", chat_profile.id, "ornith-text", None, 1024))


@pytest.mark.parametrize(("backend", "socket"), [
    (image.LOCAL, "upsampler"),
    (image.API_WRITER, "options"),
    (image.LOCAL_GGUF, "local_options"),
])
def test_image_writer_requests_only_pending_links(monkeypatch, backend, socket):
    latest = types.ModuleType("comfy_api.latest")
    latest.io = types.SimpleNamespace(ComfyNode=object)
    monkeypatch.setitem(sys.modules, latest.__name__, latest)
    source = Path(__file__).resolve().parents[1] / PKG / "image_writer.py"
    spec = importlib.util.spec_from_file_location(f"{PKG}.image_writer_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check = module.MiniMaxH3ImagePromptWriter.check_lazy_status
    assert check(backend) == []
    assert check(backend, **{socket: None}) == [socket]
    assert check(backend, **{socket: object()}) == []
    assert check(backend, bypass=True, **{socket: None}) == []
    assert check(image.CONTEXT_IR, **{socket: None}) == []
