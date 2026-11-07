"""Streaming keeps the upstream connected until completion or cancellation."""

import asyncio
import gzip
import json
import time
import traceback
from dataclasses import replace

import pytest

from test_api_writer import SECRET, client, completion, fake_server, http_client, job, local_profile, profiles


def chunk(content=None, *, finish=None, **delta):
    if content is not None:
        delta["content"] = content
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def events(*values, done=True, newline=b"\n"):
    blocks = []
    for value in values:
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        blocks.append(b"data: " + data + newline * 2)
    if done:
        blocks.append(b"data: [DONE]" + newline * 2)
    return b"".join(blocks)


def offer(api, body, **headers):
    api.body = body
    api.headers = {"Content-Type": "text/event-stream; charset=utf-8", **headers}


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("chunk_size", [0, 1, 7])
def test_stream_reassembles_utf8_reasoning_finish_and_final_usage(newline, chunk_size):
    with fake_server() as api:
        prefix = b"\xef\xbb\xbf: keepalive" + newline * 2
        body = events(chunk(role="assistant"), chunk(reasoning_content="think "),
                      chunk(reasoning_content="separately"), chunk("a cat "), chunk("\u043a\u043e\u0442"),
                      chunk(finish="stop"), {"choices": [], "usage": {
                          "prompt_tokens": 21, "completion_tokens": 13, "total_tokens": 34}}, newline=newline)
        offer(api, prefix + body)
        api.chunk_size = chunk_size
        answer = http_client(local_profile(api)).complete(job())
        assert answer.text == "a cat \u043a\u043e\u0442" and answer.reasoning == "think separately"
        assert answer.usage.total_tokens == 34
        assert len(api.calls) == 1 and api.calls[0]["body"]["stream"] is True


def test_stream_supports_multiline_data_comments_and_unknown_sse_fields():
    with fake_server() as api:
        body = b"event: message\nid: ignored\nretry: 1000\n: ping\ndata: {\ndata: \"choices\": "
        body += json.dumps(chunk("HELLO_API", finish="stop")["choices"]).encode() + b"}\n\ndata: [DONE]\n\n"
        offer(api, body)
        answer = http_client(local_profile(api)).complete(job())
        assert answer.text == "HELLO_API" and answer.usage.total_tokens is None


def test_stream_can_carry_usage_with_the_final_choice():
    with fake_server() as api:
        final = chunk("HELLO_API", finish="stop")
        final["usage"] = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
        offer(api, events(final))
        assert http_client(local_profile(api)).complete(job()).usage.total_tokens == 5


@pytest.mark.parametrize("body,code", [
    (events(chunk("partial"), done=False), "unfinished"),
    (events(chunk("complete", finish="stop"), done=False), "unfinished"),
    (events(chunk("partial")), "unfinished"),
    (events(chunk("partial", finish="length")), "unfinished"),
    (events(chunk("refused", finish="content_filter")), "refusal"),
    (events(chunk(refusal="no")), "refusal"),
    (events(chunk(tool_calls=[{"id": "ignored", "function": {"name": "exec"}}])), "tool_call"),
    (events(chunk(function_call={"name": "exec"})), "tool_call"),
    (events(chunk(finish="tool_calls")), "tool_call"),
    (events(chunk([], finish="stop")), "protocol"),
    (events(chunk(reasoning_content=[])), "protocol"),
    (events(chunk(role="user")), "protocol"),
    (events({"choices": [chunk()["choices"][0], chunk()["choices"][0]]}), "protocol"),
    (events({"choices": [{"index": 1, "delta": {"content": "x"}}]}), "protocol"),
    (events({"choices": [{"index": True, "delta": {}}]}), "protocol"),
    (events({"choices": [{"index": 0, "delta": None}]}), "protocol"),
    (events({"choices": []}), "protocol"),
    (events({"choices": {}}), "protocol"),
    (events([1, 2]), "protocol"),
    (events(b"{bad json"), "protocol"),
    (events(b"\xff"), "protocol"),
    (events(chunk("one", finish="stop"), chunk("two")), "protocol"),
    (events(chunk(finish="stop")), "empty"),
    (events({"error": {"message": SECRET}}), "server"),
    (b"event: error\ndata: " + SECRET.encode() + b"\n\n", "server"),
])
def test_stream_never_returns_partial_refused_tool_or_malformed_answers(body, code, caplog):
    with fake_server() as api:
        offer(api, body)
        with pytest.raises(client.ApiError) as error:
            http_client(local_profile(api)).complete(job())
        assert error.value.code == code and len(api.calls) == 1
        assert error.value.__context__ is None
        assert SECRET not in "".join(traceback.format_exception(error.value)) + caplog.text


