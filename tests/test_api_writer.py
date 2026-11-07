"""API writer stage 1: real HTTP on loopback, with no models or real keys.

The fake server records what crossed the boundary. Failed validation must not
contact it; redirects must not reach a second recipient; errors must not expose
the known test credential even when the server echoes it back.
"""

import contextlib
import importlib
import json
import pathlib
import ssl
import socket
import sys
import threading
import time
import traceback
import types
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

_PKG = "minimax_h3_rewriter"
ROOT = pathlib.Path(__file__).resolve().parent.parent
if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(ROOT / _PKG)]
    sys.modules[_PKG] = package

contract = importlib.import_module(f"{_PKG}.api_contract")
profiles = importlib.import_module(f"{_PKG}.api_profiles")
client = importlib.import_module(f"{_PKG}.api_client")
references = importlib.import_module(f"{_PKG}.references")

SECRET = "stage-1-test-secret-DO-NOT-EXPORT"
ANSWER = "integrated_multimodal_description: A quiet street.\noverall_soundscape: Wind.\nnon_diegetic_music: N/A"


def completion(text=ANSWER, *, finish="stop", **message):
    return {"choices": [{"message": {"role": "assistant", "content": text, **message}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 13, "total_tokens": 34}}


@contextlib.contextmanager
def fake_server():
    state = types.SimpleNamespace(status=200, body=completion(), headers={}, calls=[], delay=0.0,
                                  chunk_size=0, chunk_delay=0.0, disconnected=threading.Event())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            state.calls.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(data)})
            time.sleep(state.delay)
            body = state.body if isinstance(state.body, bytes) else json.dumps(state.body).encode("utf-8")
            try:
                self.send_response(state.status)
                self.send_header("Content-Type", state.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(body)))
                for key, value in state.headers.items():
                    if key.lower() != "content-type":
                        self.send_header(key, value)
                self.end_headers()
                if state.chunk_size:
                    for offset in range(0, len(body), state.chunk_size):
                        self.wfile.write(body[offset:offset + state.chunk_size])
                        self.wfile.flush()
                        time.sleep(state.chunk_delay)
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                state.disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def api():
    with fake_server() as state:
        yield state


def local_profile(api, **changes):
    return replace(profiles.example_profiles().profiles[0], base_url=api.url, **changes)


def job(profile=None, **changes):
    return contract.make_job(profile_id=profile.id if profile else "llama-server-local",
                             prompt="  A cat.\nKeep this wording.  ", guide="Test guide", **changes)


def http_client(profile, **options):
    return client.ApiClient(profiles.ProfileSet((profile,)), **options)


def test_two_templates_are_nonsecret_and_roundtrip():
    offered = profiles.example_profiles()
    packaged = json.loads((ROOT / _PKG / "api_profiles.example.json").read_text(encoding="utf-8"))
    assert profiles.ProfileSet.from_dict(packaged) == offered
    assert profiles.ProfileSet.from_dict(json.loads(json.dumps(offered.to_dict()))) == offered
    local, deepseek = offered.profiles
    assert local.completions_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert local.secret.kind == "none" and local.capabilities.modalities == {"text"}
    assert deepseek.completions_url == "https://api.deepseek.com/chat/completions"
    assert deepseek.model == "deepseek-flash" and deepseek.secret == profiles.SecretRef("vault", "deepseek")
    assert "image" in deepseek.capabilities.modalities and "json_schema" not in deepseek.capabilities.formats


@pytest.mark.parametrize("url,network", [
    ("http://api.deepseek.com", "cloud"), ("https://user:key@api.deepseek.com", "cloud"),
    ("https://api.deepseek.com?api_key=" + SECRET, "cloud"),
    ("https://api.deepseek.com/#fragment", "cloud"), ("file:///C:/secrets", "cloud"),
    ("https://api.deepseek.com/v1/../secret", "cloud"), ("https://api.deepseek.com/%2e%2e", "cloud"),
    ("https://api.deepseek.com\\@evil.example", "cloud"), ("https://api.deepseek.com\r\nx: y", "cloud"),
    ("https://127.0.0.1/v1", "cloud"), ("http://192.168.1.20:8000/v1", "loopback"),
    ("http://localhost:8080/v1", "loopback"), ("http://2130706433:8080/v1", "loopback"),
    ("http://0.0.0.0:8080/v1", "lan"), ("http://169.254.169.254/v1", "lan"),
    ("http://8.8.8.8:8080/v1", "lan"), ("http://127.0.0.1:99999/v1", "loopback"),
])
def test_bad_endpoints_are_rejected_without_echoing_values(url, network):
    with pytest.raises(contract.ContractError) as error:
        profiles.endpoint_url(url, network)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("url,network", [
    ("http://127.0.0.1:8080/v1/", "loopback"), ("http://[::1]:8080/v1", "loopback"),
    ("http://192.168.1.20:8000/proxy/v1", "lan"), ("https://api.deepseek.com", "cloud"),
])
def test_explicit_recipients_are_valid(url, network):
    assert profiles.endpoint_url(url, network) == url.rstrip("/")


