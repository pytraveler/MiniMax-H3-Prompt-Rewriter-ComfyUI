"""Browser editing security boundaries; temporary files and dummy keys only."""

import asyncio
import base64
import copy
import json
import os
import ssl
import datetime
import ipaddress
import types
import sys
from dataclasses import replace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from test_api_secrets import DUMMY, config, profiles, secrets

from minimax_h3_rewriter import api_management as management, api_management_routes as routes

ORIGIN = "http://127.0.0.1:8188"
ADMIN = "dummy-administrator-token-32-characters-minimum"


@pytest.fixture(autouse=True)
def isolated_administration_environment(monkeypatch):
    for name in (routes.ADMIN_TOKEN_ENV, routes.ADMIN_ORIGIN_ENV, secrets.VAULT_KEY_ENV):
        monkeypatch.delenv(name, raising=False)


def profile():
    return replace(profiles.example_profiles().profiles[1], capabilities=profiles.ModelCapabilities())


class EncryptedTestBackend:
    """Real AEAD in tests without requiring an OS account or storing a master key."""

    def __init__(self):
        self.key = AESGCM.generate_key(256)

    def seal(self, data):
        iv = os.urandom(12)
        return iv + AESGCM(self.key).encrypt(iv, data, None)

    def unseal(self, data):
        return AESGCM(self.key).decrypt(data[:12], data[12:], None)


@pytest.fixture
def editor(tmp_path):
    backend = EncryptedTestBackend()
    factory = lambda directory: secrets.DpapiStore(directory, backend=backend)
    config.save_profiles(profiles.ProfileSet((profile(),)), tmp_path)
    factory(tmp_path).put(profile(), DUMMY)
    manager = management.ProfileManager(tmp_path, store_factory=factory)
    manager.test_store = factory(tmp_path)
    return manager


def stage(editor, opened, data=None, **kwargs):
    return editor.stage({"session": opened["session"], "revision": opened["revision"], "operation": "save",
                         "original_id": profile().id, "profile": profile().to_dict() if data is None else data,
                         "key_action": "keep", **kwargs}, ORIGIN)


def envelope(challenge, token=DUMMY, *, label=None):
    rsa = serialization.load_der_public_key(base64.b64decode(challenge["public_key"]))
    label = ("minimax-h3-api-key:v1:" + challenge["challenge"]).encode() if label is None else label
    key = AESGCM.generate_key(256)
    iv = os.urandom(12)
    return {"wrapped_key": base64.b64encode(rsa.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=label))).decode(),
            "iv": base64.b64encode(iv).decode(), "ciphertext": base64.b64encode(AESGCM(key).encrypt(iv, token.encode(), label)).decode()}


def commit(editor, opened, staged, **extra):
    return editor.commit({"session": opened["session"], "challenge": staged["challenge"], **extra}, ORIGIN)


def test_snapshot_never_decrypts_and_no_key_export(editor, monkeypatch):
    monkeypatch.setattr(editor.test_store.backend, "unseal", lambda _: pytest.fail("Snapshot decrypted a key"))
    opened = editor.open(ORIGIN)
    assert opened["profiles"][0] == profile().to_dict()
    assert DUMMY not in json.dumps(opened)
    assert len(opened["templates"]) == 4
    assert "private" not in json.dumps(opened)


def test_capability_edit_needs_no_vault_read(editor, monkeypatch):
    opened = editor.open(ORIGIN)
    data = profile().to_dict()
    data["capabilities"]["modalities"].append("audio")
    before = config.read_bytes(editor.test_store.root, config.SECRETS)
    monkeypatch.setattr(editor.test_store.backend, "unseal", lambda _: pytest.fail("Capabilities decrypted a key"))
    staged = stage(editor, opened, data)
    assert staged["public_key"] is None
    result = commit(editor, opened, staged)
    assert result["profiles"][0]["capabilities"]["modalities"] == ["audio", "text"]
    assert config.read_bytes(editor.test_store.root, config.SECRETS) == before
    with pytest.raises(management.ManagementError):
        commit(editor, opened, staged)


