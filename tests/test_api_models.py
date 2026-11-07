"""Model selection at the HTTP boundary, with fake recipients and dummy keys."""

import asyncio
import contextlib
import importlib
import json
import threading
import time
import traceback
import types
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_api_writer import SECRET, client, completion, contract, job, profiles
from test_api_text import ANSWER, config, node_environment, run_node, writer
from test_api_images import picture
from test_api_secrets import DUMMY, profile, store

models = importlib.import_module("minimax_h3_rewriter.api_models")
secrets = importlib.import_module("minimax_h3_rewriter.api_secrets")
text = importlib.import_module("minimax_h3_rewriter.api_text")


@pytest.fixture(autouse=True)
def isolated_model_cache():
    models._CACHE.clear()
    models._ATTEMPTS.clear()
    yield
    models._CACHE.clear()
    models._ATTEMPTS.clear()


@contextlib.contextmanager
def model_server():
    state = types.SimpleNamespace(gets=[], posts=[], status=200, headers={}, delay=0,
        listing={"object": "list", "data": [{"id": "vision"}, {"id": "small"}]},
        answer=ANSWER, after_post=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state.gets.append((self.path, dict(self.headers)))
            time.sleep(state.delay)
            self.respond(state.status, state.listing)

        def do_POST(self):
            state.posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if state.after_post:
                state.after_post()
            self.respond(200, completion(state.answer))

        def respond(self, status, body):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in state.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def configured(api, **changes):
    return profiles.ApiProfile("multi", "Model test", api.url, "vision", "loopback",
        capabilities=contract.ModelCapabilities(modalities={"text", "image"}, formats={"text", "json_object"},
                                                parameters={"max_tokens", "temperature", "seed"}),
        models=(profiles.ModelSettings("small", limits=profiles.ProfileLimits(output_tokens=512)),), **changes)


def refresh(profile, **kwargs):
    return asyncio.run(models.refresh_models(profile, **kwargs))


def test_old_profile_and_job_roundtrip_use_the_same_default_model():
    old = profiles.example_profiles().profiles[0]
    raw = old.to_dict()
    assert "models" not in raw
    assert profiles.ApiProfile.from_dict(raw) == old
    legacy = job().to_dict()
    legacy.pop("model")
    assert contract.WriterJob.from_dict(legacy).model == ""
    assert old.select_model("") is old


@pytest.mark.parametrize("value", ["", "../secret", "C:/models/private.gguf", "/models/private", "http://host/key", "bad\\path", "x\ny", "x" * 257])
def test_invalid_allowed_ids_do_not_echo_values(value):
    with pytest.raises(contract.ContractError) as error:
        profiles.ModelSettings(value)
    assert str(error.value) == "Invalid allowed model settings."


def test_model_entries_do_not_inherit_another_models_support():
    base = profiles.example_profiles().profiles[1]
    value = replace(base, models=(profiles.ModelSettings("text-only"),))
    selected = value.select_model("text-only")
    assert selected.model == "text-only" and selected.capabilities.modalities == {"text"}
    assert selected.capabilities.formats == {"text"}
    assert profiles.ApiProfile.from_dict(value.to_dict()) == value
    for ids in ((profiles.ModelSettings(base.model),), (profiles.ModelSettings("other"),) * 2):
        with pytest.raises(contract.ContractError, match="Duplicate"):
            replace(base, models=ids)
    for bad in (None, {}, "other"):
        with pytest.raises(contract.ContractError):
            profiles.ApiProfile.from_dict(dict(base.to_dict(), models=bad))


def test_selection_changes_payload_report_and_keeps_connection_credentials():
    with model_server() as api:
        base = configured(api, secret=profiles.SecretRef("environment", "MODEL_TEST_KEY"))
        authorized = []
        transport = client.ApiClient(profiles.ProfileSet((base,)), authorize=lambda one: authorized.append(one) or SECRET)
        for choice, expected in (("", "vision"), ("small", "small")):
            result = transport.complete(job(base, model=choice, generation=contract.Generation(128)))
            assert api.posts[-1]["model"] == result.model == expected
        assert authorized == [base, base] and not api.gets
        before = len(api.posts)
        for change in ({"model": "not-allowed"}, {"model": "small", "generation": contract.Generation(513)},
                       {"model": "small", "generation": contract.Generation(128, seed=1)},
                       {"model": "small", "response_format": "json_object"}):
            with pytest.raises(contract.ContractError):
                transport.complete(job(base, **change))
        assert len(api.posts) == before and len(authorized) == 2


def test_configuration_listing_never_calls_http_or_credentials():
    with model_server() as api:
        base = configured(api, secret=profiles.SecretRef("vault", "test-vault"))
        listing = models.public_models(base)
        assert listing["source"] == "configuration" and all(one["available"] is None for one in listing["models"])
        assert [one["id"] for one in listing["models"]] == ["vision", "small"]
        assert "vault" not in json.dumps(listing) and not api.gets and not api.posts


def test_remote_discovery_filters_private_router_fields_and_tightens_limits(monkeypatch):
    with model_server() as api:
        base = configured(api, secret=profiles.SecretRef("environment", "MODEL_TEST_KEY"))
        api.listing["data"] = [
            {"id": "vision", "input_modalities": ["text"], "context_window": 200, "max_output_tokens": 100,
             "name": "C:/private/model.gguf", "status": {"args": [SECRET]}},
            {"id": "small", "input_modalities": ["text", "image"], "max_output_tokens": 999999},
            {"id": "C:/private/unlisted.gguf", "args": [SECRET]}, {"id": "unapproved"},
        ]
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("NO_PROXY", "")
        with pytest.raises(client.ApiError) as error:
            refresh(base, authorize=lambda one: SECRET)
        assert error.value.code == "credential_echo" and models.cached(base) is None
        models._ATTEMPTS.clear()
        api.listing["data"][0]["status"]["args"] = ["--private-path", "C:/private/secret.gguf"]
        api.listing["data"][2]["args"] = ["--something-private"]
        listing = refresh(base, authorize=lambda one: SECRET)
        serialized = json.dumps(listing)
        assert SECRET not in serialized and "private" not in serialized and "args" not in serialized
        assert api.gets[-1][0] == "/v1/models" and api.gets[-1][1]["Authorization"] == "Bearer " + SECRET
        first, second = listing["models"]
        assert first["modalities"] == ["text"] and first["output_tokens"] == 100 and first["context_tokens"] == 200
        assert second["modalities"] == ["text"] and second["output_tokens"] == 512
        assert not api.posts


def test_missing_metadata_keeps_only_per_model_admin_declarations():
    with model_server() as api:
        base = configured(api)
        listing = refresh(base)
        assert listing["models"][0]["modalities"] == ["image", "text"]
        assert listing["models"][1]["modalities"] == ["text"]
        assert listing["models"][1]["output_tokens"] == 512


@pytest.mark.parametrize("listing", [{}, {"data": {}}, {"data": [None]}, {"data": [{"id": "vision"}] * 2},
    {"data": [{"id": "vision", "context_window": True}]},
    {"data": [{"id": "vision", "input_modalities": "image"}]}, {"data": [{"id": "vision", "max_output_tokens": -1}]}])
def test_invalid_discovery_is_safe_and_does_not_replace_cache(listing):
    with model_server() as api:
        base = configured(api)
        original = refresh(base)
        api.listing = listing
        models._ATTEMPTS.clear()
        with pytest.raises(client.ApiError) as error:
            refresh(base)
        assert error.value.code == "protocol" and models.public_models(base) == original


@pytest.mark.parametrize("status,expected", [(302, "redirect"), (401, "unauthorized"), (403, "forbidden"), (429, "rate_limit"), (500, "server")])
def test_discovery_http_errors_never_echo_or_follow_redirects(status, expected):
    with model_server() as api:
        base = configured(api)
        api.status = status
        api.headers["Location"] = api.url + "/other?key=" + SECRET
        api.listing = {"error": SECRET}
        with pytest.raises(client.ApiError) as error:
            refresh(base)
        assert error.value.code == expected and len(api.gets) == 1 and not api.posts
        assert SECRET not in "".join(traceback.format_exception(error.value)) and error.value.__context__ is None


def test_discovery_is_bounded_rate_limited_and_cache_identity_includes_configuration(monkeypatch):
    with model_server() as api:
        base = configured(api)
        refresh(base)
        with pytest.raises(contract.ContractError, match="Wait a few seconds"):
            refresh(base)
        assert len(api.gets) == 1
        changed = replace(base, models=())
        assert models.public_models(changed)["source"] == "configuration"
        monkeypatch.setattr(models, "TTL", -1)
        assert models.public_models(base)["source"] == "configuration"
        models._ATTEMPTS.clear()
        api.listing = b" " * ((1 << 20) + 1)
        with pytest.raises(client.ApiError) as error:
            refresh(base)
        assert error.value.code == "response_limit"


def test_unavailable_choice_fails_before_credentials_and_never_falls_back():
    with model_server() as api:
        base = configured(api)
        api.listing = {"data": [{"id": "vision"}]}
        listing = refresh(base)
        assert listing["models"][1]["available"] is False
        transport = client.ApiClient(profiles.ProfileSet((base,)), authorize=lambda *_args: pytest.fail("Read key for unavailable model"))
        with pytest.raises(contract.ContractError, match="not returned"):
            transport.complete(job(base, model="small", generation=contract.Generation(128)))
        assert not api.posts


def test_repair_pins_model_and_metadata_even_when_listing_changes_mid_request():
    with model_server() as api:
        base = configured(api)
        api.answer = ANSWER.replace("[Shot 1]", "[Shot 3]")
        refresh(base)
        api.after_post = lambda: models._CACHE.__setitem__(base, models.ModelList((), time.monotonic()))
        transport = client.ApiClient(profiles.ProfileSet((base,)))
        result = text.write(job(base, model="small", generation=contract.Generation(128)), transport, repair_once=True)
        report = json.loads(result.report)
        assert [one["model"] for one in api.posts] == ["small", "small"]
        assert report["model"] == "small" and report["requests"] == 2
        with pytest.raises(contract.ContractError, match="not returned"):
            client.ApiClient(profiles.ProfileSet((base,))).complete(job(base, model="small"))


def test_node_uses_selected_caps_before_media_and_reports_repeat_provenance(node_environment, monkeypatch):
    directory, _messages = node_environment
    with model_server() as api:
        base = configured(api)
        config.save_profiles(profiles.ProfileSet((base,)), directory)
        with pytest.raises(contract.ContractError, match="native media"):
            run_node(base.id, "I2VA", "raw", "16:9", 5, 128, model="small", ref_0=picture())
        assert not api.posts
        first = run_node(base.id, "T2VA", "raw", "16:9", 5, 128, model="small", unique_id="model-repeat",
                         system_prompt="Return a caption.", validation="custom")
        assert json.loads(first[-2])["model"] == "small"
        record = writer.memory.recall("model-repeat")
        assert record.about["model"] == "small"
        monkeypatch.setattr(config, "load_profiles", lambda: pytest.fail("Repeat read configuration"))
        repeated = run_node("missing", "T2VA", "changed", "16:9", 5, 128, model="vision", unique_id="model-repeat",
                            system_prompt="Return a caption.", validation="custom", repeat_last=True)
        report = json.loads(repeated[-2])
        assert report["model"] == "small" and report["profile_id"] == "multi" and report["requests"] == 0
        assert repeated[0] == first[0] and len(api.posts) == 1
        writer.memory.forget("model-repeat")


def test_legacy_ciphertext_upgrades_for_same_connection_models_without_reentry(store, profile):
    old_binding = secrets._legacy_binding(profile)
    store._write({profile.secret.name: {"binding": old_binding, "token": DUMMY}})
    path = store.root / config.SECRETS
    before = path.read_bytes()
    assert store.get(profile) == DUMMY and path.read_bytes() == before
    expanded = replace(profile, models=(profiles.ModelSettings("second"),))
    assert store.get(expanded) == DUMMY and path.read_bytes() != before
    assert DUMMY.encode() not in path.read_bytes()
    assert store._read()[profile.secret.name]["binding"]["models"] == sorted([profile.model, "second"])
    changed_default = replace(expanded, model="second", models=(profiles.ModelSettings(profile.model),))
    assert store.get(changed_default) == DUMMY


@pytest.mark.parametrize("change", [{"base_url": "https://other.example/v1"}, {"id": "another"},
    {"model": "unrelated"}, {"secret": profiles.SecretRef("vault", "other-record")}])
def test_legacy_migration_never_rebinds_recipient_or_unverified_original(store, profile, change):
    store._write({profile.secret.name: {"binding": secrets._legacy_binding(profile), "token": DUMMY}})
    path = store.root / config.SECRETS
    before = path.read_bytes()
    altered = replace(profile, **change)
    with pytest.raises(secrets.SecretError):
        store.get(altered)
    with pytest.raises(secrets.SecretError):
        store.migrate(altered)
    assert path.read_bytes() == before


def test_allowed_set_changes_need_local_migration_and_failed_write_preserves_ciphertext(store, profile, monkeypatch):
    store.put(profile, DUMMY)
    expanded = replace(profile, models=(profiles.ModelSettings("second"),))
    with pytest.raises(secrets.SecretError, match="different profile"):
        store.get(expanded)
    path = store.root / config.SECRETS
    before = path.read_bytes()
    real = config.atomic_write
    monkeypatch.setattr(config, "atomic_write", lambda *_args: (_ for _ in ()).throw(config.ConfigError("Failed")))
    with pytest.raises(config.ConfigError):
        store.migrate(expanded)
    assert path.read_bytes() == before
    monkeypatch.setattr(config, "atomic_write", real)
    store.migrate(expanded)
    assert store.get(expanded) == DUMMY and DUMMY.encode() not in path.read_bytes()
    with pytest.raises(secrets.SecretError):
        store.migrate(replace(expanded, base_url="https://other.example/v1"))
