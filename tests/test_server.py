"""The captioning server: what it sends, what it starts, and when it declines.

None of this loads a model. What is worth pinning down here is the wire format
and the fallbacks -- the parts that are wrong silently. A misspelled content
part comes back as a refusal from llama.cpp that reads like a broken model, and
a fallback that raises instead of returning turns a speed-up into an outage.

The package is registered by hand, as in test_memory: importing it for real
would run ``__init__.py`` and pull in ComfyUI.
"""

import base64
import importlib
import json
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

server_engine = importlib.import_module(f"{_PKG}.server_engine")
llamacpp = importlib.import_module(f"{_PKG}.llamacpp")
mtmd_engine = importlib.import_module(f"{_PKG}.mtmd_engine")


@pytest.fixture(autouse=True)
def unset(monkeypatch):
    monkeypatch.delenv(server_engine.CHOICE_ENV, raising=False)


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "frame_000.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n and then some")
    return str(path)


@pytest.fixture
def sound(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")
    return str(path)


def test_one_asset_is_not_worth_a_server():
    assert not server_engine.wanted(1)


def test_two_are():
    assert server_engine.wanted(2)


def test_nothing_to_describe_wants_nothing():
    assert not server_engine.wanted(0)


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv(server_engine.CHOICE_ENV, "never")
    assert not server_engine.wanted(9)


def test_it_can_be_switched_on_for_one(monkeypatch):
    monkeypatch.setenv(server_engine.CHOICE_ENV, "always")
    assert server_engine.wanted(1)


def test_case_and_spacing_are_forgiven(monkeypatch):
    monkeypatch.setenv(server_engine.CHOICE_ENV, "  NEVER ")
    assert not server_engine.wanted(9)


def test_a_typo_falls_back_to_auto_rather_than_off(monkeypatch):
    """The failure mode of guessing 'off' is a silent loss of the whole feature."""
    monkeypatch.setenv(server_engine.CHOICE_ENV, "yes")
    assert server_engine.wanted(4)


def test_a_strip_of_pictures_needs_room_for_one():
    """Six photographs are six requests, never one, so the sixth pays nothing."""
    assert mtmd_engine.busiest(["image", "image", "image", "audio"], 8) == 1


def test_a_clip_needs_room_for_all_its_frames():
    assert mtmd_engine.busiest(["image", "video"], 8) == 8


def test_the_busiest_asset_decides_not_the_last_one():
    assert mtmd_engine.busiest(["video", "image"], 8) == 8


def test_nothing_connected_still_asks_for_a_number():
    assert mtmd_engine.busiest([]) == 1


def test_it_reads_a_generator_once():
    """The callers pass a generator expression over the strip."""
    assert mtmd_engine.busiest(kind for kind in ("image", "video")) > 1


def test_the_instruction_comes_after_the_media(picture):
    parts = server_engine.content_parts("what is this", [("image", picture)])
    assert [part["type"] for part in parts] == ["image_url", "text"]
    assert parts[-1]["text"] == "what is this"


def test_a_picture_travels_as_a_data_uri(picture):
    part = server_engine.content_parts("x", [("image", picture)])[0]
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == pathlib.Path(picture).read_bytes()


def test_a_sound_travels_as_input_audio(sound):
    """The part name and the format string are llama.cpp's, not a guess.

    ``llama-server`` rejects anything else with "input_audio.format must be
    either 'wav' or 'mp3'", and the pack writes WAV.
    """
    part = server_engine.content_parts("x", [("audio", sound)])[0]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["format"] == "wav"
    assert base64.b64decode(part["input_audio"]["data"]) == pathlib.Path(sound).read_bytes()


def test_frame_order_is_kept(tmp_path):
    """A clip's frames are chronological, so the parts have to stay in order."""
    paths = []
    for index in range(4):
        path = tmp_path / f"frame_{index:03d}.png"
        path.write_bytes(f"frame {index}".encode())
        paths.append(str(path))
    parts = server_engine.content_parts("x", [("image", path) for path in paths])
    sent = [
        base64.b64decode(part["image_url"]["url"].split(",", 1)[1]).decode()
        for part in parts[:-1]
    ]
    assert sent == ["frame 0", "frame 1", "frame 2", "frame 3"]


def test_an_unknown_kind_is_refused(picture):
    with pytest.raises(ValueError, match="unknown attachment kind"):
        server_engine.content_parts("x", [("hologram", picture)])


def command(**extra):
    settings = dict(
        binary="llama-server", model_path="m.gguf", mmproj_path="p.gguf",
        port=1234, gpu_layers=-1, n_ctx=8192,
    )
    settings.update(extra)
    return server_engine.build_command(**settings)


def test_the_model_and_projector_are_decided_once():
    built = command()
    assert built[built.index("--mmproj") + 1] == "p.gguf"
    assert built[built.index("--ctx-size") + 1] == "8192"
    assert built[built.index("--port") + 1] == "1234"
    assert built[built.index("--host") + 1] == server_engine.HOST


def test_it_only_ever_listens_locally():
    assert server_engine.HOST == "127.0.0.1"


def test_per_request_settings_are_not_on_the_command_line():
    """Sampling, the seed and the token ceiling belong to the ask, not the model."""
    built = command()
    for flag in ("--temp", "--seed", "--predict", "--top-p", "--top-k", "--prompt"):
        assert flag not in built, f"{flag} would freeze for every reference"


def test_all_layers_when_the_node_said_use_the_card():
    assert command(gpu_layers=-1)[command().index("--n-gpu-layers") + 1] == "999"


def test_the_cpu_gets_no_layers():
    built = command(device="cpu")
    assert built[built.index("--n-gpu-layers") + 1] == "0"


def test_an_adapter_is_attached_when_there_is_one():
    assert "--lora" not in command()
    assert command(adapter_path="l.gguf")[-1] == "l.gguf"


def delta(payload):
    return server_engine._delta(f"data: {json.dumps(payload)}\n".encode())


def test_a_token_is_read_out_of_its_line():
    assert delta({"choices": [{"delta": {"content": "Hel"}}]}) == "Hel"


def test_the_end_marker_is_not_text():
    assert server_engine._delta(b"data: [DONE]\n") == ""


def test_a_keepalive_is_not_text():
    assert server_engine._delta(b"\n") == ""
    assert server_engine._delta(b": ping\n") == ""


def test_a_line_that_is_not_json_costs_nothing():
    assert server_engine._delta(b"data: {half") == ""


def test_a_choice_with_no_delta_costs_nothing():
    assert delta({"choices": [{"finish_reason": "stop"}]}) == ""
    assert delta({}) == ""


def test_no_binary_is_not_an_error():
    assert server_engine.open_server("", "m.gguf", "p.gguf", -1, 8192) is None


def test_a_binary_that_is_not_there_is_not_an_error(tmp_path):
    """Falling back costs the run its speed. Raising would cost it the captions."""
    missing = str(tmp_path / "llama-server.exe")
    assert server_engine.open_server(missing, "m.gguf", "p.gguf", -1, 8192) is None


def test_the_port_is_one_nothing_holds():
    first = server_engine.free_port()
    assert 1024 < first < 65536
    assert first != server_engine.free_port()


def test_closing_a_server_that_never_started():
    server_engine.Server("llama-server", [], 1234).close()


def test_the_server_is_only_looked_for_beside_the_captioner():
    assert llamacpp.server_beside("") == ""


def test_it_is_found_beside_the_captioner(tmp_path):
    (tmp_path / llamacpp.MTMD_BINARIES[0]).write_bytes(b"")
    (tmp_path / llamacpp.SERVER_BINARIES[0]).write_bytes(b"")
    found = llamacpp.server_beside(str(tmp_path / llamacpp.MTMD_BINARIES[0]))
    assert found == str(tmp_path / llamacpp.SERVER_BINARIES[0])


def test_a_build_without_one_says_so_quietly(tmp_path):
    (tmp_path / llamacpp.MTMD_BINARIES[0]).write_bytes(b"")
    assert llamacpp.server_beside(str(tmp_path / llamacpp.MTMD_BINARIES[0])) == ""


def body(**extra):
    settings = dict(instruction="what is this", attachments=[], system_prompt="")
    settings.update(extra)
    return server_engine.request_body(**settings)


def test_a_system_turn_is_sent_when_there_is_one():
    messages = body(system_prompt="be brief")["messages"]
    assert messages[0] == {"role": "system", "content": "be brief"}
    assert messages[1]["role"] == "user"


def test_no_system_turn_is_invented_here():
    """The default belongs to describe, which has to apply it to both paths."""
    assert [message["role"] for message in body()["messages"]] == ["user"]


def test_greedy_means_temperature_zero_and_no_other_sampler():
    asked = body(greedy=True, temperature=0.9, top_p=0.5, top_k=99)
    assert asked["temperature"] == 0.0
    assert "top_p" not in asked and "top_k" not in asked


def test_sampling_travels_when_it_is_not_greedy():
    asked = body(greedy=False, temperature=0.9, top_p=0.5, top_k=99)
    assert (asked["temperature"], asked["top_p"], asked["top_k"]) == (0.9, 0.5, 99)


def test_the_seed_and_the_ceiling_are_per_request():
    asked = body(seed=7, max_new_tokens=123)
    assert asked["seed"] == 7 and asked["max_tokens"] == 123


def test_a_description_states_its_system_turn_rather_than_leaving_it_out():
    """Left out, the binary and the server build different conversations.

    Measured on Qwen2.5-Omni-3B at the same seed and sampling: one described a
    sine sweep as "a variety of synthesized sounds", the other as "a descending
    sweep". Naming the turn is what makes the two paths agree; no value of it
    reproduces either program's own default in the other.
    """
    assert mtmd_engine.DEFAULT_SYSTEM


def test_no_opinion_and_a_deliberate_none_are_not_the_same_thing():
    """A captioner says nothing; a rewriter whose guide has no system turn says "".

    Collapsing them with ``or`` would put a generic assistant persona in front
    of the 8B and Omni rewriters, which were written to have none.
    """
    import inspect

    signature = inspect.signature(mtmd_engine.describe)
    assert signature.parameters["system_prompt"].default is None
