"""Read-only draft connection tests: fake recipients and dummy keys only."""

import asyncio
import json
from dataclasses import replace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from test_api_management import DUMMY, ORIGIN, commit, config, editor, envelope, management, profile, profiles, routes, secrets, stage
from test_api_models import model_server, models
from minimax_h3_rewriter.api_client import ApiError


def configure(editor, api, kind="vault"):
    reference = profiles.SecretRef(kind, "TEST_API_CONNECTION_KEY" if kind == "environment" else profile().secret.name if kind == "vault" else "")
    chosen = replace(profile(), base_url=api.url, network="loopback", secret=reference)
    config.save_profiles(profiles.ProfileSet((chosen,)), editor.user_directory)
    if kind == "vault":
        editor.test_store.put(chosen, DUMMY)
    api.listing = {"data": [{"id": chosen.model}, {"id": "private-server-model", "path": "private-server-path"}]}
    return chosen


def files(editor):
    return tuple(config.read_bytes(editor.test_store.root, name) for name in (config.PROFILES, config.SECRETS))


def probe(editor, opened, staged, **extra):
    return editor.test({"session": opened["session"], "challenge": staged["challenge"], **extra}, ORIGIN)


@pytest.mark.parametrize("kind,action", [("none", "keep"), ("environment", "keep"), ("vault", "keep"), ("vault", "replace")])
def test_draft_credentials_do_not_save_or_pollute_discovery(editor, monkeypatch, kind, action):
    with model_server() as api:
        chosen = configure(editor, api, kind)
        if kind == "environment":
            monkeypatch.setenv(chosen.secret.name, DUMMY)
        opened = editor.open(ORIGIN)
        data = chosen.to_dict()
        data["models"] = [{"id": "draft-model"}]
        data["name"] = "Unsaved name"
        api.listing["data"].append({"id": "draft-model"})
        staged = stage(editor, opened, data, operation="test", key_action=action)
        before = files(editor)
        old_caches = dict(models._CACHE), dict(models._ATTEMPTS)
        monkeypatch.setattr(config, "atomic_write", lambda *_args: pytest.fail("A connection test wrote a file"))
        new = "dummy-new-unsaved-key"
        extra = {"envelope": envelope(staged, new)} if action == "replace" else {}
        result = asyncio.run(probe(editor, opened, staged, model="draft-model", **extra))
        assert result["model_available"] and result["model"] == "draft-model"
        assert result["models"] == [chosen.model, "draft-model"]
        assert result["credential"] == ("none" if kind == "none" else "sent")
        assert api.posts == [] and len(api.gets) == 1 and api.gets[0][0] == "/v1/models"
        assert api.gets[0][1].get("Authorization") == (None if kind == "none" else "Bearer " + (new if action == "replace" else DUMMY))
        assert DUMMY not in json.dumps(result) and new not in json.dumps(result) and "private-server" not in json.dumps(result)
        assert files(editor) == before
        assert (dict(models._CACHE), dict(models._ATTEMPTS)) == old_caches
        assert not editor.sessions[opened["session"]].testing
        with pytest.raises(management.ManagementError):
            asyncio.run(probe(editor, opened, staged, **extra))


def test_new_unsaved_provider_and_key_work_without_creating_records(editor, monkeypatch):
    with model_server() as api:
        data = replace(profile(), id="brand-new", base_url=api.url, network="loopback").to_dict()
        data["secret"]["name"] = ""
        api.listing = {"data": [{"id": data["model"]}]}
        opened = editor.open(ORIGIN)
        before = files(editor)
        staged = stage(editor, opened, data, operation="test", original_id="", key_action="replace")
        monkeypatch.setattr(config, "atomic_write", lambda *_args: pytest.fail("An unsaved provider was persisted"))
        result = asyncio.run(probe(editor, opened, staged, envelope=envelope(staged)))
        assert result["model_available"] and api.gets[0][1]["Authorization"] == "Bearer " + DUMMY
        assert files(editor) == before


def test_legacy_vault_and_unsaved_model_set_never_migrate_on_probe(editor, monkeypatch):
    with model_server() as api:
        chosen = configure(editor, api)
        records = editor.test_store._read()
        records[chosen.secret.name]["binding"] = secrets._legacy_binding(chosen)
        editor.test_store._write(records)
        opened = editor.open(ORIGIN)
        data = chosen.to_dict()
        data["models"] = [{"id": "new-model"}]
        staged = stage(editor, opened, data, operation="test")
        before = files(editor)
        monkeypatch.setattr(config, "atomic_write", lambda *_args: pytest.fail("A test migrated the legacy binding"))
        assert asyncio.run(probe(editor, opened, staged))["model_available"]
        assert files(editor) == before