def test_workflow_cannot_override_endpoint_or_secret_source():
    original = job().to_dict()
    for name in ("base_url", "endpoint", "api_key", "secret", "headers", "authorize", "capabilities"):
        with pytest.raises(contract.ContractError):
            contract.WriterJob.from_dict(dict(original, **{name: SECRET}))
    for name in ("api_key", "headers", "trust_remote_code", "extra_body"):
        with pytest.raises(contract.ContractError):
            profiles.ApiProfile.from_dict(dict(profiles.example_profiles().profiles[0].to_dict(), **{name: SECRET}))


def test_profile_selection_has_no_fallback_or_path_interpretation():
    offered = profiles.example_profiles()
    for name in ("missing", "../deepseek", "C:\\secret.json"):
        with pytest.raises(contract.ContractError):
            offered.resolve(name)
    with pytest.raises(contract.ContractError):
        profiles.ProfileSet((offered.profiles[0], offered.profiles[0]))


def test_custom_system_replaces_guide_and_requires_explicit_validation():
    with pytest.raises(contract.ContractError, match="Choose"):
        contract.make_job(profile_id="local", prompt="raw", system_prompt="my system")
    custom = contract.make_job(profile_id="local", prompt="raw", system_prompt="  my system\n", validation="custom")
    assert custom.messages()[0]["content"] == "  my system\n"
    assert "WRITING GUIDE" not in custom.messages()[0]["content"]
    with pytest.raises(contract.ContractError):
        replace(custom, response_format="json_schema")


def test_job_roundtrip_preserves_raw_text_and_keeps_previous_as_text():
    original = job(task="Ref2VA", references=(contract.ReferenceSpec("ref_0", "Picture", "image", "a frame"),),
                   previous_context="Picture 99: text from an earlier node")
    restored = contract.WriterJob.from_dict(json.loads(json.dumps(original.to_dict())))
    assert original == restored
    assert restored.messages()[1]["content"].endswith("original_prompt:\n" + original.prompt)
    assert [one.label for one in restored.manifest] == ["Picture 1"]
    assert "text only, no media attached" in restored.messages()[1]["content"]


@pytest.mark.parametrize("changes", [
    {"version": True}, {"profile_id": []}, {"task": []}, {"duration": SECRET},
    {"duration": 10**1000}, {"references": SECRET}, {"generation": None},
    {"references": [{"slot": "ref_0", "role": [], "kind": "image"}]},
    {"references": [{"slot": "ref_0", "role": "Picture", "kind": []}]},
    {"references": [{"slot": "ref_0", "role": "Picture", "kind": "image", "refmod": 1}]},
])
def test_malformed_serialized_job_is_rejected_as_data(changes):
    with pytest.raises(contract.ContractError) as error:
        contract.WriterJob.from_dict(dict(job().to_dict(), **changes))
    assert SECRET not in str(error.value)


def test_duplicate_or_missing_reference_relationships_fail():
    reference = contract.ReferenceSpec("ref_0", "Picture", "image")
    for refs in ((reference, reference), (replace(reference, related_slots=("ref_9",)),)):
        with pytest.raises(contract.ContractError):
            job(task="Ref2VA", references=refs)