def test_new_key_hybrid_envelope_then_encrypted_disk(editor):
    opened = editor.open(ORIGIN)
    staged = stage(editor, opened, key_action="replace")
    new = "dummy-browser-replacement-" + "x" * 1000
    result = commit(editor, opened, staged, envelope=envelope(staged, new))
    assert editor.test_store.get(profile()) == new
    assert new.encode() not in config.read_bytes(editor.test_store.root, config.SECRETS)
    assert new not in json.dumps(result) and DUMMY not in json.dumps(result)
    assert all("private" not in key for key in staged)


@pytest.mark.parametrize("backend", ["dpapi", "server_key_aesgcm"])
@pytest.mark.parametrize("during_request", [False, True], ids=["between-requests", "request-in-flight"])
def test_key_replacement_reaches_the_same_client_without_restart(tmp_path, monkeypatch, backend, during_request):
    from test_api_writer import client, fake_server, job
    from test_api_text import writer

    if backend == "dpapi":
        if sys.platform != "win32":
            pytest.skip("Real DPAPI requires Windows")
        factory = secrets.DpapiStore
    else:
        monkeypatch.setenv(secrets.VAULT_KEY_ENV, base64.b64encode(os.urandom(32)).decode("ascii"))
        factory = secrets.ServerKeyStore
    monkeypatch.setitem(sys.modules, "folder_paths", types.SimpleNamespace(get_user_directory=lambda: str(tmp_path)))
    old, new = "dummy-old-rotation-key", "dummy-new-rotation-key"
    with fake_server() as api:
        selected = replace(profile(), base_url=api.url, network="loopback")
        config.save_profiles(profiles.ProfileSet((selected,)), tmp_path)
        store = factory(tmp_path)
        store.put(selected, old)
        resolver = secrets.SecretResolver(tmp_path, store=store)
        connection = client.ApiClient(profiles.ProfileSet((selected,)), authorize=resolver)
        request = job(selected)
        manager = management.ProfileManager(tmp_path, store_factory=factory)
        stamp = writer.MiniMaxH3ApiUniversalWriter.IS_CHANGED()

        def replace_key():
            opened = manager.open(ORIGIN)
            staged = manager.stage({"session": opened["session"], "revision": opened["revision"],
                "operation": "save", "original_id": selected.id, "profile": selected.to_dict(),
                "key_action": "replace"}, ORIGIN)
            result = commit(manager, opened, staged, envelope=envelope(staged, new))
            assert result["ok"]
            assert old not in json.dumps(result) and new not in json.dumps(result)
            assert writer.MiniMaxH3ApiUniversalWriter.IS_CHANGED() != stamp

        async def run():
            if during_request:
                api.delay = 0.5
                first = asyncio.create_task(connection.complete_async(request))
                for _ in range(300):
                    if api.calls:
                        break
                    await asyncio.sleep(0.01)
                assert api.calls and not first.done()
                replace_key()
                await first
                api.delay = 0
            else:
                await connection.complete_async(request)
                replace_key()
            await connection.complete_async(request)

        asyncio.run(run())
        assert [call["headers"]["Authorization"] for call in api.calls] == ["Bearer " + old, "Bearer " + new]
        encrypted = config.read_bytes(store.root, config.SECRETS)
        assert old.encode() not in encrypted and new.encode() not in encrypted