@pytest.mark.parametrize("field", ["content", "reasoning_content"])
def test_credential_echo_split_across_events_never_reaches_outputs(field):
    with fake_server() as api:
        offer(api, events(chunk(**{field: SECRET[:12]}), chunk(**{field: SECRET[12:]}),
                          chunk("final", finish="stop")))
        profile = local_profile(api, secret=profiles.SecretRef("vault", "dummy"))
        with pytest.raises(client.ApiError) as error:
            http_client(profile, authorize=lambda selected: SECRET).complete(job(profile))
        assert error.value.code == "credential_echo"
        assert SECRET not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("compressed", [False, True])
def test_stream_bounds_all_decoded_bytes_including_comments(compressed):
    with fake_server() as api:
        body = b":" + b"x" * 2000 + b"\n\n" + events(chunk("small", finish="stop"))
        offer(api, gzip.compress(body) if compressed else body,
              **({"Content-Encoding": "gzip"} if compressed else {}))
        profile = local_profile(api, limits=profiles.ProfileLimits(response_bytes=1024))
        with pytest.raises(client.ApiError) as error:
            http_client(profile).complete(job(profile))
        assert error.value.code == "response_limit" and len(api.calls) == 1


def test_stream_pings_do_not_extend_total_deadline():
    with fake_server() as api:
        offer(api, b": ping\n\n" * 500)
        api.chunk_size, api.chunk_delay = 8, 0.01
        start = time.monotonic()
        with pytest.raises(client.ApiError) as error:
            http_client(local_profile(api), timeout=(1, 1), deadline=0.15).complete(job())
        assert error.value.code == "timeout" and time.monotonic() - start < 0.9
        assert len(api.calls) == 1


@pytest.mark.parametrize("native_cancel", [False, True])
def test_cancel_closes_stream_and_next_request_on_same_client_succeeds(native_cancel):
    async def run(api):
        connection = http_client(local_profile(api))
        offer(api, events(*(chunk("story fragment ") for _ in range(2000)), done=False))
        api.chunk_size, api.chunk_delay = 64, 0.005
        start = time.monotonic()
        stop = False
        operation = asyncio.create_task(connection.complete_async(job(), cancelled=lambda: stop))
        while not api.calls:
            assert time.monotonic() - start < 2
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        cancel_started = time.monotonic()
        if native_cancel:
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            stop = True
            with pytest.raises(client.ApiError) as error:
                await operation
            assert error.value.code == "cancelled"
        assert time.monotonic() - cancel_started < 0.9
        until = time.monotonic() + 1
        while not api.disconnected.is_set() and time.monotonic() < until:
            await asyncio.sleep(0.01)
        assert api.disconnected.is_set(), "The upstream must observe the closed connection."
        offer(api, events(chunk("HELLO_API", finish="stop")))
        api.chunk_size, api.chunk_delay = 0, 0
        answer = await connection.complete_async(replace(job(), prompt="Hello again"))
        assert answer.text == "HELLO_API" and len(api.calls) == 2
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}

    with fake_server() as api:
        asyncio.run(run(api))


def test_explicit_unary_profile_still_works_without_stream_options():
    with fake_server() as api:
        api.body = completion("HELLO_API")
        profile = local_profile(api, stream=False)
        assert http_client(profile).complete(job(profile)).text == "HELLO_API"
        assert api.calls[0]["body"]["stream"] is False
        assert "stream_options" not in api.calls[0]["body"]


@pytest.mark.parametrize("value", [None, 0, 1, "true", {}, []])
def test_profile_stream_setting_is_strictly_boolean(value):
    with pytest.raises(client.ContractError, match="streaming"):
        replace(profiles.example_profiles().profiles[0], stream=value)


def test_existing_profile_without_stream_field_defaults_to_streaming():
    saved = profiles.example_profiles().profiles[0].to_dict()
    saved.pop("stream")
    restored = profiles.ApiProfile.from_dict(saved)
    assert restored.stream is True
