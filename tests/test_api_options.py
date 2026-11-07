"""Options at the real HTTP boundary and bypass without profile/media access."""

import importlib
import json
from dataclasses import FrozenInstanceError

import pytest

from test_api_text import ANSWER, SECTIONS, config, node_environment, run_node, writer
from test_api_writer import client, completion, fake_server, local_profile, profiles
from test_api_images import picture
from test_api_multimedia import FileVideo, omni, sound

options = importlib.import_module("minimax_h3_rewriter.api_options")


def test_options_node_has_only_non_secret_settings_and_returns_immutable_values():
    node = options.MiniMaxH3ApiWriterOptions()
    schema = node.INPUT_TYPES()
    fields = schema["required"] | schema["optional"]
    assert len(fields) == 10
    assert not {"base_url", "api_key", "headers", "model", "secret"} & fields.keys()
    value, = node.execute(max_tokens=512, temperature=0.25)
    with pytest.raises(FrozenInstanceError):
        value.max_tokens = 1024
    schema["required"]["response_format"][0].append("arbitrary")
    assert "arbitrary" not in node.INPUT_TYPES()["required"]["response_format"][0]


@pytest.mark.parametrize("settings", [
    {"max_tokens": True}, {"temperature": float("nan")}, {"temperature": -0.5},
    {"timeout_seconds": 0}, {"video_sample_seconds": float("inf")},
    {"video_max_seconds": "15"}, {"batch_video_fps": 0},
    {"audio_sample_rate": 22050}, {"auto_download_guide": "false"},
    {"repair_once": "false"}, {"repair_once": 1},
    {"base_url": "https://untrusted.example/"}, {"response_format": "provider-secret-value"},
])
def test_invalid_options_are_rejected_without_echoing_values(settings):
    with pytest.raises(client.ContractError) as error:
        options.MiniMaxH3ApiWriterOptions().execute(**settings)
    assert "provider-secret-value" not in str(error.value)
    assert "untrusted.example" not in str(error.value)


def test_connected_options_override_legacy_scalars_in_the_actual_request(node_environment, monkeypatch):
    directory, _messages = node_environment
    guides, deadlines = [], []

    def guide(kind, download, _progress):
        guides.append((kind, download))
        return "A local test guide."

    def connection(*args, **kwargs):
        deadlines.append((kwargs["deadline"], kwargs["timeout"]))
        return client.ApiClient(*args, **kwargs)

    monkeypatch.setattr(writer.guides, "text", guide)
    monkeypatch.setattr(writer, "ApiClient", connection)
    picked, = options.MiniMaxH3ApiWriterOptions().execute(max_tokens=64, temperature=0.25,
        response_format="json_schema", timeout_seconds=7, auto_download_guide=False)
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        api.body = completion(json.dumps(SECTIONS))
        output = run_node(profile.id, "T2VA", "cat", "16:9", 5, 999999, options=picked,
            response_format="invalid-legacy-format", temperature=float("nan"),
            timeout_seconds=-1, auto_download_guide="invalid-legacy-flag", repair_once="invalid-legacy-flag")
        sent = api.calls[0]["body"]
        assert output[0] == ANSWER and len(api.calls) == 1
        assert sent["max_tokens"] == 64 and sent["temperature"] == 0.25
        assert sent["response_format"]["type"] == "json_schema"
        assert guides == [("base", False)] and deadlines == [(7, (5.0, 7))]


@pytest.mark.parametrize("settings,legacy,requests", [
    ({}, True, 1), ({"repair_once": True}, False, 2), ({"repair_once": False}, True, 1),
    (None, True, 2), (None, False, 1),
])
def test_repair_request_count_uses_options_or_unconnected_legacy_flag(node_environment, settings, legacy, requests):
    directory, _messages = node_environment
    picked = None if settings is None else options.MiniMaxH3ApiWriterOptions().execute(**settings)[0]
    broken = ANSWER.replace("[Shot 1]", "[Shot 3]")
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        api.body = completion(broken)
        output = run_node(profile.id, "T2VA", "cat", "16:9", 5, options=picked,
            system_prompt="Return an H3 prompt.", repair_once=legacy)
        report = json.loads(output[-2])
        assert len(api.calls) == report["requests"] == report["completed_requests"] == requests
        assert output[0] == broken