@pytest.mark.parametrize("corruption", ["wrong-label", "iv", "ciphertext", "wrapped_key", "plaintext", "unknown", "short", "oversize"])
def test_envelope_failures_are_consumed_and_do_not_modify_files(editor, corruption):
    opened = editor.open(ORIGIN)
    staged = stage(editor, opened, key_action="replace")
    before = config.read_bytes(editor.test_store.root, config.SECRETS)
    data = envelope(staged, label=b"another-recipient" if corruption == "wrong-label" else None)
    if corruption in ("iv", "ciphertext", "wrapped_key"):
        raw = bytearray(base64.b64decode(data[corruption])); raw[-1] ^= 1
        data[corruption] = base64.b64encode(raw).decode()
    elif corruption == "plaintext":
        data = {"token": DUMMY}
    elif corruption == "unknown":
        data["url"] = "https://other.example"
    elif corruption == "short":
        data["iv"] = "AA=="
    elif corruption == "oversize":
        data["ciphertext"] = "A" * 90000
    with pytest.raises(management.ManagementError) as error:
        commit(editor, opened, staged, envelope=data)
    assert DUMMY not in str(error.value)
    assert config.read_bytes(editor.test_store.root, config.SECRETS) == before
    with pytest.raises(management.ManagementError):
        commit(editor, opened, staged, envelope=envelope(staged))


def test_models_change_rebinds_only_same_connection_and_keeps_token(editor):
    opened = editor.open(ORIGIN)
    data = profile().to_dict()
    data["models"] = [{"id": "second-model", "capabilities": {"modalities": ["text", "image"]}}]
    result = commit(editor, opened, stage(editor, opened, data))
    saved = profiles.ApiProfile.from_dict(result["profiles"][0])
    assert editor.test_store.get(saved) == DUMMY
    with pytest.raises(secrets.SecretError):
        editor.test_store.get(profile())
    assert saved.select_model("second-model").capabilities.modalities == {"text", "image"}


@pytest.mark.parametrize("field,value", [("base_url", "https://another.example/v1"), ("network", "lan"), ("id", "changed")])
def test_no_silent_key_rebinding(editor, field, value):
    opened = editor.open(ORIGIN)
    data = profile().to_dict()
    data[field] = value
    with pytest.raises((management.ManagementError, ValueError)):
        stage(editor, opened, data)
    assert editor.test_store.get(profile()) == DUMMY


def test_new_profile_has_its_own_key_reference(editor):
    opened = editor.open(ORIGIN)
    data = replace(profile(), id="another").to_dict()
    with pytest.raises(management.ManagementError):
        stage(editor, opened, data, original_id="")
    data["secret"]["name"] = ""
    staged = stage(editor, opened, data, original_id="", key_action="replace")
    result = commit(editor, opened, staged, envelope=envelope(staged, "dummy-another-key"))
    second = profiles.ApiProfile.from_dict(result["profiles"][1])
    assert second.secret.name != profile().secret.name
    assert editor.test_store.get(second) == "dummy-another-key"
    assert editor.test_store.get(profile()) == DUMMY


def test_deletion_removes_profile_and_key_without_touching_other_records(editor):
    opened = editor.open(ORIGIN)
    staged = editor.stage({"session": opened["session"], "revision": opened["revision"], "operation": "delete",
                           "original_id": profile().id, "key_action": "delete"}, ORIGIN)
    result = commit(editor, opened, staged)
    assert result["profiles"] == []
    with pytest.raises(secrets.SecretError):
        editor.test_store.get(profile())


def test_stale_editor_and_changed_cli_file_do_not_overwrite(editor):
    opened = editor.open(ORIGIN)
    staged = stage(editor, opened)
    config.save_profiles(profiles.ProfileSet((replace(profile(), name="CLI changed"),)), editor.user_directory)
    with pytest.raises(management.ManagementError) as failure:
        commit(editor, opened, staged)
    assert failure.value.status == 409
    assert config.load_profiles(editor.user_directory).profiles[0].name == "CLI changed"
    with pytest.raises(management.ManagementError):
        stage(editor, opened)


