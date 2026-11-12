"""Full Qwen contract boundaries: media order, task families and no-work paths."""

import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from minimax_h3_rewriter import qwen_engine, qwen_models, qwen_node, qwen_prompt
from minimax_h3_rewriter.api_contract import make_job
from minimax_h3_rewriter.api_media import PreparedImage
from minimax_h3_rewriter.api_multimedia import Transport, arrange_references
from minimax_h3_rewriter.qwen_options import QwenOptions, resolve_options


def picture(slot="ref_0", colour="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, format="JPEG")
    return PreparedImage(slot, 1, 1, 64, 64, 64, 64, buffer.getvalue())


def job(task="T2VA", refs=(), prompt="Animate this scene."):
    return make_job(profile_id="qwen", task=task, prompt=prompt, validation="h3",
                    system_prompt="A test guide.", duration=10.0, references=tuple(one.spec for one in refs))


@pytest.mark.parametrize("name,wanted", [("Qwen3.5 9B FL2VA SFT", "FL2VA"),
    ("MiniMax-H3-Qwen3.8-27B-Ref2VA-Q5_K_M.gguf", "Ref2VA")])
def test_checkpoint_family_must_be_explicit(name, wanted):
    assert qwen_models.family(name) == wanted


@pytest.mark.parametrize("name", ["Qwen3.5-9B", "Ref2VA plus FL2VA", "unknown"])
def test_ambiguous_checkpoint_is_not_guessed(name):
    with pytest.raises(ValueError):
        qwen_models.family(name)


@pytest.mark.parametrize("options", [{"max_new_tokens": 32768}, {"thinking": 1},
    {"context_size": True}, {"temperature": float("nan")}, {"video_max_frames": 0},
    {"device": 42}, {"llama_backend": "invalid"}, {"quantization": "unknown"}])
def test_invalid_options_fail_at_the_options_boundary(options):
    with pytest.raises(ValueError):
        QwenOptions(**options)


def test_options_from_another_node_are_not_silently_used():
    with pytest.raises(ValueError):
        resolve_options({"max_new_tokens": 12})


def test_fl2va_keeps_first_and_last_images_interleaved_with_their_labels():
    refs = arrange_references({"ref_0": np.zeros((1, 64, 64, 3)), "ref_1": np.ones((1, 64, 64, 3))},
                              '{"order":["ref_1","ref_0"]}')
    images = (picture("ref_1", "blue"), picture("ref_0", "red"))
    messages, supplied = qwen_prompt.build(job("FL2VA", refs), Transport(images))
    parts = messages[1]["content"]
    assert [one.slot for one in supplied] == ["ref_1", "ref_0"]
    assert [one["type"] for one in parts] == ["text", "text", "image", "text", "image"]
    assert "first frame at 0.00" in parts[1]["text"]
    assert "last frame at 10.00" in parts[3]["text"]
    converted = qwen_engine.server_messages(messages, supplied)[1]["content"]
    assert converted[2]["image_url"]["url"] != converted[4]["image_url"]["url"]


def test_an_image_batch_is_not_accepted_as_one_exact_frame():
    refs = arrange_references({"ref_0": np.zeros((2, 64, 64, 3))})
    with pytest.raises(ValueError, match="one image per exact frame"):
        qwen_prompt.build(job("I2VA", refs), Transport((picture(), picture())))


def test_ref2va_video_samples_have_timestamps_and_one_video_label():
    frames = np.zeros((12, 64, 64, 3), dtype=np.float32)
    refs = arrange_references({"ref_0": frames}, '{"roles":{"ref_0":"Video"}}')
    transport = asyncio.run(qwen_prompt.prepare(refs, QwenOptions(video_max_frames=3, batch_video_fps=1.0), 10.0))
    request, images = qwen_prompt.build(job("Ref2VA", refs), transport)
    text = "\n".join(one.get("text", "") for one in request[1]["content"])
    assert len(images) == 3
    assert all(one.slot == "ref_0" for one in images)
    assert "video 1, frame 1/3, source timestamp 0.000000" in text
    assert "video 2" not in text
    assert images[-1].timestamp_seconds > images[0].timestamp_seconds


class UnreadAudio(dict):
    def __getitem__(self, key):
        raise AssertionError("An audio waveform was read by a vision-only writer")


def test_audio_description_is_used_without_reading_or_encoding_the_waveform():
    refs = arrange_references({"ref_0": UnreadAudio(waveform=None)},
                             raw_descriptions='{"ref_0":"A low voice says hello."}')
    transport = asyncio.run(qwen_prompt.prepare(refs, QwenOptions(), 10.0))
    messages, images = qwen_prompt.build(job("Ref2VA", refs), transport)
    assert images == [] and transport.audios == ()
    assert "A low voice says hello." in str(messages)
    assert refs[0].handed is refs[0].value


def test_undescribed_audio_stops_before_reading_the_waveform():
    refs = arrange_references({"ref_0": UnreadAudio(waveform=None)})
    with pytest.raises(ValueError, match="explicit sound description"):
        asyncio.run(qwen_prompt.prepare(refs, QwenOptions(), 10.0))


def test_disabled_media_is_not_inspected():
    class Untouchable:
        def __getattribute__(self, _name):
            raise AssertionError("A disabled reference was inspected")
    assert arrange_references({"ref_0": Untouchable()}, '{"off":["ref_0"]}') == ()


def test_disabled_and_t2va_references_do_not_request_upstream_execution():
    node = qwen_node.MiniMaxH3QwenRewriter()
    assert node.check_lazy_status("T2VA", ref_0=None) == []
    assert node.check_lazy_status("Ref2VA", '{"off":["ref_0"]}', ref_0=None, ref_1=None) == ["ref_1"]


@pytest.mark.parametrize("family,task", [("FL2VA", "Ref2VA"), ("Ref2VA", "T2VA")])
def test_checkpoint_mismatch_is_refused(family, task):
    with pytest.raises(ValueError, match="checkpoint"):
        qwen_prompt.validate_family(family, task, ())


def test_bypass_uses_no_model_guide_or_media_preparation(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Bypass started work")
    monkeypatch.setattr(qwen_models, "resolve", forbidden)
    monkeypatch.setattr(qwen_prompt, "prepare", forbidden)
    monkeypatch.setattr(qwen_node.guides, "text", forbidden)
    outputs = asyncio.run(qwen_node.MiniMaxH3QwenRewriter().execute(
        reference_layout="{}", task="T2VA", resolution="16:9", duration=10.0,
        model="unused", prompt="Keep this.", bypass=True, ref_0=object()))
    assert outputs[0] == "Keep this."
    assert json.loads(outputs[-2])["generations"] == 0


def test_an_unsupported_sft_loader_stops_before_media_preparation_or_download(monkeypatch):
    monkeypatch.setattr(qwen_models, "resolve", lambda _name: qwen_models.Choice("test/FL2VA", "transformers", "FL2VA"))
    def unavailable(*_args):
        raise RuntimeError("SFT loader unavailable")
    monkeypatch.setattr(qwen_engine, "check_support", unavailable)
    monkeypatch.setattr(qwen_prompt, "prepare", lambda *_args: pytest.fail("Prepared media before the loader check"))
    monkeypatch.setattr(qwen_models, "ensure", lambda *_args: pytest.fail("Downloaded before the loader check"))
    with pytest.raises(RuntimeError, match="SFT loader unavailable"):
        asyncio.run(qwen_node.MiniMaxH3QwenRewriter().execute(reference_layout="{}", task="T2VA", resolution="16:9",
                    duration=5.0, model="test", prompt="A garden."))


@pytest.mark.parametrize("raw,text,thought", [("<think>why</think>answer", "answer", "why"),
    ("why</think>answer", "answer", "why"), ("<think>unfinished", "", "unfinished"),
    ("answer", "answer", "")])
def test_reasoning_is_separate_from_the_prompt(raw, text, thought):
    assert qwen_prompt.split_reasoning(raw) == (text, thought)


def test_truncated_prefilled_thinking_is_not_returned_as_the_final_prompt():
    assert qwen_prompt.split_reasoning("Unfinished private analysis", thinking=True) == ("", "Unfinished private analysis")


@pytest.mark.parametrize("width,layers", [(4096, 32), (5120, 64)])
def test_full_qwen_shapes_are_accepted(width, layers):
    config = {"model_type": "qwen3_5", "vision_config": {"model_type": "qwen3_5_vision"},
              "text_config": {"hidden_size": width, "num_hidden_layers": layers}}
    assert not qwen_models.config_problem(config)
    assert qwen_models.config_problem({**config, "vision_config": {}})


def test_aspect_ratio_from_a_wire_overrides_the_picker():
    assert qwen_node._ratio("3840x1080", "1:1") == "32:9"
    assert qwen_node._ratio("1.77", "1:1") == "16:9"


def test_video_sound_description_keeps_a_separate_audio_label_in_canonical_order():
    from minimax_h3_rewriter.api_contract import ReferenceSpec
    from minimax_h3_rewriter.api_media import PreparedVideoFrame
    ref = ReferenceSpec("ref_0", "Video", "video", clip_audio=True,
                        audio_description="A bell rings twice.")
    request = make_job(profile_id="qwen", task="Ref2VA", prompt="Keep the clip sound.",
                       system_prompt="A test guide.", validation="h3", duration=10.0, references=(ref,))
    source = picture()
    frame = PreparedVideoFrame(source.slot, source.frame, source.frames, source.source_width,
                               source.source_height, source.width, source.height, source.jpeg)
    messages, images = qwen_prompt.build(request, Transport((frame,)))
    parts = messages[1]["content"]
    assert images == [frame]
    assert "audio 1" in parts[1]["text"]
    assert "A bell rings twice." in parts[1]["text"]
    assert parts[-1]["type"] == "image"
    assert [(one.label, one.component) for one in request.manifest] == [("Audio 1", "sound"), ("Video 1", "media")]
