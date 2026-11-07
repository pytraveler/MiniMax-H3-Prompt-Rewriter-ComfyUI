"""Local profile editing and one-use browser key envelopes, independent of routes.

Sessions and RSA private keys live only in memory. Explicit connection tests
read /models; editing never calls providers. No graph inputs, caller-selected
files, plaintext persistence or credential export.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass

from . import api_config as config
from .api_contract import model_id, object_fields
from .api_profiles import ApiProfile, ProfileSet, example_profiles
from .api_secrets import SecretError, SecretResolver, binding, encrypted_store, storage_kind, _exclusive, _legacy_binding, valid_token

MAX_BODY = 128 << 10
SESSION_SECONDS = 900
CHALLENGE_SECONDS = 90
MAX_SESSIONS = 8
TEST_COOLDOWN = 3.0


class ManagementError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass
class _Session:
    origin: str
    expires: float
    staged: object = None
    testing: bool = False
    last_test: float | None = None


@dataclass
class _Change:
    id: str
    expires: float
    revision: str
    profiles: ProfileSet
    previous: ApiProfile | None
    desired: ApiProfile | None
    action: str
    operation: str = "save"
    private_key: object = None


def _decode(value, maximum):
    if not isinstance(value, str) or len(value) > ((maximum + 2) // 3) * 4:
        raise ValueError()
    return base64.b64decode(value, validate=True)


def _envelope_key(change, envelope):
    """RSA-OAEP(SHA-256) wraps AES-256-GCM; both bind the one-use challenge."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag

    token = None
    try:
        raw = object_fields(envelope, ("wrapped_key", "iv", "ciphertext"), ("wrapped_key", "iv", "ciphertext"))
        wrapped = _decode(raw["wrapped_key"], 384)
        iv = _decode(raw["iv"], 12)
        ciphertext = _decode(raw["ciphertext"], 65536 + 16)
        if len(wrapped) != 384 or len(iv) != 12 or not 17 <= len(ciphertext) <= 65536 + 16:
            raise ValueError()
        label = ("minimax-h3-api-key:v1:" + change.id).encode("ascii")
        key = change.private_key.decrypt(wrapped, padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=label))
        if len(key) != 32:
            raise ValueError()
        plaintext = AESGCM(key).decrypt(iv, ciphertext, label)
        del key
        token = plaintext.decode("ascii")
        del plaintext
        if not valid_token(token):
            token = None
    except (ValueError, TypeError, KeyError, binascii.Error, InvalidTag):
        pass
    if token is None:
        raise ManagementError("The encrypted key could not be accepted. Enter it again; nothing was saved.")
    return token