def test_expiry_origin_and_session_capacity(editor):
    now = [0]
    editor.clock = lambda: now[0]
    opened = editor.open(ORIGIN)
    staged = stage(editor, opened, key_action="replace")
    with pytest.raises(management.ManagementError):
        editor.commit({"session": opened["session"], "challenge": staged["challenge"]}, "http://localhost:8188")
    now[0] += management.CHALLENGE_SECONDS + 1
    with pytest.raises(management.ManagementError):
        commit(editor, opened, staged)
    for _ in range(management.MAX_SESSIONS - 1):
        editor.open(ORIGIN)
    with pytest.raises(management.ManagementError) as failure:
        editor.open(ORIGIN)
    assert failure.value.status == 429
    now[0] += management.SESSION_SECONDS
    assert editor.open(ORIGIN)["profiles"]


def test_config_failure_restores_old_ciphertext(editor, monkeypatch):
    opened = editor.open(ORIGIN)
    staged = stage(editor, opened, key_action="replace")
    before = config.read_bytes(editor.test_store.root, config.SECRETS)
    original = config.atomic_write
    def failing(root, name, data):
        if name == config.PROFILES:
            raise config.ConfigError("Dummy disk failure")
        return original(root, name, data)
    monkeypatch.setattr(config, "atomic_write", failing)
    with pytest.raises(config.ConfigError):
        commit(editor, opened, staged, envelope=envelope(staged, "dummy-new-key"))
    assert config.read_bytes(editor.test_store.root, config.SECRETS) == before
    assert editor.test_store.get(profile()) == DUMMY


def test_route_guards_and_real_encrypted_post(editor):
    async def exercise():
        app = web.Application()
        table = web.RouteTableDef()
        routes.register(table, "/test", web, manager=editor)
        app.add_routes(table)
        async with TestClient(TestServer(app)) as http:
            url = str(http.make_url("/")).rstrip("/")
            for headers in ({}, {"Origin": "null"}, {"Origin": "https://evil.example"},
                            {"Origin": url, "Sec-Fetch-Site": "cross-site"},
                            {"Origin": url, "X-Forwarded-For": "127.0.0.1"},
                            {"Origin": "http://evil.example", "Host": "evil.example"}):
                response = await http.post("/test/api/manage/open", json={}, headers=headers)
                assert response.status == 403
            headers = {"Origin": url, "Sec-Fetch-Site": "same-origin"}
            response = await http.post("/test/api/manage/open", json={}, headers=headers)
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            opened = await response.json()
            response = await http.post("/test/api/manage/stage", json={"session": opened["session"], "revision": opened["revision"],
                "operation": "save", "original_id": profile().id, "profile": profile().to_dict(), "key_action": "replace"}, headers=headers)
            assert response.status == 200
            staged = await response.json()
            response = await http.post("/test/api/manage/commit", json={"session": opened["session"], "challenge": staged["challenge"],
                "envelope": envelope(staged)}, headers=headers)
            assert response.status == 200
            assert DUMMY not in await response.text()
            response = await http.post("/test/api/manage/open", data=json.dumps({"token": DUMMY}), headers={**headers, "Content-Type": "application/json"})
            assert response.status == 400 and DUMMY not in await response.text()
            response = await http.post("/test/api/manage/open", data=b"{}" * management.MAX_BODY, headers={**headers, "Content-Type": "application/json"})
            assert response.status == 400
    asyncio.run(exercise())


@pytest.mark.parametrize("remote", ["192.168.1.2", "203.0.113.2", "::ffff:192.168.1.2"])
def test_nonlocal_peer_cannot_administer_profiles(remote):
    from multidict import CIMultiDict
    request = types.SimpleNamespace(transport=types.SimpleNamespace(get_extra_info=lambda _: (remote, 1111)),
        headers=CIMultiDict(Host="localhost:8188", Origin="http://localhost:8188"), scheme="http", host="localhost:8188")
    with pytest.raises(management.ManagementError):
        routes.local_origin(request)


