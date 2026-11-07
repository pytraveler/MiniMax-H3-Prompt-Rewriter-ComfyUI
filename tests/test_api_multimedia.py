"""Actual audio/container decoding, manifest binding and node transport boundaries."""

import asyncio
import base64
import importlib
import io
import json
import types
import wave
from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest

from test_api_writer import client, completion, contract, fake_server, job, local_profile, profiles
from test_api_text import config, node_environment, run_node, writer
from test_api_images import picture, sent_pixels

multimedia = importlib.import_module("minimax_h3_rewriter.api_multimedia")
refmods = importlib.import_module("minimax_h3_rewriter.refmods")
memory = importlib.import_module("minimax_h3_rewriter.memory")


def sound(seconds=2.0, rate=24000, channels=2):
    samples = np.sin(2 * np.pi * 440 * np.arange(round(seconds * rate)) / rate).astype(np.float32) * 0.5
    return {"waveform": np.broadcast_to(samples, (1, channels, len(samples))).copy(), "sample_rate": rate}


class ComponentVideo:
    def __init__(self, images=None, audio=None, fps=10):
        self.parts = types.SimpleNamespace(images=picture(frames=20) if images is None else images,
                                          audio=audio, frame_rate=Fraction(fps))

    def get_components(self):
        return self.parts


class FileVideo:
    def __init__(self, source, start=0.0, duration=0.0):
        self.source, self.start, self.duration = source, start, duration

    def get_stream_source(self):
        return self.source

    def get_active_trim_window(self):
        return self.start, self.duration


def video_file(with_sound=True, *, times=None):
    av = pytest.importorskip("av")
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="matroska") as output:
        visual = output.add_stream("ffv1", rate=10)
        visual.width, visual.height, visual.pix_fmt = 64, 32, "bgr0"
        audio = output.add_stream("pcm_s16le", rate=16000) if with_sound else None
        if audio:
            audio.layout = "mono"
        for index, at in enumerate(times or [n / 10 for n in range(20)]):
            pixels = np.zeros((32, 64, 3), dtype=np.uint8)
            pixels[..., 0 if at < 1 else 2] = 255
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = round(at * 1000), Fraction(1, 1000)
            for packet in visual.encode(frame):
                output.mux(packet)
            if audio:
                data = np.full((1, 1600), 8192 if at < 1 else -8192, dtype=np.int16)
                af = av.AudioFrame.from_ndarray(data, format="s16", layout="mono")
                af.sample_rate, af.pts, af.time_base = 16000, index * 1600, Fraction(1, 16000)
                for packet in audio.encode(af):
                    output.mux(packet)
        for stream in (visual, audio):
            if stream:
                for packet in stream.encode(None):
                    output.mux(packet)
    buffer.seek(0)
    return buffer


def prepare(values, layout="{}", descriptions="{}", **options):
    assets = multimedia.arrange_references(values, layout, "{}", descriptions)
    return asyncio.run(multimedia.prepare_references_async(assets, **options))


def omni(api, **changes):
    return local_profile(api, capabilities=contract.ModelCapabilities(
        modalities={"text", "image", "audio"}, formats={"text", "json_schema"}), **changes)


def audio_pixels(part):
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "wav"
    data = base64.b64decode(part["input_audio"]["data"])
    with wave.open(io.BytesIO(data), "rb") as handle:
        return handle.getframerate(), handle.getnchannels(), np.frombuffer(handle.readframes(handle.getnframes()), "<i2")


