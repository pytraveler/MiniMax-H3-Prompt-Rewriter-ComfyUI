"""Thinking control at the local binary, routing and saved Options boundaries."""

import importlib
import io
import os
import pathlib
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
ROOT = pathlib.Path(__file__).resolve().parent.parent
if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(ROOT / _PKG)]
    sys.modules[_PKG] = package

flags = importlib.import_module(f"{_PKG}.llama_flags")
mtmd = importlib.import_module(f"{_PKG}.mtmd_engine")
server = importlib.import_module(f"{_PKG}.server_engine")


class HelpProcess:
    def __init__(self, text, code=0):
        self.stdout = io.BytesIO(text)
        self.stderr = io.BytesIO()
        self.returncode = code
        self.killed = False

    def communicate(self, timeout):
        return self.stdout.getvalue(), self.stderr.getvalue()

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1

    def wait(self, timeout=0):
        return self.returncode


@pytest.fixture(autouse=True)
def clean_probe(monkeypatch):
    flags._probe.cache_clear()
    monkeypatch.setattr(flags.runner, "interrupted", lambda: False)
    monkeypatch.delenv(server.CHOICE_ENV, raising=False)
    yield
    flags._probe.cache_clear()


@pytest.fixture
def probe(tmp_path, monkeypatch):
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"version one")
    calls = []

    def install(text, code=0):
        def spawn(command, executable):
            assert command == [str(binary), "--help"] and executable == str(binary)
            process = HelpProcess(text, code)
            flags.runner._LIVE.add(process)
            calls.append(process)
            return process
        monkeypatch.setattr(flags.runner, "spawn", spawn)

    return binary, calls, install


def test_probe_reads_advertised_flags_and_releases_the_child(probe):
    binary, calls, install = probe
    install(b" -rea, --reasoning [on|off|auto]\n --jinja, --no-jinja   enable templates\n --reasoning-format FORMAT\n")
    assert flags.supported(str(binary)) == {"--reasoning", "--jinja", "--reasoning-format"}
    assert len(calls) == 1 and calls[0] not in flags.runner._LIVE
    assert calls[0].stdout.closed and calls[0].stderr.closed


