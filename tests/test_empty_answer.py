"""A writer that answers with nothing stops the node instead of handing on "".

An empty answer used to leave as an empty prompt, and the video downstream was
made from nothing with no word said about it -- with 'self_check' off, not even
in the log. Both GGUF engines now refuse it. The binary's refusal carries the
end of its stderr, because that is the only record of how many tokens it ran.

No model is run. ``runner.run`` and the wheel's stream are stood in for; the
package is registered by hand, as in test_memory, so ComfyUI is not imported.
"""

import importlib
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

cli_engine = importlib.import_module(f"{_PKG}.cli_engine")
gguf_engine = importlib.import_module(f"{_PKG}.gguf_engine")
runner = importlib.import_module(f"{_PKG}.runner")

STDERR = (
    "0.02.239.538 I generate: n_ctx = 12288, n_batch = 2048, n_predict = 2048, n_keep = 0\n"
    "0.05.447.007 I common_perf_print:        eval time =      4.85 ms /     1 runs   "
    "(    4.85 ms per token,   206.38 tokens per second)\n"
)

GENERATION = dict(
    gpu_layers=-1, n_ctx=12288, seed=42, greedy=True, max_new_tokens=2048,
    temperature=0.7, top_p=0.8, top_k=20, repetition_penalty=1.05,
)


@pytest.fixture
def binary(monkeypatch):
    """``cli_engine.generate`` with the child process replaced by an answer."""
    answer = {"stdout": ""}
    monkeypatch.setattr(cli_engine, "render_prompt", lambda model_path, messages: "prompt")
    monkeypatch.setattr(runner, "free_comfy_vram", lambda device="auto": None)
    monkeypatch.setattr(runner, "run", lambda command, binary, report: (answer["stdout"], STDERR))

    def generate():
        return cli_engine.generate(
            binary="llama-completion.exe", model_path="writer.gguf", adapter_path=None,
            messages=[{"role": "user", "content": "x"}], **GENERATION,
        )

    return answer, generate


class Wheel:
    """A llama-cpp-python instance that streams the pieces it is given."""

    model_path = "/models/writer.gguf"

    def __init__(self, pieces):
        self.pieces = pieces

    def __call__(self, rendered, **kwargs):
        return ({"choices": [{"text": piece}]} for piece in self.pieces)


@pytest.fixture
def wheel(monkeypatch):
    monkeypatch.setattr(gguf_engine, "_render", lambda llama, messages: "prompt")
    monkeypatch.setattr(gguf_engine, "_active_loras", lambda llama: None)

    def generate(pieces):
        settings = {key: value for key, value in GENERATION.items() if key not in ("gpu_layers", "n_ctx")}
        return gguf_engine.generate(Wheel(pieces), [{"role": "user", "content": "x"}], **settings)

    return generate


def test_the_binary_answering_with_nothing_stops_the_node(binary):
    answer, generate = binary
    with pytest.raises(RuntimeError) as raised:
        generate()
    message = str(raised.value)
    assert "writer.gguf wrote nothing" in message
    assert "eval time" in message and "1 runs" in message
    assert "'greedy'" in message


def test_the_binary_answer_comes_back_as_it_was(binary):
    answer, generate = binary
    answer["stdout"] = "subject_definitions:\n<Subject 1> is a woman"
    assert generate() == "subject_definitions:\n<Subject 1> is a woman"


def test_the_wheel_ending_at_once_stops_the_node(wheel):
    with pytest.raises(RuntimeError, match="writer.gguf wrote nothing: .*not a single token"):
        wheel([])


def test_the_wheel_writing_only_blank_space_stops_the_node(wheel):
    with pytest.raises(RuntimeError, match=r"after 2 token\(s\) of blank space"):
        wheel(["\n", " "])


def test_the_wheel_answer_comes_back_stripped(wheel):
    assert wheel(["\n", "a field", " at dawn", "\n"]) == "a field at dawn"
