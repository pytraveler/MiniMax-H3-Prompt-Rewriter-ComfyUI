"""The Qwen node owns its server and never leaves it behind after failure."""

import json
import threading
from types import SimpleNamespace

import pytest

from minimax_h3_rewriter import qwen_engine as engine
from minimax_h3_rewriter.qwen_options import QwenOptions


@pytest.fixture
def servers(monkeypatch):
    made = []

    class Server:
        def __init__(self, *args):
            self.closed = 0
            self.started = 0
            self.command = args[1]
            made.append(self)
        def start(self, **_kwargs):
            self.started += 1
        def _healthy(self):
            return not self.closed
        def close(self):
            self.closed += 1

    monkeypatch.setattr(engine, "_SERVER", None)
    monkeypatch.setattr(engine, "_KEY", None)
    monkeypatch.setattr(engine.llamacpp, "ensure_mtmd", lambda *_args: "mtmd")
    monkeypatch.setattr(engine.llamacpp, "server_beside", lambda _path: "server")
    monkeypatch.setattr(engine.runner, "free_comfy_vram", lambda *_args: None)
    monkeypatch.setattr(engine.engine, "unload", lambda: None)
    monkeypatch.setattr(engine.server_engine, "free_port", lambda: 12345)
    monkeypatch.setattr(engine.server_engine, "build_command", lambda *args: list(args))
    monkeypatch.setattr(engine.server_engine, "Server", Server)
    monkeypatch.setattr(engine, "_ask", lambda *_args: ("final", "thought", {"completion_tokens": 2}))
    yield made
    engine.unload()


def generate(keep=True, settings=None):
    return engine.generate(SimpleNamespace(fmt="gguf"), ("model.gguf", "mmproj.gguf"), [], [],
                           settings or QwenOptions(), 42, True, keep, SimpleNamespace(text=lambda *_a, **_k: None))


def test_kept_server_is_reused_without_another_load(servers):
    assert generate()[0] == "final"
    assert generate()[0] == "final"
    assert len(servers) == 1
    assert servers[0].started == 1 and servers[0].closed == 0
    assert engine.holding() == "model.gguf"


def test_changed_context_closes_the_previous_owned_server(servers):
    generate()
    generate(settings=QwenOptions(context_size=65536))
    assert len(servers) == 2
    assert servers[0].closed == 1 and servers[1].closed == 0


def test_unkept_server_is_closed_even_after_a_success(servers):
    generate(keep=False)
    assert servers[0].closed == 1
    assert engine.holding() == ""


def test_failed_request_cannot_leave_a_kept_server_running(servers, monkeypatch):
    def fail(*_args):
        raise RuntimeError("Request failed")
    monkeypatch.setattr(engine, "_ask", fail)
    with pytest.raises(RuntimeError, match="Request failed"):
        generate(keep=True)
    assert servers[0].closed == 1 and engine.holding() == ""


def test_loading_failure_closes_the_owned_server(servers, monkeypatch):
    def fail(_self, **_kwargs):
        raise RuntimeError("Load failed")
    monkeypatch.setattr(engine.server_engine.Server, "start", fail)
    with pytest.raises(RuntimeError, match="Load failed"):
        generate()
    assert servers[0].closed == 1 and engine.holding() == ""


def test_sse_reasoning_stays_separate_and_the_cancellation_watcher_ends(monkeypatch):
    sent = []
    class Answer:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def __iter__(self):
            for event in ({"choices": [{"delta": {"reasoning_content": "A thought."}}]},
                          {"choices": [{"delta": {"content": "The final prompt."}, "finish_reason": "stop"}]},
                          {"choices": [], "usage": {"completion_tokens": 11}}):
                yield b"data: " + json.dumps(event).encode() + b"\n"
            yield b"data: [DONE]\n"
    def request(call, **_kwargs):
        sent.append(json.loads(call.data))
        return Answer()
    monkeypatch.setattr(engine.urllib.request, "urlopen", request)
    monkeypatch.setattr(engine.runner, "interrupted", lambda: False)
    server = SimpleNamespace(base="http://127.0.0.1:12345", close=lambda: pytest.fail("Closed an active healthy server"))
    result = engine._ask(server, [{"role": "user", "content": [{"type": "text", "text": "Write."}]}], [],
                         QwenOptions(thinking=True), 42, False, SimpleNamespace(text=lambda *_args: None))
    assert result == ("The final prompt.", "A thought.", {"finish_reason": "stop", "completion_tokens": 11})
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert sent[0]["max_tokens"] == 16384
    assert not any(thread.name == "h3-qwen-cancel" for thread in threading.enumerate())