def test_manifest_uses_generator_audio_order_and_keeps_subject_separate():
    refs = (
        contract.ReferenceSpec("ref_0", "Audio", "audio", "standalone voice"),
        contract.ReferenceSpec("ref_1", "Subject", "image", "person", refmod=True),
        contract.ReferenceSpec("ref_2", "Video", "video", "street clip", clip_audio=True, audio_description="traffic"),
        contract.ReferenceSpec("ref_3", "Picture", "image", "frame", related_slots=("ref_1",)),
    )
    prepared = job(task="Ref2VA", references=refs, media_mode="descriptions")
    assert [(one.slot, one.component, one.label) for one in prepared.manifest] == [
        ("ref_1", "media", "Subject 1"), ("ref_2", "sound", "Audio 1"),
        ("ref_2", "media", "Video 1"), ("ref_0", "media", "Audio 2"), ("ref_3", "media", "Picture 1"),
    ]
    originals = {one.slot: object() for one in refs}
    bundle = references.slot_bundle((one.slot, one.role, originals[one.slot], one.clip_audio) for one in refs)
    assert bundle["pictures"] == [originals["ref_3"], originals["ref_1"]]
    assert bundle["video_sounds"] == [True]
    assert contract.WriterJob.from_dict(json.loads(json.dumps(prepared.to_dict()))).manifest == prepared.manifest


def test_text_task_ignores_connected_references_with_notice(api):
    prepared = job(references=(contract.ReferenceSpec("ref_0", "Audio", "audio"),), previous_context="old references")
    answer = http_client(local_profile(api)).complete(prepared)
    assert not answer.manifest and answer.notices
    assert "reference_assets" not in api.calls[0]["body"]["messages"][1]["content"]
    assert "old references" not in api.calls[0]["body"]["messages"][1]["content"]


@pytest.mark.parametrize("task,count", [("I2VA", 1), ("FL2VA", 2), ("L2VA", 1), ("Ref2VA", 1)])
def test_task_counts_and_response_schema(task, count):
    refs = tuple(contract.ReferenceSpec(f"ref_{index}", "Picture", "image", "frame") for index in range(count))
    prepared = job(task=task, references=refs)
    expected = set(contract.guide_prompt.FIELDS_FOR_MODE[task])
    if task in contract.guide_prompt.ALIGNMENT_LINE:
        expected.add("alignment")
    assert set(prepared.output_schema()["required"]) == expected
    assert not prepared.output_schema()["additionalProperties"]
    with pytest.raises(contract.ContractError):
        replace(prepared, references=())


def test_h3_reference_capacity_is_independent_of_api_capacity():
    refs = tuple(contract.ReferenceSpec(f"ref_{index}", "Video", "video") for index in range(4))
    with pytest.raises(contract.ContractError, match="H3 capacity"):
        job(task="Ref2VA", references=refs)


def test_empty_guide_invalid_validation_and_unicode_fail_before_http(api):
    with pytest.raises(contract.ContractError):
        contract.make_job(profile_id="local", prompt="raw")
    with pytest.raises(contract.ContractError):
        contract.make_job(profile_id="local", prompt="raw", guide="test", validation="")
    with pytest.raises(contract.ContractError, match="Unicode"):
        http_client(local_profile(api)).complete(replace(job(), prompt="invalid \ud800"))
    assert not api.calls


@pytest.mark.parametrize("change", [
    {"duration": float("nan")}, {"duration": -1}, {"generation": contract.Generation(max_tokens=9000)},
    {"generation": contract.Generation(seed=42)}, {"response_format": "json_schema"},
    {"prompt": "x" * 3000},
])
def test_invalid_or_unsupported_jobs_do_not_request_a_key_or_send_http(api, change):
    profile = local_profile(api, secret=profiles.SecretRef("vault", "test"),
                            capabilities=contract.ModelCapabilities(),
                            limits=profiles.ProfileLimits(request_bytes=1500))
    authorized = []
    with pytest.raises(contract.ContractError):
        prepared = replace(job(profile), **change)
        http_client(profile, authorize=lambda selected: authorized.append(selected) or SECRET).complete(prepared)
    assert not authorized and not api.calls