def admin_request(*, origin="https://comfy.example", peer="203.0.113.2", scheme="https", token=ADMIN, **headers):
    from multidict import CIMultiDict
    authority = origin.split("://", 1)[1]
    return types.SimpleNamespace(transport=types.SimpleNamespace(get_extra_info=lambda _: (peer, 1111)),
        headers=CIMultiDict({"Host": authority, "Origin": origin, "X-MiniMax-H3-Admin": token, **headers}), scheme=scheme, host=authority)


def test_remote_requires_operator_opt_in_and_correct_token(monkeypatch):
    with pytest.raises(management.ManagementError):
        routes.local_origin(admin_request())
    monkeypatch.setenv(routes.ADMIN_ORIGIN_ENV, "https://comfy.example")
    with pytest.raises(management.ManagementError):
        routes.local_origin(admin_request())
    monkeypatch.setenv(routes.ADMIN_TOKEN_ENV, ADMIN)
    assert routes.local_origin(admin_request()) == "https://comfy.example"
    with pytest.raises(management.ManagementError) as error:
        routes.local_origin(admin_request(token="wrong"))
    assert error.value.status == 401 and ADMIN not in str(error.value)
    for request in (admin_request(scheme="http"), admin_request(origin="https://another.example"),
                    admin_request(**{"Sec-Fetch-Site": "cross-site"}),
                    admin_request(scheme="http", **{"X-Forwarded-Proto": "https"})):
        with pytest.raises(management.ManagementError):
            routes.local_origin(request)


def test_only_exact_origin_and_loopback_proxy_may_forward_tls(monkeypatch):
    monkeypatch.setenv(routes.ADMIN_ORIGIN_ENV, "https://comfy.example")
    monkeypatch.setenv(routes.ADMIN_TOKEN_ENV, ADMIN)
    assert routes.local_origin(admin_request(peer="127.0.0.1", scheme="http", **{"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.2"})) == "https://comfy.example"
    for extra in ({"X-Forwarded-Proto": "http"}, {"X-Forwarded-Proto": "https,http"}, {"Forwarded": "proto=https"}):
        with pytest.raises(management.ManagementError):
            routes.local_origin(admin_request(peer="127.0.0.1", scheme="http", **extra))
    monkeypatch.setenv(routes.ADMIN_TOKEN_ENV, "short")
    with pytest.raises(management.ManagementError) as error:
        routes.local_origin(admin_request())
    assert error.value.status == 503


def test_token_when_configured_also_protects_local_editor(monkeypatch):
    monkeypatch.setenv(routes.ADMIN_TOKEN_ENV, ADMIN)
    for token, expected in (("", 401), ("incorrect", 401), (ADMIN, 200)):
        request = admin_request(origin=ORIGIN, peer="127.0.0.1", scheme="http", token=token)
        try:
            assert routes.local_origin(request) == ORIGIN
            assert expected == 200
        except management.ManagementError as error:
            assert error.status == expected