def test_clip_audio_is_numbered_before_standalone_sound_and_originals_pass_through(node_environment):
    directory, _ = node_environment
    standalone, clip_sound = sound(), sound(seconds=2, rate=48000)
    clip = ComponentVideo(audio=clip_sound)
    original_wave = standalone["waveform"].copy()
    with fake_server() as api:
        profile = omni(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        result = run_node(profile.id, "Ref2VA", " raw \n", "16:9", 2, 128,
            system_prompt="Plain caption.", validation="custom", ref_0=standalone, ref_1=clip,
            reference_layout='{"sound":["ref_1"]}', video_max_frames=2, video_sample_seconds=0.5)
        manifest = json.loads(result[-2])["references"]
        assert [(one["slot"], one["label"], one["component"]) for one in manifest] == [
            ("ref_1", "Audio 1", "sound"), ("ref_1", "Video 1", "media"), ("ref_0", "Audio 2", "media")]
        parts = api.calls[0]["body"]["messages"][1]["content"]
        assert [part["text"].split(" (")[0] for part in parts[1::2]] == ["<Audio 1>", "<Video 1>", "<Video 1>", "<Audio 2>"]
        rate, channels, samples = audio_pixels(parts[2])
        assert (rate, channels, len(samples)) == (16000, 1, 32000)
        assert result[-1]["audios"][0] is standalone and result[-1]["videos"][0] is clip
        assert result[-1]["video_sounds"] == [True]
        assert np.array_equal(standalone["waveform"], original_wave)
        assert "base64" not in result[-2] and "data:image" not in result[-2]


def test_audio_copies_preserve_frequency_and_report_resampling_downmix_and_crop():
    value = sound(seconds=3)
    assets, copied = prepare({"ref_0": value}, audio_max_seconds=1.25)
    copied_audio = copied.audios[0]
    rate, channels, samples = audio_pixels(copied_audio.content())
    assert (rate, channels, len(samples)) == (16000, 1, 20000)
    frequency = np.argmax(np.abs(np.fft.rfft(samples))) * rate / len(samples)
    assert frequency == pytest.approx(440, abs=1)
    assert copied_audio.metadata()["trimmed"] and copied_audio.metadata()["resampled"] and copied_audio.metadata()["downmixed"]
    assert assets[0].handed is value and copied_audio.wav not in repr(copied_audio).encode()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.1, 1.1])
def test_invalid_audio_values_fail_before_network(bad):
    value = sound(seconds=0.1)
    value["waveform"][0, 0, 0] = bad
    with pytest.raises(contract.ContractError, match="finite"):
        prepare({"ref_0": value})


def test_audio_batches_and_byte_limits_are_not_silently_truncated():
    value = sound()
    value["waveform"] = np.repeat(value["waveform"], 2, axis=0)
    with pytest.raises(contract.ContractError, match="one waveform batch"):
        prepare({"ref_0": value})
    with pytest.raises(contract.ContractError, match="byte limit"):
        prepare({"ref_0": sound()}, byte_limit=100)


def test_disabled_audio_does_not_read_lazy_waveform():
    class Lazy(dict):
        def get(self, key, default=None):
            pytest.fail("Disabled audio was read")

    assets, copied = prepare({"ref_0": Lazy(waveform=None)}, layout='{"off":["ref_0"]}')
    assert not assets and not copied.audios


def test_video_sound_off_is_absent_from_transport_and_generator_flags(node_environment):
    directory, _ = node_environment
    class UnreadableAudio:
        pass
    clip = ComponentVideo(audio=UnreadableAudio())
    with fake_server() as api:
        profile = local_profile(api, capabilities=contract.ModelCapabilities(modalities={"text", "image"}))
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        result = run_node(profile.id, "Ref2VA", "raw", "16:9", 2, 128,
                          system_prompt="Caption.", validation="custom", ref_0=clip)
        parts = api.calls[0]["body"]["messages"][1]["content"]
        assert {part["type"] for part in parts} == {"text", "image_url"}
        assert result[-1]["video_sounds"] == [False]


def test_sound_on_silent_clip_has_no_phantom_audio_label():
    assets, copied = prepare({"ref_0": FileVideo(video_file(False))}, layout='{"sound":["ref_0"]}')
    assert not assets[0].spec.clip_audio and not copied.audios
    assert "no audio stream" in copied.notes[0]
    assert [one.label for one in contract.reference_manifest("Ref2VA", tuple(one.spec for one in assets))] == ["Video 1"]


def test_real_container_trim_frames_and_audio_are_on_the_same_window():
    source = video_file()
    source.seek(7)
    clip = FileVideo(source, start=1, duration=0.8)
    assets, copied = prepare({"ref_0": clip}, layout='{"sound":["ref_0"]}',
                            video_sample_seconds=0.2, video_max_frames=3, target_seconds=0.6)
    assert source.tell() == 7
    assert assets[0].handed is clip and assets[0].spec.clip_audio
    assert len(copied.images) == 3
    assert [one.timestamp_seconds for one in copied.images] == pytest.approx([0, 0.2, 0.4], abs=0.001)
    assert [one.source_timestamp_seconds for one in copied.images] == pytest.approx([1, 1.2, 1.4], abs=0.001)
    assert all(sent_pixels(one.content())[0, 0, 2] > 240 for one in copied.images)
    rate, _channels, samples = audio_pixels(copied.audios[0].content())
    assert len(samples) == round(0.6 * rate) and samples.mean() < -7000
    assert copied.videos[0]["window_seconds"] == pytest.approx(0.6)
    assert copied.audios[0].start_seconds == 1