@pytest.mark.parametrize("operation,consume", [("test", "commit"), ("save", "test")])
def test_challenge_purpose_cannot_be_switched(editor, operation, consume):
    opened = editor.open(ORIGIN)
    staged = stage(editor, opened, operation=operation, key_action="replace")
    before = files(editor)
    with pytest.raises(management.ManagementError):
        if consume == "commit":
            commit(editor, opened, staged, envelope=envelope(staged))
        else:
            asyncio.run(probe(editor, opened, staged, envelope=envelope(staged)))
    assert files(editor) == before and editor.sessions[opened["session"]].staged is None


def test_saved_key_cannot_be_sent_to_an_edited_recipient(editor, monkeypatch):
    opened = editor.open(ORIGIN)
    data = profile().to_dict()
    data["base_url"] = "https://another.example/v1"
    monkeypatch.setattr(editor.test_store.backend, "unseal", lambda _: pytest.fail("An unapproved recipient decrypted a key"))
    with pytest.raises(management.ManagementError, match="recipient"):
        stage(editor, opened, data, operation="test")


@pytest.mark.parametrize("failure", ["revision", "expired", "tampered", "selection", "binding"])
def test_invalid_tests_fail_before_provider_io(editor, monkeypatch, failure):
    with model_server() as api:
        chosen = configure(editor, api)
        now = [editor.clock()]
        editor.clock = lambda: now[0]
        opened = editor.open(ORIGIN)
        action = "replace" if failure == "tampered" else "keep"
        staged = stage(editor, opened, chosen.to_dict(), operation="test", key_action=action)
        extra = {}
        if failure == "revision":
            config.save_profiles(profiles.ProfileSet((replace(chosen, name="Concurrent edit"),)), editor.user_directory)
        elif failure == "expired":
            now[0] += management.CHALLENGE_SECONDS + 1
        elif failure == "tampered":
            extra["envelope"] = envelope(staged, label=b"wrong-purpose")
        elif failure == "selection":
            extra["model"] = "not-allowed"
        else:
            records = editor.test_store._read()
            records[chosen.secret.name]["binding"]["base_url"] = "https://different.example"
            editor.test_store._write(records)
        before = files(editor)
        with pytest.raises((management.ManagementError, secrets.SecretError, profiles.ContractError)):
            asyncio.run(probe(editor, opened, staged, **extra))
        assert api.gets == [] and api.posts == [] and files(editor) == before


@pytest.mark.parametrize("kind", ["none", "environment"])
def test_testing_a_key_deletion_never_deletes_the_old_record(editor, monkeypatch, kind):
    with model_server() as api:
        chosen = configure(editor, api)
        if kind == "environment":
            monkeypatch.setenv("TEST_API_CONNECTION_KEY", "dummy-env-key")
        data = chosen.to_dict()
        data["secret"] = {"kind": kind, "name": "TEST_API_CONNECTION_KEY" if kind == "environment" else ""}
        opened = editor.open(ORIGIN)
        staged = stage(editor, opened, data, operation="test", key_action="delete")
        before = files(editor)
        asyncio.run(probe(editor, opened, staged))
        assert files(editor) == before and editor.test_store.get(chosen) == DUMMY
        assert api.gets[0][1].get("Authorization") == (None if kind == "none" else "Bearer dummy-env-key")


def test_vault_key_marked_for_deletion_is_not_silently_reused(editor):
    with model_server() as api:
        chosen = configure(editor, api)
        opened = editor.open(ORIGIN)
        staged = stage(editor, opened, chosen.to_dict(), operation="test", key_action="delete")
        with pytest.raises(management.ManagementError, match="marked for deletion"):
            asyncio.run(probe(editor, opened, staged))
        assert api.gets == []