def test_options_control_real_video_sampling_and_audio_copy(node_environment):
    directory, _messages = node_environment
    value, = options.MiniMaxH3ApiWriterOptions().execute(max_tokens=64, video_sample_seconds=0.5,
        video_max_seconds=1.5, batch_video_fps=4, audio_sample_rate=24000)
    with fake_server() as api:
        profile = omni(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        api.body = completion("A reference caption.")
        output = run_node(profile.id, "Ref2VA", "Describe.", "16:9", 5, options=value,
            system_prompt="Return plain text.", validation="custom", ref_0=picture(frames=8), ref_1=sound(),
            reference_layout='{"roles":{"ref_0":"Video"}}', video_sample_seconds=99,
            video_max_seconds=99, batch_video_fps=99, audio_sample_rate=48000)
        report = json.loads(output[-2])
        assert report["videos"][0]["source_seconds"] == 2
        assert report["videos"][0]["window_seconds"] == 1.5
        assert [one["timestamp_seconds"] for one in report["media"] if one["kind"] == "video_frame"] == [0, 0.5, 1]
        assert next(one for one in report["media"] if one["kind"] == "audio")["sample_rate"] == 24000


def test_untyped_options_cannot_smuggle_profile_fields(node_environment, monkeypatch):
    monkeypatch.setattr(writer.api_config, "load_profiles", lambda: pytest.fail("Read profile for invalid options"))
    with pytest.raises(client.ContractError, match="Connect MiniMax-H3 API Writer Options"):
        run_node("unused", "T2VA", "cat", "16:9", 5, options={"base_url": "https://untrusted.example/"})


@pytest.mark.parametrize("task", ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"])
def test_bypass_needs_no_profile_guide_caption_or_media_read(node_environment, monkeypatch, task):
    class UnreadableVideo(FileVideo):
        def get_stream_source(self):
            pytest.fail("Bypass opened video")

    def unexpected(*_args, **_kwargs):
        pytest.fail("Bypass accessed configuration, media or the API")

    for owner, name in ((writer.api_config, "load_profiles"), (writer.guides, "text"),
                        (writer, "resolve_clip_sounds_async"), (writer, "prepare_references_async"),
                        (writer, "ApiClient"), (writer.library, "picked"), (writer.memory, "repeat")):
        monkeypatch.setattr(owner, name, unexpected)
    video = UnreadableVideo("never-opened.mp4")
    output = run_node("unconfigured", task, "  Raw prompt.  ", "16:9", 5, bypass=True,
        repeat_last=True, options=object(), ref_0=video, reference_layout='{"sound":["ref_0"]}')
    assert output[0] == "Raw prompt." and not any(output[1:-2])
    assert len(output) == len(writer.MiniMaxH3ApiUniversalWriter.RETURN_TYPES)
    report = json.loads(output[-2])
    assert report["source"] == "bypass" and report["requests"] == report["completed_requests"] == 0
    assert output[-1]["videos"] == ([] if task == "T2VA" else [video])


def test_bypass_does_not_overwrite_the_answer_kept_for_repeat(node_environment):
    directory, _messages = node_environment
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        api.body = completion(ANSWER)
        common = {"system_prompt": "Return an H3 prompt.", "unique_id": "api-options-bypass-repeat"}
        first = run_node(profile.id, "T2VA", "cat", "16:9", 5, **common)
        bypassed = run_node(profile.id, "T2VA", "Unprocessed request.", "16:9", 5, bypass=True, **common)
        repeated = run_node(profile.id, "T2VA", "new request", "16:9", 5, repeat_last=True, **common)
        assert first[0] == repeated[0] == ANSWER and bypassed[0] == "Unprocessed request."
        assert json.loads(repeated[-2])["requests"] == 0 and len(api.calls) == 1