def test_vfr_container_uses_actual_presentation_times():
    clip = FileVideo(video_file(False, times=[0, 0.1, 0.3, 0.6, 1.0, 1.7]))
    _, copied = prepare({"ref_0": clip}, video_sample_seconds=0.5, video_max_frames=8)
    timestamps = [one.timestamp_seconds for one in copied.images]
    assert timestamps[:3] == pytest.approx([0, 0.6, 1.0], abs=0.001)
    assert timestamps == sorted(set(timestamps))


def test_image_batch_video_sampling_keeps_one_label_and_reports_limit():
    images = picture(frames=100, width=16, height=16)
    assets, copied = prepare({"ref_0": images}, layout='{"roles":{"ref_0":"Video"}}',
                            batch_video_fps=10, video_sample_seconds=0.5, video_max_frames=3, target_seconds=4)
    assert assets[0].spec.kind == "image" and assets[0].spec.role == "Video"
    assert [one.timestamp_seconds for one in copied.images] == pytest.approx([0, 1.7, 3.5])
    assert all(one.frames == 3 for one in copied.images)
    assert copied.videos[0]["source_frames"] == 100 and copied.videos[0]["window_seconds"] == 4


def test_refmod_look_voice_pair_keeps_saved_descriptions_links_and_original_objects(node_environment):
    directory, _ = node_environment
    visual = refmods.RefMod("Hero", "image", object(), appearance="Pale coat and blonde hair.", pictures=picture())
    voice = refmods.RefMod("Hero", "audio", object(), voice="A low soft voice.", sound=sound(seconds=0.1))
    with fake_server() as api:
        profile = omni(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        result = run_node(profile.id, "Ref2VA", "raw", "16:9", 2, 128,
            system_prompt="Caption.", validation="custom", ref_0=visual, ref_1=voice)
        manifest = json.loads(result[-2])["references"]
        assert [one["related_slots"] for one in manifest] == [["ref_1"], ["ref_0"]]
        assert all(one["refmod"] for one in manifest)
        assert manifest[0]["description"] == visual.appearance and manifest[1]["description"] == voice.voice
        assert result[-1]["pictures"][0] is visual and result[-1]["audios"][0] is voice


def test_refmod_video_uses_saved_times_and_retains_stack_hint():
    mod = refmods.RefMod("Look", "video", object(), pictures=picture(frames=4), times=[0, 0.3, 0.9, 1.5])
    _, copied = prepare({"ref_0": mod}, video_sample_seconds=0.5)
    assert [one.source_timestamp_seconds for one in copied.images] == [0, 0.3, 0.9, 1.5]
    stack = replace(mod, source="stack", pictures=picture(frames=16), times=[])
    assets, copied = prepare({"ref_0": stack}, video_sample_seconds=0.5)
    assert "separate photographs" in assets[0].spec.instruction and copied.videos[0]["refmod_stack"]


@pytest.mark.parametrize("mode", ["native", "descriptions"])
def test_visual_refmod_with_unsupported_seed_fails_before_http_and_works_when_omitted(node_environment, mode):
    directory, _ = node_environment
    visual = refmods.RefMod("Hero", "video", object(), source="stack", pictures=picture(frames=10),
                           appearance="A small green character in a pale robe.")
    with fake_server() as api:
        profile = local_profile(api, capabilities=contract.ModelCapabilities(modalities={"text", "image"}))
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        common = dict(system_prompt="Caption.", validation="custom", media_mode=mode, ref_0=visual)
        with pytest.raises(contract.ContractError) as error:
            run_node(profile.id, "Ref2VA", "Describe the reference.", "16:9", 11.5, seed=100393539, **common)
        message = str(error.value)
        assert "parameters: seed" in message and "seed=-1" in message and "fixed" in message
        assert "100393539" not in message and not api.calls
        output = run_node(profile.id, "Ref2VA", "Describe the reference.", "16:9", 11.5, seed=-1, **common)
        assert len(api.calls) == 1 and "seed" not in api.calls[0]["body"]
        parts = api.calls[0]["body"]["messages"][1]["content"]
        images = [part for part in parts if part["type"] == "image_url"] if isinstance(parts, list) else []
        assert 0 < len(images) <= 8 if mode == "native" else not images
        assert visual.appearance in (parts[0]["text"] if isinstance(parts, list) else parts)
        assert output[-1]["videos"][0] is visual


@pytest.mark.parametrize("kind,port", [("image", "vae"), ("audio", "audio_vae")])
def test_undecoded_refmod_names_missing_loader_input(kind, port):
    mod = refmods.RefMod("Missing", kind, object(), appearance="A coat.", voice="A voice.")
    with pytest.raises(contract.ContractError, match=port):
        prepare({"ref_0": mod})
    assets, copied = prepare({"ref_0": mod}, media_mode="descriptions")
    assert assets[0].handed is mod and not copied.images and not copied.audios


def test_descriptions_mode_sends_only_user_text_without_decoding_or_media_support(node_environment):
    directory, _ = node_environment
    class Unreadable:
        shape = (1, 32, 64, 3)
        def __getitem__(self, _key):
            pytest.fail("Descriptions mode read image pixels")
    image = Unreadable()
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        result = run_node(profile.id, "I2VA", "raw", "16:9", 2, 128,
            system_prompt="Caption.", validation="custom", media_mode="descriptions", ref_0=image,
            reference_descriptions='{"ref_0":"A red cat on a fence."}')
        content = api.calls[0]["body"]["messages"][1]["content"]
        assert isinstance(content, str) and "A red cat on a fence." in content
        assert not json.loads(result[-2])["media"] and result[-1]["pictures"][0] is image
        assert "Descriptions mode" in repr(json.loads(result[-2])["notes"])


def test_missing_descriptions_or_incompatible_model_make_no_request(node_environment):
    directory, _ = node_environment
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        for extra in ({"media_mode": "descriptions"}, {}):
            with pytest.raises(contract.ContractError):
                run_node(profile.id, "Ref2VA", "raw", "16:9", 2, 128,
                         system_prompt="Caption.", validation="custom", ref_0=sound(), **extra)
        assert not api.calls


def test_descriptions_of_silent_clip_do_not_require_a_phantom_sound_description(node_environment):
    directory, _ = node_environment
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        output = run_node(profile.id, "Ref2VA", "raw", "16:9", 2, 128,
            system_prompt="Caption.", validation="custom", media_mode="descriptions",
            ref_0=FileVideo(video_file(False)), reference_layout='{"sound":["ref_0"]}',
            reference_descriptions='{"ref_0":"A red scene."}')
        report = json.loads(output[-2])
        assert [one["label"] for one in report["references"]] == ["Video 1"]
        assert output[-1]["video_sounds"] == [False] and report["media"] == []
        assert "no audio stream" in repr(report["notes"])


def test_image_only_profile_accepts_silent_clip_but_checks_audible_track_before_conversion(node_environment, monkeypatch):
    directory, _ = node_environment
    with fake_server() as api:
        profile = local_profile(api, capabilities=contract.ModelCapabilities(modalities={"text", "image"}))
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        output = run_node(profile.id, "Ref2VA", "raw", "16:9", 2, 128,
            system_prompt="Caption.", validation="custom", ref_0=ComponentVideo(), reference_layout='{"sound":["ref_0"]}')
        assert output[-1]["video_sounds"] == [False] and len(api.calls) == 1
        monkeypatch.setattr(multimedia, "_audio_copy", lambda *_args, **_kwargs: pytest.fail("Unsupported audio was converted"))
        with pytest.raises(contract.ContractError, match="does not support"):
            run_node(profile.id, "Ref2VA", "raw", "16:9", 2, 128,
                system_prompt="Caption.", validation="custom", ref_0=ComponentVideo(audio=sound()),
                reference_layout='{"sound":["ref_0"]}')
        assert len(api.calls) == 1


def test_descriptions_without_clip_sound_do_not_open_the_video():
    class UnreadableVideo:
        def get_components(self):
            pytest.fail("Descriptions opened video components")
        def get_stream_source(self):
            pytest.fail("Descriptions opened a video source")
    assets, copies = prepare({"ref_0": UnreadableVideo()}, media_mode="descriptions",
                            descriptions='{"ref_0":"A scene."}')
    assert assets[0].spec.description == "A scene." and not copies.images


def test_accumulated_video_is_not_materialized_behind_a_frame_limit():
    class VideoFromList:
        def get_components(self):
            pytest.fail("Accumulated video decoded without a bound")
    with pytest.raises(contract.ContractError, match="Accumulated VIDEO"):
        prepare({"ref_0": VideoFromList()})
    assets, copied = prepare({"ref_0": VideoFromList()}, media_mode="descriptions",
                            descriptions='{"ref_0":"A concatenated scene."}')
    assert assets[0].spec.role == "Video" and not copied.images


def test_decoder_exceptions_cannot_disclose_source_names_or_paths():
    import traceback
    class BrokenVideo:
        def get_components(self):
            raise RuntimeError("PRIVATE_FILE_AND_TEST_CREDENTIAL")
    with pytest.raises(contract.ContractError) as error:
        prepare({"ref_0": BrokenVideo()})
    assert "PRIVATE_FILE_AND_TEST_CREDENTIAL" not in "".join(traceback.format_exception(error.value))


def test_video_request_byte_limit_and_global_frame_limit_are_explicit():
    with pytest.raises(contract.ContractError, match="byte limit"):
        prepare({"ref_0": FileVideo(video_file(False))}, byte_limit=100)
    with pytest.raises(contract.ContractError, match="64-image"):
        prepare({"ref_0": picture(frames=64, width=4, height=4), "ref_1": ComponentVideo()})


def test_local_video_cannot_load_a_playlist_or_a_network_share(tmp_path):
    playlist = tmp_path / "unsafe.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:1\nfile:///PRIVATE_TEST_FILE\n", encoding="ascii")
    with pytest.raises(contract.ContractError, match="supported local container"):
        prepare({"ref_0": FileVideo(str(playlist))})
    with pytest.raises(contract.ContractError, match="local file"):
        prepare({"ref_0": FileVideo(r"\\server\share\input.mp4")})