class ProfileManager:
    """One local editor per route registration, with optimistic file revisions."""

    def __init__(self, user_directory=None, *, store_factory=encrypted_store, clock=time.monotonic):
        self.user_directory = user_directory
        self.store_factory = store_factory
        self.clock = clock
        self.sessions = {}
        self.lock = threading.RLock()

    def _document(self):
        body = config.read_bytes(config.user_root(self.user_directory), config.PROFILES)
        try:
            profiles = ProfileSet(()) if body is None else ProfileSet.from_dict(config.parse_json(body))
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise config.ConfigError("Invalid API profiles. Fix the local api_profiles.json; it was not changed.") from None
        return profiles, hashlib.sha256(b"missing" if body is None else body).hexdigest()

    def _session(self, token, origin):
        now = self.clock()
        for key in list(self.sessions):
            if self.sessions[key].expires <= now:
                del self.sessions[key]
        session = self.sessions.get(token) if isinstance(token, str) else None
        if session is None or session.origin != origin:
            raise ManagementError("This editor session expired. Reopen API providers.", 409)
        if session.staged is not None and session.staged.expires <= now:
            session.staged.private_key = None
        return session

    def _snapshot(self):
        profiles, revision = self._document()
        if len(profiles.profiles) > 128:
            raise ManagementError("Too many profiles for the editor. Use the local CLI.")
        return {**profiles.to_dict(), "revision": revision,
                "key_storage": storage_kind()}

    def open(self, origin):
        with self.lock:
            for key in list(self.sessions):
                if self.sessions[key].expires <= self.clock():
                    del self.sessions[key]
            if len(self.sessions) >= MAX_SESSIONS:
                raise ManagementError("Too many open API editors. Close another editor or wait for it to expire.", 429)
            snapshot = self._snapshot()
            token = secrets.token_urlsafe(32)
            self.sessions[token] = _Session(origin, self.clock() + SESSION_SECONDS)
            return {**snapshot, "session": token, "expires_in": SESSION_SECONDS,
                    "templates": example_profiles(include_local_runtimes=True).to_dict()["profiles"]}

    def close(self, raw, origin):
        raw = object_fields(raw, ("session",), ("session",))
        with self.lock:
            self._session(raw["session"], origin)
            del self.sessions[raw["session"]]
        return {"ok": True}

    def stage(self, raw, origin):
        raw = object_fields(raw, ("session", "revision", "operation", "original_id", "profile", "key_action"),
                            ("session", "revision", "operation", "original_id", "key_action"))
        with self.lock:
            session = self._session(raw["session"], origin)
            if session.testing:
                raise ManagementError("A connection test is already running in this editor.", 429)
            current, revision = self._document()
            if raw["revision"] != revision:
                raise ManagementError("Profiles changed in another editor or CLI. Reopen this window before saving.", 409)
            previous = None
            original = raw["original_id"]
            if original != "":
                previous = current.resolve(original)
            action = raw["key_action"]
            if action not in ("keep", "replace", "delete"):
                raise ManagementError("Invalid key operation.")
            desired = None
            if raw["operation"] in ("save", "test"):
                data = dict(object_fields(raw.get("profile"), ApiProfile.__dataclass_fields__))
                reference = dict(object_fields(data.get("secret", {}), ("kind", "name")))
                if reference.get("kind") == "vault":
                    name = previous.secret.name if previous and previous.secret.kind == "vault" else (
                        "api-" + hashlib.sha256(str(data.get("id", "")).encode()).hexdigest()[:32])
                    if reference.get("name", "") not in ("", name):
                        raise ManagementError("The saved key reference cannot be reassigned in the browser.")
                    reference["name"] = name
                data["secret"] = reference
                desired = ApiProfile.from_dict(data)
                if previous and desired.id != previous.id:
                    raise ManagementError("An existing profile ID is fixed. Add a new profile to use a different ID.")
                if not previous and any(one.id == desired.id for one in current.profiles):
                    raise ManagementError("That profile ID already exists.")
                if len(desired.name) > 200 or any(ord(c) < 32 for c in desired.name) or not all(model_id(m) for m in desired.allowed_models):
                    raise ManagementError("Use a short display name and public model IDs or server aliases.")
                for settings in (desired, *desired.models):
                    if any(value is not None and value > 2**31 - 1 for value in vars(settings.limits).values()):
                        raise ManagementError("A model limit is too large.")
            elif raw["operation"] != "delete" or previous is None or "profile" in raw:
                raise ManagementError("Invalid profile operation.")
            if action == "replace" and (desired is None or desired.secret.kind != "vault"):
                raise ManagementError("A new key requires encrypted server storage on the profile.")
            if action == "replace" and storage_kind() == "environment_only":
                raise SecretError("unsupported")
            if action == "delete" and (previous is None or previous.secret.kind != "vault"):
                raise ManagementError("This profile has no configured vault record to delete.")
            if action == "keep" and previous and previous.secret.kind == "vault":
                if (desired is None or desired.secret != previous.secret or any(
                        getattr(desired, field) != getattr(previous, field) for field in ("id", "base_url", "network", "protocol"))):
                    raise ManagementError("Changing this key's recipient or source requires a new key or explicit deletion of the saved key.")
            result = [one for one in current.profiles if previous is None or one.id != previous.id]
            if desired is not None:
                if previous is None:
                    result.append(desired)
                else:
                    result.insert(next(i for i, one in enumerate(current.profiles) if one.id == previous.id), desired)
            profiles = ProfileSet(tuple(result))
            if len(result) > 128 or len(json.dumps(profiles.to_dict()).encode()) > config.MAX_FILE:
                raise ManagementError("The profile list is too large.")
            change = _Change(secrets.token_urlsafe(32), self.clock() + CHALLENGE_SECONDS,
                             revision, profiles, previous, desired, action, operation=raw["operation"])
            public = None
            if action == "replace":
                try:
                    from cryptography.hazmat.primitives import serialization
                    from cryptography.hazmat.primitives.asymmetric import rsa
                except ImportError:
                    raise ManagementError("Install the declared cryptography dependency and restart ComfyUI. Key transfer is unavailable.", 503) from None
                change.private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
                public = base64.b64encode(change.private_key.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode("ascii")
            session.staged = change
            return {"challenge": change.id, "expires_in": CHALLENGE_SECONDS, "public_key": public,
                    "algorithm": "RSA-OAEP-256+A256GCM" if public else None}

    def commit(self, raw, origin):
        raw = object_fields(raw, ("session", "challenge", "envelope"), ("session", "challenge"))
        with self.lock:
            session = self._session(raw["session"], origin)
            change = session.staged
            if change is None or raw["challenge"] != change.id:
                raise ManagementError("This save challenge is no longer available. Try saving again.", 409)
            session.staged = None
            if change.operation == "test":
                raise ManagementError("A connection-test challenge cannot save settings. Nothing was saved.", 409)
            if change.expires <= self.clock():
                raise ManagementError("This save challenge expired. Enter the key and save again.", 409)
            token = None
            if change.action == "replace":
                token = _envelope_key(change, raw.get("envelope"))
            elif raw.get("envelope") is not None:
                raise ManagementError("Unexpected key envelope.")
            change.private_key = None
            try:
                self._write(change, token)
            finally:
                token = None
            return {"ok": True, **self._snapshot()}

    def _prepare_test(self, raw, origin):
        from .api_client import credential

        raw = object_fields(raw, ("session", "challenge", "envelope", "model"), ("session", "challenge"))
        with self.lock:
            session = self._session(raw["session"], origin)
            if session.testing or session.last_test is not None and self.clock() - session.last_test < TEST_COOLDOWN:
                raise ManagementError("A connection test is running or was just requested. Wait a few seconds.", 429)
            change = session.staged
            if change is None or raw["challenge"] != change.id:
                raise ManagementError("This connection-test challenge is no longer available. Test again.", 409)
            session.staged = None
            try:
                if change.operation != "test" or change.expires <= self.clock():
                    raise ManagementError("This challenge cannot test a connection. Test again; nothing was saved.", 409)
                if self._document()[1] != change.revision:
                    raise ManagementError("Profiles changed before testing. Reopen the editor; nothing was saved.", 409)
                desired = change.desired
                model = desired.select_model(raw.get("model", "")).model
                token = None
                if change.action == "replace":
                    token = _envelope_key(change, raw.get("envelope"))
                elif raw.get("envelope") is not None:
                    raise ManagementError("Unexpected key envelope.")
                elif desired.secret.kind == "vault":
                    if change.action == "delete":
                        raise ManagementError("This key is marked for deletion. Choose No key to test anonymous access, or keep/replace the key.")
                    previous = change.previous
                    if previous is None or previous.secret.kind != "vault":
                        raise SecretError("missing")
                    record = self.store_factory(self.user_directory)._read().get(previous.secret.name)
                    if record is None:
                        raise SecretError("missing")
                    if record["binding"] not in (binding(previous), _legacy_binding(previous)):
                        raise SecretError("binding")
                    token = record["token"]
                elif desired.secret.kind == "environment":
                    token = SecretResolver(self.user_directory)(desired)
                token = credential(desired, lambda _profile: token)
                session.last_test = self.clock()
                return desired, model, token, session
            finally:
                change.private_key = None

    async def test(self, raw, origin):
        from .api_models import test_connection

        profile, model, token, session = await asyncio.to_thread(self._prepare_test, raw, origin)
        with self.lock:
            session.testing = True
        try:
            return await test_connection(profile, selected=model, authorize=lambda _profile: token)
        finally:
            token = None
            with self.lock:
                session.testing = False

    def _write(self, change, token):
        root = config.user_root(self.user_directory)
        with _exclusive(root):
            if self._document()[1] != change.revision:
                raise ManagementError("Profiles changed before saving. Reopen the editor; nothing was saved.", 409)
            previous, desired = change.previous, change.desired
            needs_vault = change.action in ("replace", "delete") or (
                previous is not None and desired is not None and previous.secret.kind == "vault"
                and binding(previous) != binding(desired))
            old_vault = config.read_bytes(root, config.SECRETS) if needs_vault else None
            if needs_vault:
                store = self.store_factory(self.user_directory)
                records = store._read()
                selected = desired if change.action == "replace" else previous
                record = records.get(selected.secret.name)
                if record and record["binding"]["id"] != selected.id:
                    raise SecretError("binding")
                if change.action == "replace":
                    if not record and len(records) >= 128:
                        raise SecretError("format")
                    records[selected.secret.name] = {"binding": binding(selected), "token": token}
                elif change.action == "delete":
                    records.pop(selected.secret.name, None)
                elif record is not None:
                    if record["binding"] not in (binding(previous), _legacy_binding(previous)):
                        raise SecretError("binding")
                    records[selected.secret.name] = {"binding": binding(desired), "token": record["token"]}
                store._write(records)
            data = json.dumps(change.profiles.to_dict(), ensure_ascii=True, indent=2).encode() + b"\n"
            try:
                config.atomic_write(root, config.PROFILES, data)
            except (config.ConfigError, OSError):
                if needs_vault:
                    try:
                        if old_vault is None:
                            config.checked_path(root, config.SECRETS).unlink(missing_ok=True)
                        else:
                            config.atomic_write(root, config.SECRETS, old_vault)
                    except (config.ConfigError, OSError):
                        raise ManagementError("Saving failed and the encrypted key could not be restored. Check local storage before using this profile.", 503) from None
                raise