def test_media_support_never_causes_silent_drop_or_fallback(api):
    reference = contract.ReferenceSpec("ref_0", "Picture", "image", "a red cup")
    prepared = job(task="I2VA", references=(reference,))
    with pytest.raises(contract.ContractError, match="does not support"):
        http_client(local_profile(api)).complete(prepared)
    declared_vision = local_profile(api, capabilities=contract.ModelCapabilities(modalities=frozenset({"text", "image"})))
    with pytest.raises(contract.ContractError, match="match every reference"):
        http_client(declared_vision).complete(prepared)
    assert not api.calls
    with pytest.raises(contract.ContractError, match="every reference"):
        http_client(local_profile(api)).complete(replace(prepared, media_mode="descriptions", references=(replace(reference, description=""),)))
    answer = http_client(local_profile(api)).complete(replace(prepared, media_mode="descriptions"))
    assert answer.manifest == prepared.manifest
    assert "a red cup" in api.calls[0]["body"]["messages"][1]["content"]


@pytest.mark.parametrize("template_index", [0, 1])
def test_both_profiles_obey_the_same_http_contract(api, template_index):
    template = profiles.example_profiles().profiles[template_index]
    profile = replace(template, base_url=api.url, network="loopback")
    prepared = job(profile, response_format="json_object")
    api.body = completion(reasoning_content="private reasoning")
    answer = http_client(profile, authorize=lambda selected: SECRET).complete(prepared)
    sent = api.calls[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["body"]["model"] == template.model and sent["body"]["stream"] is True
    assert sent["body"]["stream_options"] == {"include_usage": True}
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert sent["body"]["messages"] == prepared.messages()
    assert sent["headers"].get("Authorization") == ("Bearer " + SECRET if template_index else None)
    assert SECRET not in json.dumps(sent["body"])
    assert answer.text == ANSWER and answer.reasoning == "private reasoning"
    assert answer.usage.total_tokens == 34 and answer.elapsed_seconds >= 0
    assert SECRET not in repr(answer) and "private reasoning" not in repr(answer)


def test_llama_schema_is_transmitted_without_tools(api):
    prepared = job(response_format="json_schema")
    http_client(local_profile(api)).complete(prepared)
    payload = api.calls[0]["body"]
    assert payload["response_format"]["json_schema"]["schema"] == prepared.output_schema()
    assert not {"tools", "tool_choice", "functions", "extra_body"} & payload.keys()


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "forbidden"), (429, "rate_limit"), (500, "server"), (400, "http")])
def test_http_errors_do_not_retry_or_echo_server_secrets(api, status, code, caplog):
    api.status, api.body = status, {"error": SECRET, "request_headers": {"Authorization": "Bearer " + SECRET}}
    profile = local_profile(api, secret=profiles.SecretRef("vault", "test"))
    with pytest.raises(client.ApiError) as error:
        http_client(profile, authorize=lambda selected: SECRET).complete(job(profile))
    assert error.value.code == code and error.value.status == status and len(api.calls) == 1
    assert SECRET not in "".join(traceback.format_exception(error.value)) + caplog.text
    assert error.value.__context__ is None


def test_redirect_cannot_forward_authorization_to_another_recipient(api):
    with fake_server() as other:
        api.status, api.headers = 307, {"Location": other.url + "/chat/completions?key=" + SECRET}
        profile = local_profile(api, secret=profiles.SecretRef("vault", "test"))
        with pytest.raises(client.ApiError, match="No redirect"):
            http_client(profile, authorize=lambda selected: SECRET).complete(job(profile))
        assert len(api.calls) == 1 and not other.calls


@pytest.mark.parametrize("response,code", [
    (completion("  "), "empty"), (completion(None, reasoning_content="only reasoning"), "protocol"),
    (completion(finish="length"), "unfinished"), (completion(finish="aborted"), "unfinished"),
    (completion(finish=None), "unfinished"), (completion(refusal="no"), "refusal"),
    (completion(finish="content_filter"), "refusal"),
    (completion(tool_calls=[{"function": {"name": "exec", "arguments": SECRET}}]), "tool_call"),
    (completion(function_call={"name": "shell", "arguments": SECRET}), "tool_call"),
    ({"choices": []}, "protocol"), (b"<html>" + SECRET.encode() + b"</html>", "protocol"),
])
def test_unusable_answers_are_errors_not_prompts(api, response, code):
    api.body = response
    with pytest.raises(client.ApiError) as error:
        http_client(local_profile(api)).complete(job())
    assert error.value.code == code and len(api.calls) == 1
    assert SECRET not in "".join(traceback.format_exception(error.value))


def test_model_text_is_data_and_never_executed(api):
    text = "<script>throw new Error('x')</script>\n__import__('os').system('bad')"
    api.body = completion(text)
    assert http_client(local_profile(api)).complete(job()).text == text