def test_prepared_parts_cannot_omit_duplicate_or_substitute_components():
    assets, copies = prepare({"ref_0": ComponentVideo(audio=sound())}, layout='{"sound":["ref_0"]}')
    with fake_server() as api:
        profile = omni(api)
        request = job(profile, task="Ref2VA", references=tuple(one.spec for one in assets))
        client.prepare_payload(request, profile, copies.images, copies.audios)
        for images, audios in ((copies.images, ()), ((), copies.audios), (copies.images, copies.audios * 2),
                               (copies.images, (replace(copies.audios[0], component="media"),))):
            with pytest.raises(contract.ContractError):
                client.prepare_payload(request, profile, images, audios)
        with pytest.raises(contract.ContractError):
            client.prepare_payload(replace(request, media_mode="descriptions",
                references=tuple(replace(one.spec, description="Scene.", audio_description="Wind.") for one in assets)),
                profile, copies.images, copies.audios)


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_cancellation_during_decoding_closes_all_owned_work(kind):
    value = sound(seconds=5) if kind == "audio" else FileVideo(video_file())
    assets = multimedia.arrange_references({"ref_0": value})
    async def run():
        stop = False
        task = asyncio.create_task(multimedia.prepare_references_async(assets, cancelled=lambda: stop))
        await asyncio.sleep(0)
        stop = True
        with pytest.raises(client.ApiError) as error:
            await task
        assert error.value.code == "cancelled"
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())
    if kind == "video":
        assert not value.source.closed


@pytest.mark.parametrize("source", ["https://example.com/a.mp4", "file:///private/key", "relative.mp4"])
def test_video_object_cannot_make_the_decoder_fetch_a_url_or_relative_path(source):
    with pytest.raises(contract.ContractError, match="local file"):
        prepare({"ref_0": FileVideo(source)})


def test_repeat_of_multimedia_keeps_current_originals_without_decoding(node_environment, monkeypatch):
    directory, _ = node_environment
    with fake_server() as api:
        profile = omni(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        first = run_node(profile.id, "Ref2VA", "raw", "16:9", 2, 128,
            system_prompt="Caption.", validation="custom", ref_0=sound(seconds=0.1), unique_id="api-audio-repeat")
        monkeypatch.setattr(config, "load_profiles", lambda: pytest.fail("Repeat read profiles"))
        current = sound(seconds=0.2)
        current["waveform"] = object()
        second = run_node("missing", "Ref2VA", "raw", "16:9", 2, 128,
            validation="custom", ref_0=current, unique_id="api-audio-repeat", repeat_last=True)
        assert first[0] == second[0] and second[-1]["audios"][0] is current and len(api.calls) == 1
        memory.forget("api-audio-repeat")