def test_help_is_cached_but_replacing_the_binary_invalidates_it(probe):
    binary, calls, install = probe
    install(b"--reasoning MODE\n")
    assert "--reasoning" in flags.supported(str(binary))
    assert "--reasoning" in flags.supported(str(binary))
    assert len(calls) == 1
    stamp = binary.stat()
    os.utime(binary, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
    install(b"--old-option VALUE\n")
    assert flags.supported(str(binary)) == {"--old-option"}
    assert len(calls) == 2


@pytest.mark.parametrize("text, code", [
    (b"--reasoning-format FORMAT\n", 0),
    (b"  A description mentions --reasoning off.\n", 0),
    (b"--reasoning MODE\n", 1),
])
def test_a_mention_or_failed_help_is_not_support(probe, text, code):
    binary, _calls, install = probe
    install(text, code)
    assert "--reasoning" not in flags.supported(str(binary))


def test_probe_failure_preserves_older_binary_compatibility(probe, monkeypatch):
    binary, _calls, _install = probe
    def fail(*_args):
        raise OSError("missing dependency")
    monkeypatch.setattr(flags.runner, "spawn", fail)
    assert flags.supported(str(binary)) == frozenset()
    assert flags.supported(str(binary.parent / "missing")) == frozenset()


def test_a_stalled_help_is_killed_and_not_left_in_live_children(probe, monkeypatch):
    binary, calls, install = probe
    install(b"")
    monkeypatch.setattr(flags, "HELP_SECONDS", 0)
    original = HelpProcess.poll
    monkeypatch.setattr(HelpProcess, "poll", lambda self: None if not self.killed else original(self))
    assert not flags.supported(str(binary))
    assert calls[0].killed and calls[0] not in flags.runner._LIVE
    assert calls[0].stdout.closed


def test_interrupt_during_help_reaps_the_child_and_is_not_cached(probe, monkeypatch):
    binary, calls, install = probe
    install(b"--reasoning MODE\n")
    class Cancelled(Exception):
        pass
    count = 0
    def check():
        nonlocal count
        count += 1
        if count == 2:
            raise Cancelled()
    monkeypatch.setattr(flags, "check_interrupt", check)
    with pytest.raises(Cancelled):
        flags._probe(str(binary), 0, 0)
    assert calls[0] not in flags.runner._LIVE and calls[0].stdout.closed
    assert flags._probe.cache_info().currsize == 0


@pytest.mark.parametrize("thinking", ["off", "on"])
def test_explicit_thinking_uses_only_supported_arguments(monkeypatch, thinking):
    monkeypatch.setattr(flags, "supported", lambda _binary: {"--reasoning", "--jinja"})
    assert flags.reasoning_arguments("binary", thinking) == ["--jinja", "--reasoning", thinking]
    command = server.build_command("binary", "m.gguf", "p.gguf", 1234, 0, 8192, thinking=thinking)
    assert command[-3:] == ["--jinja", "--reasoning", thinking]


def test_reasoning_format_does_not_count_as_thinking_control(monkeypatch, caplog):
    monkeypatch.setattr(flags, "supported", lambda _binary: {"--reasoning-format"})
    assert flags.reasoning_arguments("old-binary", "off") == []
    assert "Using the model template default" in caplog.text


def test_auto_never_probes_or_adds_flags(monkeypatch):
    monkeypatch.setattr(flags, "supported", lambda _binary: pytest.fail("Writer default probed help"))
    assert flags.reasoning_arguments("binary") == []
    assert "--reasoning" not in server.build_command("binary", "m", "p", 1234, 0, 8192)
    assert "--reasoning" not in mtmd.build_command("binary", "m", "p", "Describe", [("image", "x")], 0)


@pytest.mark.parametrize("value", ["false", "OFF", "", None, True])
def test_unknown_modes_are_refused(value):
    with pytest.raises(ValueError, match="captioner_thinking"):
        flags.thinking_mode(value)


class CaptionServer:
    binary = "server"
    n_ctx = 8192

    def __init__(self):
        self.closed = False
        self.calls = []
        self.text = "A black cat sits on a fence."

    def close(self):
        self.closed = True

    def ask(self, *args):
        self.calls.append(args)
        return self.text


@pytest.fixture
def routing(monkeypatch):
    state = {"ensured": [], "started": [], "cli_flags": set(), "server_flags": {"--reasoning"}}
    held = CaptionServer()
    def ensure(*_args):
        state["ensured"].append(True)
        return "cli"
    def start(*args, **kwargs):
        state["started"].append((args, kwargs))
        return held
    monkeypatch.setattr(mtmd.llamacpp, "ensure_mtmd", ensure)
    monkeypatch.setattr(mtmd.llamacpp, "server_beside", lambda _binary: "server")
    monkeypatch.setattr(flags, "supported", lambda binary: state["cli_flags"] if binary == "cli" else state["server_flags"])
    monkeypatch.setattr(mtmd.server_engine, "open_server", start)
    monkeypatch.setattr(mtmd, "fit_context", lambda *_args: 8192)
    monkeypatch.setattr(mtmd.runner, "free_comfy_vram", lambda *_args: None)
    return state, held


@pytest.mark.parametrize("thinking", ["off", "on"])
def test_one_reference_uses_a_server_when_only_it_can_set_thinking(routing, thinking):
    state, held = routing
    with mtmd.session("model", "projector", 1, thinking=thinking) as selected:
        assert selected is held and not held.closed
    assert held.closed
    assert state["started"][0][1]["thinking"] == thinking


def test_one_reference_uses_future_cli_control_without_starting_a_server(routing):
    state, _held = routing
    state["cli_flags"] = {"--reasoning"}
    with mtmd.session("model", "projector", 1, thinking="off") as selected:
        assert selected is None
    assert not state["started"]
    command = mtmd.build_command("cli", "model", "projector", "Describe", [("image", "x")], 0, thinking="off")
    assert command[command.index("--reasoning") + 1] == "off"


@pytest.mark.parametrize("thinking, assets", [("auto", 1), ("off", 0), ("on", 0)])
def test_default_single_reference_and_empty_runs_do_not_start_a_server(routing, thinking, assets):
    state, _held = routing
    with mtmd.session("model", "projector", assets, thinking=thinking) as selected:
        assert selected is None
    assert not state["started"] and not state["ensured"]


def test_never_is_respected_even_when_a_server_could_disable_thinking(routing, monkeypatch):
    state, _held = routing
    monkeypatch.setenv(server.CHOICE_ENV, "never")
    with mtmd.session("model", "projector", 3, thinking="off") as selected:
        assert selected is None
    assert not state["started"] and not state["ensured"]


@pytest.mark.parametrize("missing", ["server", "flag"])
def test_older_cli_without_a_suitable_server_keeps_the_original_route(routing, monkeypatch, missing):
    state, _held = routing
    if missing == "server":
        monkeypatch.setattr(mtmd.llamacpp, "server_beside", lambda _binary: "")
    else:
        state["server_flags"] = set()
    with mtmd.session("model", "projector", 1, thinking="off") as selected:
        assert selected is None
    assert not state["started"]


def test_several_references_still_load_only_one_model(routing):
    state, held = routing
    with mtmd.session("model", "projector", 3, thinking="off") as selected:
        for _ in range(3):
            assert mtmd.describe("model", "projector", "Describe", attachments=[("image", "x")],
                                 server=selected, allow_server=False, thinking="off") == held.text
    assert len(state["started"]) == 1 and len(held.calls) == 3 and held.closed


def test_single_caption_reaches_the_server_and_releases_it(routing):
    state, held = routing
    assert mtmd.describe("model", "projector", "Describe", attachments=[("image", "x")], thinking="off") == held.text
    assert held.closed and len(state["started"]) == 1 and len(held.calls) == 1


def test_interrupt_in_single_caption_releases_the_server_without_retry(routing, monkeypatch):
    state, held = routing
    class Cancelled(Exception):
        pass
    def fail(*_args):
        raise Cancelled()
    monkeypatch.setattr(held, "ask", fail)
    with pytest.raises(Cancelled):
        mtmd.describe("model", "projector", "Describe", attachments=[("image", "x")], thinking="off")
    assert held.closed and len(state["started"]) == 1


def test_failed_batch_start_is_not_repeated_for_every_reference(routing, monkeypatch, caplog):
    state, _held = routing
    def fail(*args, **kwargs):
        state["started"].append(True)
        return None
    monkeypatch.setattr(mtmd.server_engine, "open_server", fail)
    monkeypatch.setattr(mtmd.runner, "run", lambda *_args: ("A cat.", ""))
    with mtmd.session("model", "projector", 3, thinking="off") as selected:
        for _ in range(3):
            assert mtmd.describe("model", "projector", "Describe", attachments=[("image", "x")],
                                 server=selected, allow_server=False, thinking="off") == "A cat."
    assert len(state["started"]) == 1
    assert "Using the model template default" in caplog.text


def test_interrupt_while_starting_a_server_closes_it(monkeypatch):
    state = []
    class Cancelled(Exception):
        pass
    monkeypatch.setattr(server.Server, "start", lambda self, **_kwargs: (_ for _ in ()).throw(Cancelled()))
    monkeypatch.setattr(server.Server, "close", lambda self: state.append("closed"))
    with pytest.raises(Cancelled):
        server.open_server("binary", "model", "projector", 0, 8192)
    assert state == ["closed"]


def test_options_append_the_control_and_old_values_keep_their_positions():
    nodes = importlib.import_module(f"{_PKG}.nodes")
    old_order = ["max_new_tokens", "temperature", "top_p", "top_k", "repetition_penalty",
                 "attn_implementation", "adapter", "use_lora", "merge_lora", "auto_download",
                 "gpu_layers", "n_ctx", "gguf_runtime", "device", "llama_backend", "prompt_file",
                 "self_check", "fix_once", "downloader"]
    inputs = nodes.MiniMaxH3RewriterOptions.INPUT_TYPES()
    schema = inputs["required"] | inputs["optional"]
    assert list(schema) == old_order + ["captioner_thinking"]
    assert schema["captioner_thinking"][1]["default"] == "off"
    options, = nodes.MiniMaxH3RewriterOptions().build(max_new_tokens=512)
    assert options["captioner_thinking"] == "off" and options["max_new_tokens"] == 512


@pytest.fixture
def reference_node(monkeypatch):
    nodes = importlib.import_module(f"{_PKG}.nodes")
    class Progress:
        def __init__(self, *_args):
            pass
        def finish(self, *_args):
            pass
    monkeypatch.setattr(nodes, "NodeProgress", Progress)
    monkeypatch.setattr(nodes, "_resolve_captioner_choice", lambda _choice: types.SimpleNamespace(
        local=True, reference="model", mmproj="projector"))
    monkeypatch.setattr(nodes.discovery, "gguf_header", lambda _path: {"vision": True, "audio": True})
    monkeypatch.setattr(nodes.memory, "repeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(nodes.memory, "keep", lambda *_args, **_kwargs: None)
    return nodes


@pytest.mark.parametrize("mode", [None, "off", "auto", "on"])
def test_reference_node_passes_the_control_without_raising_the_caption_budget(reference_node, monkeypatch, mode):
    seen = {}
    def describe(**kwargs):
        seen.update(kwargs)
        return "A red square."
    monkeypatch.setattr(mtmd, "describe", describe)
    options = {"max_new_tokens": 8192}
    if mode is not None:
        options["captioner_thinking"] = mode
    result = reference_node.MiniMaxH3ReferenceCaption().describe(
        "Picture", "model", "brief", 42, image=object(), options=options)
    assert result[1] == "A red square."
    assert seen["thinking"] == (mode or "off")
    assert seen["max_new_tokens"] == 1024


@pytest.mark.parametrize("answer", ["", " \n ", "<think>unfinished"])
def test_empty_gguf_caption_does_not_create_an_empty_reference_line(reference_node, monkeypatch, answer):
    monkeypatch.setattr(mtmd, "describe", lambda **_kwargs: mtmd.answer_only(answer))
    with pytest.raises(RuntimeError, match="empty description"):
        reference_node.MiniMaxH3ReferenceCaption().describe("Picture", "model", "brief", 42, image=object())


def test_clip_route_stays_separate_from_gguf_thinking_controls(reference_node, monkeypatch):
    monkeypatch.setattr(reference_node.clip_caption, "check", lambda *_args: None)
    monkeypatch.setattr(reference_node.clip_caption, "describe", lambda *_args, **_kwargs: "A red square.")
    monkeypatch.setattr(mtmd, "describe", lambda **_kwargs: pytest.fail("CLIP invoked the GGUF captioner"))
    result = reference_node.MiniMaxH3ReferenceCaption().describe(
        "Picture", "model", "brief", 42, image=object(), clip=object(), options={"captioner_thinking": "on"})
    assert result[1] == "A red square."