def test_server_master_key_store_authenticated_encryption_and_failure_preservation(tmp_path, monkeypatch):
    master = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv(secrets.VAULT_KEY_ENV, master)
    store = secrets.ServerKeyStore(tmp_path)
    store.put(profile(), DUMMY)
    original = config.read_bytes(store.root, config.SECRETS)
    assert original.startswith(secrets.SERVER_MAGIC)
    assert DUMMY.encode() not in original and master.encode() not in original
    assert store.get(profile()) == DUMMY
    assert secrets.ServerKeyStore(tmp_path).get(profile()) == DUMMY
    monkeypatch.setenv(secrets.VAULT_KEY_ENV, base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(secrets.SecretError) as error:
        secrets.ServerKeyStore(tmp_path).put(profile(), "dummy-wrong-master")
    assert error.value.code == "server_unlock"
    assert config.read_bytes(store.root, config.SECRETS) == original
    monkeypatch.delenv(secrets.VAULT_KEY_ENV)
    with pytest.raises(secrets.SecretError):
        secrets.ServerKeyStore(tmp_path).get(profile())
    assert config.read_bytes(store.root, config.SECRETS) == original


def test_server_vault_browser_roundtrip_and_resolver(editor, monkeypatch):
    monkeypatch.setenv(secrets.VAULT_KEY_ENV, base64.b64encode(os.urandom(32)).decode())
    with monkeypatch.context() as platform:
        platform.setattr(secrets, "sys", types.SimpleNamespace(platform="linux"))
        assert secrets.storage_kind() == "server_key_aesgcm"
        store = secrets.encrypted_store(editor.user_directory)
        assert isinstance(store, secrets.ServerKeyStore)
    directory = editor.user_directory / "linux-user"
    directory.mkdir()
    config.save_profiles(profiles.ProfileSet((profile(),)), directory)
    manager = management.ProfileManager(directory, store_factory=secrets.ServerKeyStore)
    opened = manager.open(ORIGIN)
    staged = stage(manager, opened, key_action="replace")
    result = commit(manager, opened, staged, envelope=envelope(staged))
    assert DUMMY not in json.dumps(result)
    assert secrets.SecretResolver(directory, store=secrets.ServerKeyStore(directory))(profile()) == DUMMY


@pytest.mark.parametrize("value", ["", "a" * 32, "not-base64" * 5, base64.b64encode(b"short").decode()])
def test_invalid_server_master_key_never_falls_back_to_plaintext(value, tmp_path, monkeypatch):
    monkeypatch.setenv(secrets.VAULT_KEY_ENV, value)
    store = secrets.ServerKeyStore(tmp_path)
    with pytest.raises(secrets.SecretError):
        store.put(profile(), DUMMY)
    assert config.read_bytes(store.root, config.SECRETS) is None


@pytest.mark.parametrize("name", [secrets.VAULT_KEY_ENV, routes.ADMIN_TOKEN_ENV])
def test_server_authority_secrets_cannot_be_used_as_model_credentials(name):
    with pytest.raises(ValueError):
        profiles.SecretRef("environment", name)


def test_https_editor_authentication_and_key_transfer(editor, tmp_path, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "comfy.example")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("comfy.example"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "test-cert.pem", tmp_path / "test-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert_path, key_path)
    client_tls = ssl.create_default_context(cafile=str(cert_path))
    monkeypatch.setenv(routes.ADMIN_TOKEN_ENV, ADMIN)

    async def exercise():
        app, table = web.Application(), web.RouteTableDef()
        routes.register(table, "/test", web, manager=editor)
        app.add_routes(table)
        server = TestServer(app, scheme="https")
        await server.start_server(ssl=server_tls)
        try:
            import aiohttp

            origin = "https://comfy.example:" + str(server.port)
            monkeypatch.setenv(routes.ADMIN_ORIGIN_ENV, origin)
            headers = {"Host": "comfy.example:" + str(server.port), "Origin": origin}
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=client_tls), trust_env=False) as http:
                url = str(server.make_url("/test/api/manage/"))
                async with http.post(url + "open", json={}, headers=headers) as response:
                    assert response.status == 401
                headers["X-MiniMax-H3-Admin"] = ADMIN
                async with http.post(url + "open", json={}, headers=headers) as response:
                    assert response.status == 200
                    opened = await response.json()
                async with http.post(url + "stage", headers=headers, json={"session": opened["session"], "revision": opened["revision"],
                    "operation": "save", "original_id": profile().id, "profile": profile().to_dict(), "key_action": "replace"}) as response:
                    assert response.status == 200
                    staged = await response.json()
                async with http.post(url + "commit", headers=headers, json={"session": opened["session"], "challenge": staged["challenge"],
                    "envelope": envelope(staged)}) as response:
                    assert response.status == 200
                    assert DUMMY not in await response.text()
                assert editor.test_store.get(profile()) == DUMMY
        finally:
            await server.close()
    asyncio.run(exercise())

