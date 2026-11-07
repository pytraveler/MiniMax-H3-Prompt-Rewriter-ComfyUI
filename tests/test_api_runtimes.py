"""Local runtime connection diagnostics use only the configured recipient."""

import asyncio
import contextlib
import importlib
import json
import threading
import types
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_api_writer import SECRET, client, contract, profiles

admin = importlib.import_module("minimax_h3_rewriter.api_admin")
config = importlib.import_module("minimax_h3_rewriter.api_config")
models = importlib.import_module("minimax_h3_rewriter.api_models")


@contextlib.contextmanager
def runtime_server():
    state = types.SimpleNamespace(calls=[], health_status=200, health_body=b"", health_headers={},
        listing={"data": [{"id": "served-model", "root": "C:/private/model", "max_model_len": 8192}]})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state.calls.append((self.path, dict(self.headers)))
            health = self.path.endswith("/health")
            body = state.health_body if health else json.dumps(state.listing).encode()
            self.send_response(state.health_status if health else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (state.health_headers if health else {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_POST(self):
            state.calls.append(("UNEXPECTED POST " + self.path, dict(self.headers)))
            self.send_response(500)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def vllm_profile(api, **changes):
    return replace(profiles.local_runtime_profiles()[1], base_url=api.url + "/proxy/v1", **changes)


def test_optional_templates_preserve_default_init_and_declare_only_text():
    old = profiles.example_profiles()
    expanded = profiles.example_profiles(include_local_runtimes=True)
    assert expanded.profiles[:2] == old.profiles
    assert [one.id for one in expanded.profiles[2:]] == ["ollama-local", "vllm-local"]
    assert profiles.ProfileSet.from_dict(expanded.to_dict()) == expanded
    for one in expanded.profiles[2:]:
        assert one.secret.kind == "none" and one.network == "loopback" and one.stream is True
        assert one.capabilities.modalities == {"text"} and one.capabilities.formats == {"text"}
        assert one.capabilities.parameters == {"max_tokens", "temperature", "seed"}
    with pytest.raises(contract.ContractError):
        profiles.example_profiles(include_local_runtimes="yes")


def test_cli_optional_init_never_overwrites_existing_profiles(tmp_path, capsys):
    args = ["--user-directory", str(tmp_path), "init", "--include-local-runtimes"]
    assert admin.main(args) == 0
    path = config.user_root(tmp_path) / config.PROFILES
    before = path.read_bytes()
    assert len(config.load_profiles(tmp_path).profiles) == 4
    assert admin.main(args) == 1 and path.read_bytes() == before
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize("health", [False, True])
def test_probe_uses_get_on_fixed_same_origin_paths_and_authenticates_once(health, monkeypatch):
    with runtime_server() as api:
        profile = vllm_profile(api, secret=profiles.SecretRef("environment", "DUMMY_RUNTIME_KEY"))
        authorized = []
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("NO_PROXY", "")
        result = asyncio.run(models.probe_profile(profile, health=health,
            authorize=lambda one: authorized.append(one) or SECRET))
        assert [one[0] for one in api.calls] == (["/proxy/health"] if health else []) + ["/proxy/v1/models"]
        assert all(one[1]["Authorization"] == "Bearer " + SECRET for one in api.calls)
        assert all(one[1]["Accept"] == "application/json" for one in api.calls)
        assert authorized == [profile]
        assert result["health"] == ("ok" if health else "not_requested")
        assert result["models"][0]["available"] is True
        assert "C:/private" not in json.dumps(result) and SECRET not in json.dumps(result)
        assert result["models"][0]["modalities"] == ["text"]


@pytest.mark.parametrize("status,code", [(302, "redirect"), (401, "unauthorized"), (403, "forbidden"), (429, "rate_limit"), (503, "server")])
def test_failed_health_does_not_follow_redirects_query_models_or_disclose_body(status, code):
    with runtime_server() as api:
        api.health_status = status
        api.health_body = SECRET.encode()
        api.health_headers = {"Location": api.url + "/other-recipient"}
        profile = vllm_profile(api)
        with pytest.raises(client.ApiError) as error:
            asyncio.run(models.probe_profile(profile, health=True))
        assert error.value.code == code and SECRET not in str(error.value)
        assert [one[0] for one in api.calls] == ["/proxy/health"]


@pytest.mark.parametrize("limit,size", [(8192, 8193), (100, 101)])
def test_health_body_is_bounded_by_small_diagnostic_and_profile_limits(limit, size):
    with runtime_server() as api:
        api.health_body = b"x" * size
        profile = vllm_profile(api, limits=profiles.ProfileLimits(response_bytes=limit))
        with pytest.raises(client.ApiError) as error:
            asyncio.run(models.probe_profile(profile, health=True))
        assert error.value.code == "response_limit"
        assert len(api.calls) == 1


def test_health_credential_echo_is_rejected_without_querying_models():
    with runtime_server() as api:
        api.health_body = SECRET.encode()
        profile = vllm_profile(api, secret=profiles.SecretRef("environment", "DUMMY_RUNTIME_KEY"))
        with pytest.raises(client.ApiError) as error:
            asyncio.run(models.probe_profile(profile, health=True, authorize=lambda _one: SECRET))
        assert error.value.code == "credential_echo" and SECRET not in str(error.value)
        assert len(api.calls) == 1


def test_probe_cli_prints_public_metadata_without_saving_or_generating(tmp_path, capsys):
    with runtime_server() as api:
        profile = vllm_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), tmp_path)
        path = config.user_root(tmp_path) / config.PROFILES
        before = path.read_bytes()
        assert admin.main(["--user-directory", str(tmp_path), "probe", profile.id, "--health"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["health"] == "ok" and result["models"][0]["available"] is True
        assert path.read_bytes() == before and not (path.parent / config.SECRETS).exists()
        assert len(api.calls) == 2


def test_probe_cli_reports_safe_connection_errors(tmp_path, capsys):
    with runtime_server() as api:
        api.health_status = 401
        api.health_body = SECRET.encode()
        profile = vllm_profile(api)
        config.save_profiles(profiles.ProfileSet((profile,)), tmp_path)
        assert admin.main(["--user-directory", str(tmp_path), "probe", profile.id, "--health"]) == 1
        captured = capsys.readouterr()
        assert not captured.out and "401" in captured.err and SECRET not in captured.err
        assert "Traceback" not in captured.err and len(api.calls) == 1


@pytest.mark.parametrize("value", [None, "http://other-host/", 1])
def test_health_option_cannot_supply_a_url_or_other_non_boolean(value):
    profile = profiles.local_runtime_profiles()[1]
    with pytest.raises(contract.ContractError):
        asyncio.run(models.probe_profile(profile, health=value))
