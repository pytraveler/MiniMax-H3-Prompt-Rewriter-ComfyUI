"""Image identities, transport contents, limits and reuse across the actual HTTP boundary."""

import asyncio
import base64
import importlib
import io
import json
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from test_api_writer import (SECRET, client, completion, contract, fake_server,
                             http_client, job, local_profile, profiles)
from test_api_text import ANSWER, SECTIONS, node_environment, run_node, writer, config

media = importlib.import_module("minimax_h3_rewriter.api_media")
memory = importlib.import_module("minimax_h3_rewriter.memory")
library = importlib.import_module("minimax_h3_rewriter.library")


def picture(colour=(1, 0, 0), *, width=128, height=64, frames=1):
    return np.broadcast_to(np.array(colour, dtype=np.float32), (frames, height, width, len(colour))).copy()


def vision(api, **options):
    return local_profile(api, capabilities=contract.ModelCapabilities(
        modalities={"text", "image"}, formats={"text", "json_schema"}), **options)


def prepared(*values, layout="{}", instructions="{}", **options):
    references = media.arrange_images({f"ref_{index}": value for index, value in enumerate(values)}, layout, instructions)
    return references, media.prepare_images(references, **options)


def sent_pixels(part):
    url = part["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    return np.array(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))))


def test_order_roles_disabled_and_original_identity_agree_at_http_boundary():
    red, green, blue = picture(), picture((0, 1, 0)), picture((0, 0, 1))
    references, copies = prepared(red, green, blue,
        layout='{"order":["ref_2","ref_1","ref_0"],"off":["ref_1"],"roles":{"ref_2":"Subject"}}',
        instructions='{"ref_2":{"text":"Keep the blue lighting.","add":true}}')
    assert references[0].value is blue and references[1].value is red
    assert [one.spec.role for one in references] == ["Subject", "Picture"]
    with fake_server() as api:
        profile = vision(api)
        request = job(profile, task="Ref2VA", references=tuple(one.spec for one in references))
        result = client.ApiClient(profiles.ProfileSet((profile,)), images=copies).complete(request)
        assert [one.label for one in result.manifest] == ["Subject 1", "Picture 1"]
        parts = api.calls[0]["body"]["messages"][1]["content"]
        assert "Keep the blue lighting." in parts[0]["text"]
        assert parts[0]["text"].endswith("original_prompt:\n" + request.prompt)
        assert [one["text"] for one in parts[1::2]] == ["<Subject 1>", "<Picture 1>"]
        assert sent_pixels(parts[2])[0, 0, 2] > 240
        assert sent_pixels(parts[4])[0, 0, 0] > 240
        assert len(api.calls) == 1


def test_resize_preserves_ratio_batch_count_and_pixels_of_original():
    value = picture(width=200, height=80, frames=3)
    original = value.copy()
    references, copies = prepared(value, max_side=128)
    assert references[0].value is value and np.array_equal(value, original)
    assert [(one.frame, one.frames, one.width, one.height) for one in copies] == [(1, 3, 128, 51), (2, 3, 128, 51), (3, 3, 128, 51)]
    assert copies[0].metadata()["resized"]
    assert (copies[0].source_width, copies[0].source_height) == (200, 80)
    profile = profiles.example_profiles().profiles[1]
    request = job(profile, task="I2VA", references=tuple(one.spec for one in references))
    parts = json.loads(client.prepare_payload(request, profile, copies))["messages"][1]["content"]
    assert len(parts) == 7 and "batch image 3 of 3" in parts[5]["text"]
    assert len(request.manifest) == 1


@pytest.mark.parametrize("colour", [(0.5,), (1, 0, 0, 0)])
def test_grayscale_and_alpha_have_explicit_rgb_transport(colour):
    _references, copies = prepared(picture(colour, width=32, height=32))
    pixels = sent_pixels(copies[0].content())
    assert pixels.shape == (32, 32, 3)
    assert pixels[0, 0, 0] >= (250 if len(colour) == 4 else 125)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_pixels_fail_without_silently_clipping(value):
    batch = picture()
    batch[0, 0, 0, 0] = value
    with pytest.raises(contract.ContractError, match="finite numbers"):
        prepared(batch)


@pytest.mark.parametrize("layout", [
    '[]', '{"order":["ref_0","ref_0"]}', '{"order":["../key"]}',
    '{"off":"ref_0"}', '{"roles":{"ref_0":"Audio"}}', '{"roles":{"ref_0":"Video"}}',
    '{"sound":["ref_0"]}', '{"url":"file:///secret"}', '{"off":[],"off":["ref_0"]}',
])
def test_bad_layouts_cannot_duplicate_media_or_choose_a_path(layout):
    with pytest.raises(contract.ContractError):
        prepared(picture(), layout=layout)


@pytest.mark.parametrize("value", ["https://example.com/image.jpg", "C:\\private\\secret.png", {"waveform": object()}, object()])
def test_only_connected_image_objects_are_accepted(value):
    with pytest.raises(contract.ContractError, match="IMAGE batch"):
        prepared(value)


def test_disabled_values_are_not_read_or_encoded():
    class Unreadable:
        @property
        def shape(self):
            pytest.fail("A disabled reference was read")

    references, copies = prepared(Unreadable(), picture(), layout='{"off":["ref_0"]}')
    assert len(references) == len(copies) == 1 and copies[0].slot == "ref_1"


def test_batch_frame_limit_and_byte_limit_are_explicit():
    with pytest.raises(contract.ContractError, match="64-image"):
        prepared(picture(frames=65))
    with pytest.raises(contract.ContractError, match="request byte limit"):
        prepared(picture(), byte_limit=100)


def test_native_copies_are_complete_and_cannot_move_to_another_label():
    references, copies = prepared(picture(frames=2), picture((0, 0, 1)))
    profile = profiles.example_profiles().profiles[1]
    request = job(profile, task="FL2VA", references=tuple(one.spec for one in references))
    for bad in ((), copies[1:], copies[::-1], copies[:1], copies + (copies[0],)):
        with pytest.raises(contract.ContractError):
            client.prepare_payload(request, profile, bad)
    with pytest.raises(contract.ContractError):
        client.prepare_payload(replace(request, task="T2VA"), profile, copies)


def test_request_size_and_modality_are_checked_before_secret_access():
    references, copies = prepared(picture())
    with fake_server() as api:
        profile = vision(api, limits=profiles.ProfileLimits(request_bytes=100),
                         secret=profiles.SecretRef("vault", "dummy"))
        request = job(profile, task="I2VA", references=tuple(one.spec for one in references))
        connection = client.ApiClient(profiles.ProfileSet((profile,)), images=copies,
            authorize=lambda _one: pytest.fail("Invalid image request accessed a key"))
        with pytest.raises(contract.ContractError, match="byte limit"):
            connection.complete(request)
        connection.profiles = profiles.ProfileSet((replace(profile, capabilities=contract.ModelCapabilities()),))
        with pytest.raises(contract.ContractError, match="does not support"):
            connection.complete(request)
        assert not api.calls


def test_cancellation_during_preparation_leaves_no_background_tasks():
    references = media.arrange_images({"ref_0": picture(frames=4)}, "{}", "{}")

    async def run():
        cancelled = False

        def check():
            return cancelled

        task = asyncio.create_task(media.prepare_images_async(references, cancelled=check))
        await asyncio.sleep(0)
        cancelled = True
        with pytest.raises(client.ApiError) as error:
            await task
        assert error.value.code == "cancelled"
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


@pytest.mark.parametrize("task,count", [("I2VA", 1), ("FL2VA", 2), ("L2VA", 1), ("Ref2VA", 2)])
def test_node_real_http_modes_guide_and_passthrough(node_environment, monkeypatch, task, count):
    directory, _messages = node_environment
    calls = []
    monkeypatch.setattr(writer.guides, "text", lambda kind, *_args: calls.append(kind) or "Dummy guide.")
    values = {f"ref_{index}": picture((0, 0, 1) if index else (1, 0, 0)) for index in range(count)}
    with fake_server() as api:
        profile = vision(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        fields = {name: "A scene." for name in writer.guide_prompt.FIELDS_FOR_MODE[task]}
        if task in writer.guide_prompt.ALIGNMENT_LINE:
            fields["alignment"] = writer.guide_prompt.ALIGNMENT_LINE[task].format(seconds="5.00").replace("Shot N", "Shot 1")
        api.body = completion(json.dumps(fields))
        output = run_node(profile.id, task, "Raw user text.", "16:9", 5, 512,
                          response_format="json_schema", **values)
        assert calls == ["ref" if task == "Ref2VA" else "base"]
        assert len(output) == len(writer.MiniMaxH3ApiUniversalWriter.RETURN_TYPES)
        assert all(output[-1]["pictures"][index] is value for index, value in enumerate(values.values()))
        report = json.loads(output[-2])
        assert report["requests"] == 1 and len(report["references"]) == len(report["media"]) == count
        assert [one["slot"] for one in report["media"]] == list(values)
        assert "data:image" not in repr(output[:-1])


def test_node_t2va_ignores_even_unreadable_media_with_notice(node_environment):
    directory, _messages = node_environment
    with fake_server() as api:
        profile = local_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        output = run_node(profile.id, "T2VA", "raw", "16:9", 5, 512,
                          system_prompt="Reply with text.", ref_0=object())
        assert isinstance(api.calls[0]["body"]["messages"][1]["content"], str)
        report = json.loads(output[-2])
        assert "ignores connected" in repr(report["notes"])
        assert not report["references"] and not report["media"] and not output[-1]["pictures"]


def test_subject_first_on_strip_follows_pictures_only_in_generator_bundle(node_environment):
    directory, _messages = node_environment
    red, blue = picture(), picture((0, 0, 1))
    with fake_server() as api:
        profile = vision(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        output = run_node(profile.id, "Ref2VA", "raw", "16:9", 5, 512,
            system_prompt="Write a caption.", validation="custom", ref_0=red, ref_1=blue,
            reference_layout='{"order":["ref_1","ref_0"],"roles":{"ref_1":"Subject"}}')
        assert output[-1]["pictures"][0] is red and output[-1]["pictures"][1] is blue
        report = json.loads(output[-2])
        assert [one["label"] for one in report["references"]] == ["Subject 1", "Picture 1"]
        assert [one["slot"] for one in report["media"]] == ["ref_1", "ref_0"]


def test_repeat_skips_config_guide_secret_and_http_but_keeps_current_media(node_environment, monkeypatch):
    directory, _messages = node_environment
    node_id = "api-image-repeat-test"
    first_picture, changed_picture = picture(), picture((0, 0, 1))
    with fake_server() as api:
        profile = vision(api, secret=profiles.SecretRef("environment", "MINIMAX_IMAGES_TEST_KEY"))
        monkeypatch.setenv("MINIMAX_IMAGES_TEST_KEY", SECRET)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        first = run_node(profile.id, "I2VA", "raw", "16:9", 5, 512,
            system_prompt="Return a plain caption.", validation="custom", unique_id=node_id, ref_0=first_picture)
        assert len(api.calls) == 1
        record = memory.recall(node_id)
        assert SECRET not in repr(record) and "Return a plain caption" not in repr(record.about)
        monkeypatch.setattr(config, "load_profiles", lambda: pytest.fail("Repeat accessed profiles"))
        monkeypatch.setattr(writer.guides, "text", lambda *_args: pytest.fail("Repeat accessed guides"))
        repeated = run_node("missing-profile", "I2VA", "changed", "16:9", 5, 512,
            system_prompt="different", repeat_last=True, unique_id=node_id, ref_0=changed_picture)
        assert repeated[0] == first[0] and repeated[-1]["pictures"][0] is changed_picture
        assert json.loads(repeated[-2])["requests"] == 0 and len(api.calls) == 1
        memory.forget(node_id)


def test_profile_listing_does_not_resolve_or_export_credential_sources(node_environment):
    directory, _messages = node_environment
    configured = profiles.example_profiles()
    config.save_profiles(configured, directory)
    listing = config.public_profiles()
    assert listing[1]["base_url"] == "https://api.deepseek.com" and listing[1]["modalities"] == ["image", "text"]
    assert all(set(one) == {"id", "name", "base_url", "model", "network", "modalities"} for one in listing)
    assert "vault" not in repr(listing) and "environment" not in repr(listing)


def test_saved_library_prompt_and_edited_session_reuse_never_call_http(node_environment, monkeypatch):
    directory, _messages = node_environment
    node_id = "api-image-library-test"
    with fake_server() as api:
        profile = vision(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        first = run_node(profile.id, "I2VA", "private raw request", "16:9", 5, 512,
            system_prompt="private system text", validation="custom", unique_id=node_id, ref_0=picture())
        record = memory.recall(node_id)
        saved = library.add("global", library.from_record(record, "API test", "", []))
        contents = json.dumps(library.load("global"))
        assert "private raw request" not in contents and "private system text" not in contents
        assert "base_url" not in contents and "data:image/jpeg" not in contents
        pick = json.dumps({"id": saved["id"], "file": "global"})
        monkeypatch.setattr(config, "load_profiles", lambda: pytest.fail("Library reuse accessed profiles"))
        result = run_node("missing", "I2VA", "unused", "16:9", 5, 512,
            validation="custom", repeat_last=True, library_pick=pick, ref_0=picture())
        assert result[0] == first[0]
        assert json.loads(result[-2])["source"] == "library"
        memory.rewrite(node_id, "An edited caption.")
        result = run_node("missing", "I2VA", "unused", "16:9", 5, 512,
            repeat_last=True, unique_id=node_id, ref_0=picture())
        assert result[0] == "An edited caption." and len(api.calls) == 1
        assert json.loads(result[-2])["references"][0]["label"] == "Picture 1"
        memory.forget(node_id)


def test_repair_uses_identical_images_and_keeps_raw_prompt(node_environment, monkeypatch):
    directory, _messages = node_environment
    with fake_server() as api:
        profile = vision(api)
        config.save_profiles(profiles.ProfileSet((profile,)), directory)
        api.body = completion(ANSWER)
        run_node(profile.id, "I2VA", "original user request", "16:9", 5, 512,
            system_prompt="Write the H3 fields.", repair_once=True, ref_0=picture())
        assert len(api.calls) == 2
        first, second = [one["body"]["messages"][1]["content"] for one in api.calls]
        assert first[1:] == second[1:]
        assert "original_prompt:\noriginal user request" in first[0]["text"]
        assert "original_prompt:\noriginal user request" in second[0]["text"]


def test_existing_api_widget_and_output_prefixes_keep_their_serialized_positions(node_environment):
    schema = writer.MiniMaxH3ApiUniversalWriter.INPUT_TYPES()
    assert list(schema["required"]) == ["profile_id", "task", "prompt", "resolution", "duration", "max_tokens"]
    assert list(schema["optional"])[:9] == ["system_prompt", "validation", "response_format", "temperature", "seed",
        "timeout_seconds", "repair_once", "auto_download_guide", "reference_layout"]
    assert writer.MiniMaxH3ApiUniversalWriter.RETURN_NAMES[:-1] == ("prompt",) + writer.ALL_FIELDS + ("reasoning", "findings", "report")
