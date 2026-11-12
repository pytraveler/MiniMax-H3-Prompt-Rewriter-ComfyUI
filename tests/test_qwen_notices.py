"""Missing reference sound descriptions stop Qwen with an actionable notice."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from minimax_h3_rewriter import qwen_engine, qwen_models, qwen_node, qwen_prompt, refmods
from minimax_h3_rewriter.api_contract import ContractError, make_job
from minimax_h3_rewriter.api_multimedia import Transport, arrange_references
from minimax_h3_rewriter.qwen_options import QwenOptions


class UnreadAudio(dict):
    def __getitem__(self, _name):
        raise AssertionError("A vision-only Qwen writer read an audio waveform")


class UnreadVideo:
    def get_components(self):
        raise AssertionError("Missing sound text caused video decoding")

    def get_stream_source(self):
        raise AssertionError("Missing sound text opened a video container")


def forbid_work(*_args, **_kwargs):
    pytest.fail("Missing descriptions started media/model work")


@pytest.fixture
def no_media_decoding(monkeypatch):
    monkeypatch.setattr(qwen_prompt, "prepare_references_async", forbid_work)


@pytest.mark.parametrize("description", ["", " \t\n "])
def test_audio_notice_explains_the_exact_description_controls(description, no_media_decoding):
    refs = arrange_references({"ref_3": UnreadAudio(waveform=None)},
                              raw_descriptions=json.dumps({"ref_3": description}))
    with pytest.raises(ContractError) as caught:
        asyncio.run(qwen_prompt.prepare(refs, QwenOptions(), 5.0))
    message = str(caught.value)
    assert "Qwen cannot hear reference audio" in message
    assert "Audio 1" in message and "ref_3" in message
    assert all(text in message for text in ("+ instr", "Sound description", "Save instructions", "checkbox"))


@pytest.mark.parametrize("description", ["", " \t\n "])
def test_clip_notice_explains_how_to_keep_video_without_sound(description, no_media_decoding):
    refs = arrange_references({"ref_1": UnreadVideo()}, '{"sound":["ref_1"]}',
                              raw_descriptions=json.dumps({"ref_1": {"sound": description}}))
    with pytest.raises(ContractError) as caught:
        asyncio.run(qwen_prompt.prepare(refs, QwenOptions(), 5.0))
    message = str(caught.value)
    assert "Qwen cannot hear reference audio" in message
    assert "Video 1" in message and "ref_1" in message
    assert all(text in message for text in ("+ instr", "Clip sound description", "Save instructions"))
    assert "sound" in message.lower() and "video" in message.lower()
    assert "disable" in message.lower() or "turn" in message.lower()


def test_all_missing_descriptions_are_reported_with_canonical_labels(no_media_decoding):
    refs = arrange_references({"ref_2": UnreadAudio(waveform=None), "ref_7": UnreadVideo()},
                              '{"order":["ref_7","ref_2"],"sound":["ref_7"]}')
    with pytest.raises(ContractError) as caught:
        asyncio.run(qwen_prompt.prepare(refs, QwenOptions(), 5.0))
    message = str(caught.value)
    # The video's sound owns Audio 1; the separate AUDIO reference is Audio 2.
    assert all(text in message for text in ("Video 1", "ref_7", "Audio 2", "ref_2"))
    assert "Sound description" in message and "Clip sound description" in message


@pytest.mark.parametrize("saved_refmod", [False, True])
def test_explicit_or_saved_voice_text_is_used_without_reading_audio(monkeypatch, saved_refmod):
    description = "A calm low voice says hello."
    sound = UnreadAudio(waveform=None)
    asset = (refmods.RefMod("Saved voice", "audio", object(), voice=description, sound=sound)
             if saved_refmod else sound)
    refs = arrange_references({"ref_0": asset},
                              raw_descriptions="{}" if saved_refmod else json.dumps({"ref_0": description}))
    received = []

    async def visuals_only(visual, **_kwargs):
        received.append(visual)
        return visual, Transport()

    monkeypatch.setattr(qwen_prompt, "prepare_references_async", visuals_only)
    transport = asyncio.run(qwen_prompt.prepare(refs, QwenOptions(), 5.0))
    job = make_job(profile_id="qwen", task="Ref2VA", prompt="Keep this sound.",
                   validation="h3", system_prompt="A guide.", duration=5.0,
                   references=tuple(one.spec for one in refs))
    messages, images = qwen_prompt.build(job, transport)
    assert received == [()] and transport.audios == () and images == []
    assert description in str(messages)
    assert refs[0].handed is asset


def test_disabled_audio_does_not_require_a_description(monkeypatch):
    refs = arrange_references({"ref_0": UnreadAudio(waveform=None)}, '{"off":["ref_0"]}')

    async def no_visuals(visual, **_kwargs):
        assert visual == ()
        return visual, Transport()

    monkeypatch.setattr(qwen_prompt, "prepare_references_async", no_visuals)
    transport = asyncio.run(qwen_prompt.prepare(refs, QwenOptions(), 5.0))
    assert transport.images == () and transport.audios == ()


@pytest.fixture
def node_boundary(monkeypatch):
    notices, progress = [], []

    class Progress:
        def __init__(self, node_id):
            self.node_id = node_id

        def text(self, message, force=False):
            progress.append((message, force))

        def finish(self, message):
            progress.append((message, True))

    monkeypatch.setattr(qwen_node, "NodeProgress", Progress)
    monkeypatch.setattr(qwen_node, "announce", lambda node, findings, **kwargs:
                        notices.append((node, findings, kwargs.get("kind"))))
    monkeypatch.setattr(qwen_models, "resolve", lambda _label:
                        SimpleNamespace(fmt="gguf", family="Ref2VA"))
    monkeypatch.setattr(qwen_engine, "check_support", lambda *_args: None)
    monkeypatch.setattr(qwen_models, "ensure", forbid_work)
    monkeypatch.setattr(qwen_engine, "generate", forbid_work)
    monkeypatch.setattr(qwen_node.guides, "text", forbid_work)
    monkeypatch.setattr(qwen_node.library, "picked", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(qwen_node.memory, "repeat", lambda *_args, **_kwargs: None)
    return notices, progress


def call_node(**overrides):
    values = dict(reference_layout="{}", task="Ref2VA", resolution="16:9", duration=5.0,
                  model="A Ref2VA model", prompt="Use this sound.", unique_id="notice-test",
                  ref_0=UnreadAudio(waveform=None))
    values.update(overrides)
    return asyncio.run(qwen_node.MiniMaxH3QwenRewriter().execute(**values))


@pytest.mark.parametrize("self_check", ["all", "none"])
def test_missing_sound_emits_a_refusal_even_when_self_check_is_off(node_boundary, no_media_decoding, self_check):
    notices, progress = node_boundary
    with pytest.raises(ContractError) as caught:
        call_node(options=QwenOptions(self_check=self_check))
    message = str(caught.value)
    assert notices == [("notice-test", [("warn", message)], "refusal")]
    assert (message, True) in progress


def test_media_contract_error_is_rethrown_after_one_notice(node_boundary, monkeypatch):
    notices, progress = node_boundary
    original = ContractError("The connected video could not be read.")

    async def fail_prepare(*_args, **_kwargs):
        raise original

    monkeypatch.setattr(qwen_prompt, "prepare", fail_prepare)
    with pytest.raises(ContractError) as caught:
        call_node()
    assert caught.value is original
    assert notices == [("notice-test", [("warn", str(original))], "refusal")]
    assert (str(original), True) in progress


def test_bypass_does_not_add_a_required_audio_description(node_boundary, monkeypatch):
    notices, _progress = node_boundary
    monkeypatch.setattr(qwen_prompt, "prepare", forbid_work)
    result = call_node(bypass=True)
    assert result[0] == "Use this sound."
    assert json.loads(result[-2])["generations"] == 0
    assert notices == []


def test_repeat_keeps_its_existing_no_media_work_path(node_boundary, monkeypatch):
    notices, _progress = node_boundary
    monkeypatch.setattr(qwen_prompt, "prepare", forbid_work)
    monkeypatch.setattr(qwen_node.memory, "repeat", lambda *_args, **_kwargs: ("An already kept prompt.",))
    monkeypatch.setattr(qwen_node.resident, "settle", lambda *_args: None)
    result = call_node(repeat_last=True)
    assert result[0] == "An already kept prompt."
    assert json.loads(result[-2])["generations"] == 0
    assert notices == []