def test_response_limit_is_enforced_on_received_bytes(api):
    api.body = completion("x" * 10000)
    profile = local_profile(api, limits=profiles.ProfileLimits(response_bytes=512))
    with pytest.raises(client.ApiError) as error:
        http_client(profile).complete(job(profile))
    assert error.value.code == "response_limit"


def test_timeout_is_bounded_and_not_retried(api):
    api.delay = 0.2
    with pytest.raises(client.ApiError) as error:
        http_client(local_profile(api), timeout=(0.05, 0.05)).complete(job())
    assert error.value.code == "timeout" and len(api.calls) == 1
    assert error.value.__context__ is None


def test_cancellation_before_and_after_http(api):
    connection = http_client(local_profile(api))
    with pytest.raises(client.ApiError) as error:
        connection.complete(job(), cancelled=lambda: True)
    assert error.value.code == "cancelled" and not api.calls
    with pytest.raises(client.ApiError) as error:
        connection.complete(job(), cancelled=lambda: bool(api.calls))
    assert error.value.code == "cancelled" and len(api.calls) == 1


def test_missing_or_failing_resolver_never_sends_http(api):
    profile = local_profile(api, secret=profiles.SecretRef("environment", "EXPLICIT_TEST_KEY"))
    with pytest.raises(client.ApiError) as error:
        http_client(profile).complete(job(profile))
    assert error.value.code == "credential_unavailable"

    def broken(_profile):
        raise ValueError(SECRET)

    with pytest.raises(client.ApiError) as error:
        http_client(profile, authorize=broken).complete(job(profile))
    assert error.value.code == "credential_failed" and error.value.__context__ is None
    assert SECRET not in "".join(traceback.format_exception(error.value)) and not api.calls


def test_tls_error_is_sanitized_and_keeps_verification_enabled(monkeypatch):
    seen = {}

    def fail(_session, _url, **options):
        seen.update(options)
        raise client.aiohttp.ClientSSLError(None, OSError(SECRET))

    monkeypatch.setattr(client.aiohttp.ClientSession, "post", fail)
    with pytest.raises(client.ApiError) as error:
        http_client(profiles.example_profiles().profiles[0]).complete(job())
    assert error.value.code == "tls" and seen["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert seen["ssl"].check_hostname and seen["allow_redirects"] is False
    assert SECRET not in "".join(traceback.format_exception(error.value)) and error.value.__context__ is None


@pytest.mark.parametrize("failure,reason", [
    (lambda: client.aiohttp.ClientConnectorDNSError(None, socket.gaierror(-2, SECRET)), "dns"),
    (lambda: client.aiohttp.ClientProxyConnectionError(None, OSError(SECRET)), "proxy"),
    (lambda: client.aiohttp.ClientConnectorError(None, PermissionError(13, SECRET)), "denied"),
    (lambda: client.aiohttp.ClientConnectorError(None, ConnectionRefusedError(10061, SECRET)), "refused"),
    (lambda: client.aiohttp.ClientOSError(10054, SECRET), "reset"),
    (lambda: client.aiohttp.ServerDisconnectedError(SECRET), "response"),
    (lambda: client.aiohttp.ClientPayloadError(SECRET), "response"),
    (lambda: client.aiohttp.InvalidURL("https://user:" + SECRET + "@invalid.example/"), "request"),
])
def test_connection_reason_is_precise_without_exposing_the_vendor_error(monkeypatch, failure, reason):
    def fail(*_args, **_kwargs):
        raise failure()

    monkeypatch.setattr(client.aiohttp.ClientSession, "post", fail)
    with pytest.raises(client.ApiError) as error:
        http_client(profiles.example_profiles().profiles[0]).complete(job())
    assert error.value.code == "connection" and error.value.reason == reason
    assert SECRET not in "".join(traceback.format_exception(error.value))
    assert error.value.__context__ is None


def test_anonymous_local_profile_ignores_netrc_and_proxy_credentials(api, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Ambient .netrc credentials were consulted")

    monkeypatch.setattr(requests.sessions, "get_netrc_auth", forbidden)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    http_client(local_profile(api)).complete(job())
    assert len(api.calls) == 1 and "Authorization" not in api.calls[0]["headers"]