def test_single_flight_cooldown_and_cancellation_release_the_editor(editor, monkeypatch):
    async def exercise():
        now = [editor.clock()]
        editor.clock = lambda: now[0]
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return {"ok": True}

        monkeypatch.setattr(models, "test_connection", hold)
        opened = editor.open(ORIGIN)
        staged = stage(editor, opened, operation="test")
        request = asyncio.create_task(probe(editor, opened, staged))
        await entered.wait()
        with pytest.raises(management.ManagementError) as error:
            stage(editor, opened, operation="test")
        assert error.value.status == 429
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert not editor.sessions[opened["session"]].testing
        staged = stage(editor, opened, operation="test")
        with pytest.raises(management.ManagementError) as error:
            await probe(editor, opened, staged)
        assert error.value.status == 429
        now[0] += management.TEST_COOLDOWN
        release.set()
        assert await probe(editor, opened, staged) == {"ok": True}

    asyncio.run(exercise())


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "forbidden"), (404, "http"), (429, "rate_limit"), (302, "redirect"), (503, "server")])
def test_provider_http_failures_are_safe_and_not_retried(editor, status, code):
    with model_server() as api:
        chosen = configure(editor, api)
        api.status = status
        api.listing = {"error": "private-provider-message-" + DUMMY}
        api.headers = {"Location": api.url + "/must-not-follow"}
        opened = editor.open(ORIGIN)
        staged = stage(editor, opened, chosen.to_dict(), operation="test")
        with pytest.raises(ApiError) as error:
            asyncio.run(probe(editor, opened, staged))
        assert error.value.code == code and error.value.status == status
        assert DUMMY not in str(error.value) and "private-provider" not in str(error.value)
        assert len(api.gets) == 1 and api.posts == []
        assert not editor.sessions[opened["session"]].testing


@pytest.mark.parametrize("listing,code", [({"data": [{"id": DUMMY}]}, "credential_echo"), ({"data": "invalid"}, "protocol"), (b"x" * ((1 << 20) + 1), "response_limit")], ids=["credential-echo", "malformed", "oversized"])
def test_bad_or_credential_echoing_responses_are_discarded(editor, listing, code):
    with model_server() as api:
        chosen = configure(editor, api)
        api.listing = listing
        opened = editor.open(ORIGIN)
        staged = stage(editor, opened, chosen.to_dict(), operation="test")
        with pytest.raises(ApiError) as error:
            asyncio.run(probe(editor, opened, staged))
        assert error.value.code == code and DUMMY not in str(error.value)


def test_absent_model_is_reported_without_fallback(editor):
    with model_server() as api:
        chosen = configure(editor, api, "none")
        api.listing = {"data": [{"id": "private-other-model"}]}
        opened = editor.open(ORIGIN)
        staged = stage(editor, opened, chosen.to_dict(), operation="test")
        result = asyncio.run(probe(editor, opened, staged))
        assert result["ok"] and not result["model_available"] and result["models"] == []
        assert api.posts == []


def test_management_route_uses_admin_guards_and_safe_provider_errors(editor):
    async def exercise(api, chosen):
        table = web.RouteTableDef()
        routes.register(table, "/test", web, manager=editor)
        app = web.Application()
        app.add_routes(table)
        async with TestClient(TestServer(app)) as http:
            url = str(http.make_url("/")).rstrip("/")
            headers = {"Origin": url, "Sec-Fetch-Site": "same-origin"}
            opened = await (await http.post("/test/api/manage/open", json={}, headers=headers)).json()
            request = {"session": opened["session"], "revision": opened["revision"], "operation": "test",
                       "original_id": chosen.id, "profile": chosen.to_dict(), "key_action": "replace"}
            staged = await (await http.post("/test/api/manage/stage", json=request, headers=headers)).json()
            raw = {"session": opened["session"], "challenge": staged["challenge"], "envelope": envelope(staged)}
            for bad in ({}, {"Origin": "https://foreign.example"}, {**headers, "Sec-Fetch-Site": "cross-site"}):
                response = await http.post("/test/api/manage/test", json=raw, headers=bad)
                assert response.status == 403 and api.gets == []
            api.status = 401
            response = await http.post("/test/api/manage/test", json=raw, headers=headers)
            assert response.status == 503 and response.headers["Cache-Control"] == "no-store"
            result = await response.json()
            assert result["code"] == "unauthorized" and result["upstream_status"] == 401
            assert DUMMY not in json.dumps(result)
            assert len(api.gets) == 1 and api.posts == []

    with model_server() as api:
        chosen = configure(editor, api)
        before = files(editor)
        asyncio.run(exercise(api, chosen))
        assert files(editor) == before
