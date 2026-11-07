"""Dummy credentials only; Windows tests exercise the real current-user DPAPI."""

import importlib
import json
import os
import pathlib
import sys
import traceback
import types
from dataclasses import replace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if "minimax_h3_rewriter" not in sys.modules:
    package = types.ModuleType("minimax_h3_rewriter")
    package.__path__ = [str(ROOT / "minimax_h3_rewriter")]
    sys.modules[package.__name__] = package

config = importlib.import_module("minimax_h3_rewriter.api_config")
secrets = importlib.import_module("minimax_h3_rewriter.api_secrets")
profiles = importlib.import_module("minimax_h3_rewriter.api_profiles")

DUMMY = "dummy-stage-2-token-NEVER-A-REAL-KEY"


@pytest.fixture
def profile():
    return profiles.example_profiles().profiles[1]


@pytest.fixture
def store(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Real DPAPI is Windows only")
    return secrets.DpapiStore(tmp_path)


def test_dpapi_round_trip_and_delete_write_only_ciphertext(store, profile, caplog):
    store.put(profile, DUMMY)
    path = store.root / config.SECRETS
    first = path.read_bytes()
    assert first.startswith(secrets.MAGIC) and DUMMY.encode() not in first
    assert store.get(profile) == DUMMY
    assert DUMMY not in repr(store) + caplog.text
    store.put(profile, DUMMY + "-replacement")
    assert path.read_bytes() != first and DUMMY.encode() not in path.read_bytes()
    assert store.get(profile) == DUMMY + "-replacement"
    assert store.delete(profile) is True
    assert store.delete(profile) is False
    with pytest.raises(secrets.SecretError, match="no saved"):
        store.get(profile)
    assert all(DUMMY.encode() not in one.read_bytes() for one in store.root.iterdir() if one.is_file())


@pytest.mark.parametrize("change", [
    {"id": "other"}, {"base_url": "https://example.org/v1"}, {"model": "another-model"},
    {"secret": profiles.SecretRef("vault", "different-record")},
])
def test_saved_key_is_bound_to_profile_recipient_and_model(store, profile, change):
    store.put(profile, DUMMY)
    with pytest.raises(secrets.SecretError) as error:
        store.get(replace(profile, **change))
    assert error.value.code in ("binding", "missing")
    assert DUMMY not in "".join(traceback.format_exception(error.value))


def test_explicit_reentry_rebinds_only_the_same_profile_id(store, profile):
    store.put(profile, DUMMY)
    different = replace(profile, base_url="https://example.org/v1")
    store.put(different, DUMMY + "-new")
    assert store.get(different) == DUMMY + "-new"
    with pytest.raises(secrets.SecretError, match="different profile"):
        store.get(profile)
    with pytest.raises(secrets.SecretError, match="different profile"):
        store.put(replace(different, id="other"), DUMMY)
    with pytest.raises(secrets.SecretError, match="different profile"):
        store.delete(replace(different, id="other"))


@pytest.mark.parametrize("invalid", [b"", b"plaintext-key", secrets.MAGIC + b"broken ciphertext", b"MINIMAX-H3-DPAPI\x00\x02anything"])
def test_corrupt_or_unknown_store_is_preserved_on_get_put_delete(store, profile, invalid):
    store.root.mkdir()
    path = store.root / config.SECRETS
    path.write_bytes(invalid)
    for operation in (lambda: store.get(profile), lambda: store.put(profile, DUMMY), lambda: store.delete(profile)):
        with pytest.raises(secrets.SecretError) as error:
            operation()
        assert error.value.code in ("format", "unlock")
        assert path.read_bytes() == invalid
        assert error.value.__context__ is None


@pytest.mark.parametrize("payload", [
    {"version": 2, "records": {}}, {"version": True, "records": {}}, {"version": 1, "records": []},
    {"version": 1, "records": {"bad": {"token": DUMMY, "binding": {}}}},
    {"version": 1, "records": {}, "extra": DUMMY},
])
def test_valid_dpapi_with_invalid_inner_document_is_not_overwritten(store, profile, payload):
    store.root.mkdir()
    path = store.root / config.SECRETS
    encrypted = secrets.MAGIC + store.backend.seal(json.dumps(payload).encode())
    path.write_bytes(encrypted)
    with pytest.raises(secrets.SecretError, match="invalid"):
        store.put(profile, DUMMY)
    assert path.read_bytes() == encrypted


def test_failed_atomic_replace_preserves_old_store_and_cleans_temporary_ciphertext(store, profile, monkeypatch):
    store.put(profile, DUMMY)
    path = store.root / config.SECRETS
    old = path.read_bytes()
    seen = []

    def fail_replace(source, _destination):
        seen.append(pathlib.Path(source).read_bytes())
        raise OSError("simulated write failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    with pytest.raises(config.ConfigError, match="previous file was preserved"):
        store.put(profile, DUMMY + "-replacement")
    assert path.read_bytes() == old and seen
    assert all(one.startswith(secrets.MAGIC) and DUMMY.encode() not in one for one in seen)
    assert not list(store.root.glob("*.tmp"))


def test_two_records_survive_independent_local_store_instances(tmp_path, profile):
    if sys.platform != "win32":
        pytest.skip("Real DPAPI is Windows only")
    second = replace(profile, id="second", secret=profiles.SecretRef("vault", "second"))
    secrets.DpapiStore(tmp_path).put(profile, DUMMY)
    secrets.DpapiStore(tmp_path).put(second, DUMMY + "-second")
    assert secrets.DpapiStore(tmp_path).get(profile) == DUMMY
    assert secrets.DpapiStore(tmp_path).get(second) == DUMMY + "-second"


def test_overlapping_admin_transactions_fail_before_changing_ciphertext(store, profile):
    store.put(profile, DUMMY)
    old = (store.root / config.SECRETS).read_bytes()
    with secrets._exclusive(store.root):
        with pytest.raises(secrets.SecretError) as error:
            store.put(profile, DUMMY + "-second")
        assert error.value.code == "busy"
    assert (store.root / config.SECRETS).read_bytes() == old


@pytest.mark.parametrize("token", ["", "a b", "a\nb", "\u041a\u043b\u044e\u0447", "x" * 65537, None],
                         ids=["empty", "space", "newline", "non-ascii", "too-long", "none"])
def test_invalid_secret_never_creates_a_file(store, profile, token):
    with pytest.raises(secrets.SecretError, match="printable ASCII"):
        store.put(profile, token)
    assert not store.root.exists()


def test_explicit_environment_only_no_other_app_discovery_or_persistence(tmp_path, profile, monkeypatch):
    profile = replace(profile, secret=profiles.SecretRef("environment", "MINIMAX_TEST_EXPLICIT_KEY"))
    monkeypatch.setenv("MINIMAX_TEST_EXPLICIT_KEY", DUMMY)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    assert secrets.SecretResolver(tmp_path)(profile) == DUMMY
    assert not list(tmp_path.iterdir())
    monkeypatch.delenv("MINIMAX_TEST_EXPLICIT_KEY")
    with pytest.raises(secrets.SecretError) as error:
        secrets.SecretResolver(tmp_path)(profile)
    assert error.value.code == "environment" and "unrelated-key" not in str(error.value)


def test_unsupported_os_never_silently_creates_plaintext_store(tmp_path, profile, monkeypatch):
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    with pytest.raises(secrets.SecretError) as error:
        secrets.SecretResolver(tmp_path)(profile)
    assert error.value.code == "unsupported" and not list(tmp_path.iterdir())
    with pytest.raises(secrets.SecretError, match="unavailable on this OS"):
        secrets.WindowsDPAPI().seal(DUMMY.encode())


def test_credential_backend_extension_does_not_require_a_plugin(tmp_path, profile):
    class SystemStore:
        def get(self, selected):
            assert selected is profile
            return DUMMY

    assert secrets.SecretResolver(tmp_path, store=SystemStore())(profile) == DUMMY
    assert secrets.SecretResolver(tmp_path)(replace(profile, secret=profiles.SecretRef())) == ""


def test_configuration_requires_explicit_real_user_directory(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "folder_paths", types.SimpleNamespace(get_user_directory=lambda: str(tmp_path)))
    assert config.user_root() == tmp_path / config.SUBDIR
    monkeypatch.setitem(sys.modules, "folder_paths", types.SimpleNamespace())
    with pytest.raises(config.ConfigError, match="package storage is disabled"):
        config.user_root()
    for invalid in (ROOT, ROOT / "minimax_h3_rewriter", "relative-user", tmp_path / "missing"):
        with pytest.raises(config.ConfigError):
            config.user_root(invalid)


def test_profiles_roundtrip_has_no_key_fields_or_vault_access(tmp_path, monkeypatch):
    expected = profiles.example_profiles()
    monkeypatch.setattr(secrets.WindowsDPAPI, "unseal", lambda *_args: pytest.fail("A profiles read decrypted a key"))
    assert config.load_profiles(tmp_path).profiles == ()
    config.save_profiles(expected, tmp_path)
    assert config.load_profiles(tmp_path) == expected
    assert config.config_stamp(tmp_path)[1] is None
    raw = json.loads((config.user_root(tmp_path) / config.PROFILES).read_text())
    assert DUMMY not in json.dumps(raw) and raw["profiles"][1]["secret"] == {"kind": "vault", "name": "deepseek"}
    raw["profiles"][1]["api_key"] = DUMMY
    body = json.dumps(raw).encode()
    (config.user_root(tmp_path) / config.PROFILES).write_bytes(body)
    with pytest.raises(config.ConfigError) as error:
        config.load_profiles(tmp_path)
    assert DUMMY not in str(error.value) and error.value.__context__ is None
    assert (config.user_root(tmp_path) / config.PROFILES).read_bytes() == body


@pytest.mark.parametrize("body", [b'{"version":1,"version":2,"profiles":[]}', b'{"version":NaN,"profiles":[]}', b"[", b"x" * (config.MAX_FILE + 1)],
                         ids=["duplicate", "nan", "broken", "too-large"])
def test_malformed_profiles_do_not_replace_or_echo_input(tmp_path, body):
    root = config.user_root(tmp_path)
    root.mkdir()
    path = root / config.PROFILES
    path.write_bytes(body)
    with pytest.raises(config.ConfigError):
        config.load_profiles(tmp_path)
    assert path.read_bytes() == body


@pytest.mark.parametrize("linked", ["directory", config.PROFILES, config.SECRETS, "api_secrets.lock"])
def test_link_rejection_keeps_its_message_without_symlink_privileges(tmp_path, monkeypatch, linked):
    root = config.user_root(tmp_path)
    target = tmp_path / "other"
    target.mkdir()
    selected = root if linked == "directory" else root / linked
    original = type(root).resolve

    def resolve(path, *args, **kwargs):
        return target / "redirected" if path == selected else original(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "resolve", resolve)
    name = config.PROFILES if linked == "directory" else linked
    with pytest.raises(config.ConfigError, match="Linked") as error:
        config.atomic_write(root, name, b"DUMMY-MUST-NOT-BE-WRITTEN")
    assert error.value.__context__ is None
    assert not list(target.iterdir()) and not root.exists()


def test_symlinked_config_cannot_redirect_store_writes(tmp_path):
    root = config.user_root(tmp_path)
    target = tmp_path / "other"
    target.mkdir()
    try:
        root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable for this Windows account")
    with pytest.raises(config.ConfigError, match="Linked"):
        config.save_profiles(profiles.example_profiles(), tmp_path)
    assert not list(target.iterdir())
