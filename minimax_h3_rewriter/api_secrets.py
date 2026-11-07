"""Credential storage, independent of graphs, HTTP, logs and other applications.

Windows uses current-user DPAPI. Linux/other servers can explicitly provide a
random master key through their secret environment. There is no plaintext file
fallback and no dependency on a desktop keyring plugin.
"""

from __future__ import annotations

import contextlib
import base64
import binascii
import ctypes
import os
import sys
import threading
from pathlib import Path
from typing import Protocol

from . import api_config as config
from .api_contract import identifier, model_id, object_fields
from .api_profiles import ApiProfile

VERSION = 1
MAX_RECORDS = 128
MAGIC = b"MINIMAX-H3-DPAPI\x00\x01"
SERVER_MAGIC = b"MINIMAX-H3-AESGCM\x00\x01"
VAULT_KEY_ENV = "MINIMAX_H3_VAULT_KEY"
_LOCK = threading.RLock()

_MESSAGES = {
    "unsupported": "Encrypted API key storage is unavailable on this OS. Explicit environment credentials are supported.",
    "protect": "Windows could not encrypt the API key for the current user.",
    "unlock": "The API key store could not be decrypted for this Windows user. The original file was preserved.",
    "format": "The API key store is invalid or has an unsupported version. The original file was preserved.",
    "missing": "This profile has no saved API key. Configure it locally first.",
    "binding": "The saved API key belongs to a different profile or recipient. Configure this profile's key again.",
    "token": "An API key must be nonempty printable ASCII without spaces or control characters.",
    "source": "This operation requires a vault credential in the selected server-side profile.",
    "environment": "The explicitly configured API credential environment variable is absent or invalid.",
    "busy": "The API key store could not be locked for changes. Check permissions and other local administration processes.",
    "terminal": "A local interactive terminal is required for hidden API key entry.",
    "vault_key": "Configure MINIMAX_H3_VAULT_KEY on the server: base64 of 32 random bytes, kept separately from the encrypted store.",
    "server_unlock": "The encrypted API key store could not be opened with this server vault key. The original file was preserved.",
    "crypto": "Install the declared cryptography dependency before using encrypted server key storage.",
}


class SecretError(RuntimeError):
    def __init__(self, code):
        super().__init__(_MESSAGES[code])
        self.code = code


def valid_token(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 65536 and all(33 <= ord(char) < 127 for char in value)


def _server_key():
    encoded = os.environ.get(VAULT_KEY_ENV, "")
    key = None
    try:
        if len(encoded) == 44:
            key = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        pass
    if key is None or len(key) != 32:
        raise SecretError("vault_key")
    return key


def storage_kind():
    """Status only: never open/decrypt the credential file."""
    if sys.platform == "win32":
        return "windows_dpapi"
    try:
        _server_key()
        return "server_key_aesgcm"
    except SecretError:
        return "environment_only"


class ServerKeyCipher:
    """Headless server AEAD; the master key is supplied by its operator, not a file."""

    @staticmethod
    def _cipher():
        key = _server_key()
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise SecretError("crypto") from None
        return AESGCM(key)

    def seal(self, data):
        nonce = os.urandom(12)
        return nonce + self._cipher().encrypt(nonce, data, SERVER_MAGIC)

    def unseal(self, data):
        result = None
        cipher = self._cipher()
        try:
            from cryptography.exceptions import InvalidTag

            if len(data) < 28:
                raise ValueError()
            result = cipher.decrypt(data[:12], data[12:], SERVER_MAGIC)
        except (ValueError, InvalidTag):
            pass
        if result is None:
            raise SecretError("server_unlock")
        return result


def binding(profile: ApiProfile) -> dict:
    """Bind to one connection and its explicit allowed models, not its default."""
    if not model_id(profile.model):
        return _legacy_binding(profile)
    return {name: getattr(profile, name) for name in ("id", "protocol", "base_url", "network")} | {
        "secret": {"kind": profile.secret.kind, "name": profile.secret.name},
        "models": sorted(profile.allowed_models),
    }


def _legacy_binding(profile):
    return {name: getattr(profile, name) for name in ("id", "protocol", "base_url", "network", "model")} | {
        "secret": {"kind": profile.secret.kind, "name": profile.secret.name},
    }


class SecretStore(Protocol):
    """Extension point for DPAPI, Keychain or a verified Secret Service backend."""

    def get(self, profile: ApiProfile) -> str: ...
    def put(self, profile: ApiProfile, token: str) -> None: ...
    def delete(self, profile: ApiProfile) -> bool: ...


class WindowsDPAPI:
    """Only CRYPTPROTECT_UI_FORBIDDEN (1); never LOCAL_MACHINE (4)."""

    def _transform(self, data: bytes, decrypt: bool) -> bytes:
        if sys.platform != "win32":
            raise SecretError("unsupported")
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        failure = True
        result = b""
        source = ctypes.create_string_buffer(data)
        incoming = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = Blob()
        kernel = None
        try:
            crypt = ctypes.WinDLL("crypt32.dll", use_last_error=True, winmode=0x00000800)
            kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x00000800)
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
            operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                                  ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                  wintypes.DWORD, ctypes.POINTER(Blob)]
            operation.restype = wintypes.BOOL
            description = None if decrypt else "MiniMax-H3 API credentials v1"
            if operation(ctypes.byref(incoming), description, None, None, None, 1, ctypes.byref(outgoing)):
                if 0 < outgoing.cbData <= config.MAX_FILE:
                    result = ctypes.string_at(outgoing.pbData, outgoing.cbData)
                    failure = False
        except (OSError, AttributeError, ValueError):
            pass
        finally:
            ctypes.memset(ctypes.addressof(source), 0, ctypes.sizeof(source))
            if outgoing.pbData and kernel is not None:
                ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
                kernel.LocalFree(outgoing.pbData)
        if failure:
            raise SecretError("unlock" if decrypt else "protect")
        return result

    def seal(self, data: bytes) -> bytes:
        return self._transform(data, False)

    def unseal(self, data: bytes) -> bytes:
        return self._transform(data, True)


@contextlib.contextmanager
def _exclusive(root: Path):
    """Avoid lost updates between local administration processes."""
    with _LOCK:
        path = config.checked_path(root, "api_secrets.lock")
        handle = None
        locked = False
        failure = None
        try:
            root.mkdir(mode=0o700, parents=False, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            handle = os.fdopen(fd, "r+b")
            if os.fstat(fd).st_size == 0:
                handle.write(b"\x00")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            failure = "busy"
        try:
            if failure:
                raise SecretError(failure)
            yield
        finally:
            if handle is not None:
                try:
                    if locked:
                        handle.seek(0)
                        if sys.platform == "win32":
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()


class DpapiStore:
    """Versioned ciphertext only, atomic replacement and strict payload checking."""

    magic = MAGIC

    def __init__(self, user_directory=None, *, backend=None):
        self.root = config.user_root(user_directory)
        self.backend = backend if backend is not None else WindowsDPAPI()

    @staticmethod
    def _require_vault(profile):
        if not isinstance(profile, ApiProfile) or profile.secret.kind != "vault":
            raise SecretError("source")

    def _read(self) -> dict:
        encrypted = config.read_bytes(self.root, config.SECRETS)
        if encrypted is None:
            return {}
        if not encrypted.startswith(self.magic):
            raise SecretError("format")
        plaintext = self.backend.unseal(encrypted[len(self.magic):])
        records = None
        try:
            raw = object_fields(config.parse_json(plaintext), ("version", "records"), ("version", "records"))
            if type(raw["version"]) is not int or raw["version"] != VERSION or not isinstance(raw["records"], dict):
                raise ValueError()
            if len(raw["records"]) > MAX_RECORDS:
                raise ValueError()
            for name, record in raw["records"].items():
                if not identifier(name):
                    raise ValueError()
                record = object_fields(record, ("binding", "token"), ("binding", "token"))
                tied = record["binding"]
                model_field = "models" if isinstance(tied, dict) and "models" in tied else "model"
                names = ("id", "protocol", "base_url", "network", "secret", model_field)
                tied = dict(object_fields(tied, names, names))
                if model_field == "models":
                    models = tied.pop("models")
                    if (not isinstance(models, list) or not 1 <= len(models) <= 64
                            or len(set(models)) != len(models) or any(not model_id(one) for one in models)):
                        raise ValueError()
                    tied["model"] = models[0]
                saved = ApiProfile.from_dict(dict(tied, name="Saved credential"))
                if saved.secret.kind != "vault" or saved.secret.name != name or not valid_token(record["token"]):
                    raise ValueError()
            records = raw["records"]
        except (ValueError, TypeError, RecursionError, OverflowError):
            pass
        finally:
            del plaintext
        if records is None:
            raise SecretError("format")
        return records

    def _write(self, records):
        import json

        plaintext = json.dumps({"version": VERSION, "records": records}, ensure_ascii=True).encode("utf-8")
        if len(plaintext) > config.MAX_FILE // 2:
            raise SecretError("format")
        encrypted = self.backend.seal(plaintext)
        del plaintext
        config.atomic_write(self.root, config.SECRETS, self.magic + encrypted)

    def get(self, profile: ApiProfile) -> str:
        self._require_vault(profile)
        with _LOCK:
            record = self._read().get(profile.secret.name)
        if record is None:
            raise SecretError("missing")
        if record["binding"] == binding(profile):
            return record["token"]
        if record["binding"] == _legacy_binding(profile):
            if not profile.models:
                return record["token"]
            self.migrate(profile, expected=record["binding"])
            return self.get(profile)
        raise SecretError("binding")

    def migrate(self, profile: ApiProfile, *, expected=None) -> None:
        """Local administration may extend models, never rebind a recipient."""
        self._require_vault(profile)
        with _exclusive(self.root):
            records = self._read()
            previous = records.get(profile.secret.name)
            if previous is None:
                raise SecretError("missing")
            saved = dict(previous["binding"])
            if expected is not None and saved != expected:
                raise SecretError("binding")
            legacy_model = saved.pop("model", None)
            saved_models = saved.pop("models", None)
            desired = binding(profile)
            if "models" not in desired:
                raise SecretError("binding")
            destination = dict(desired)
            destination.pop("models")
            if saved != destination or legacy_model is not None and legacy_model not in profile.allowed_models:
                raise SecretError("binding")
            if saved_models == desired["models"]:
                return
            records[profile.secret.name] = {"binding": desired, "token": previous["token"]}
            self._write(records)

    def put(self, profile: ApiProfile, token: str) -> None:
        self._require_vault(profile)
        if not valid_token(token):
            raise SecretError("token")
        with _exclusive(self.root):
            records = self._read()
            previous = records.get(profile.secret.name)
            if previous and previous["binding"]["id"] != profile.id:
                raise SecretError("binding")
            if not previous and len(records) >= MAX_RECORDS:
                raise SecretError("format")
            records[profile.secret.name] = {"binding": binding(profile), "token": token}
            self._write(records)

    def delete(self, profile: ApiProfile) -> bool:
        self._require_vault(profile)
        with _exclusive(self.root):
            records = self._read()
            previous = records.get(profile.secret.name)
            if previous is None:
                return False
            if previous["binding"]["id"] != profile.id:
                raise SecretError("binding")
            del records[profile.secret.name]
            self._write(records)
        return True


class ServerKeyStore(DpapiStore):
    magic = SERVER_MAGIC

    def __init__(self, user_directory=None):
        super().__init__(user_directory, backend=ServerKeyCipher())


def encrypted_store(user_directory=None):
    if sys.platform == "win32":
        return DpapiStore(user_directory)
    if storage_kind() != "server_key_aesgcm":
        raise SecretError("unsupported")
    return ServerKeyStore(user_directory)


class SecretResolver:
    """Only the selected server-side SecretRef is consulted, without discovery."""

    def __init__(self, user_directory=None, *, store: SecretStore | None = None):
        self.user_directory = user_directory
        self.store = store

    def __call__(self, profile: ApiProfile) -> str:
        if profile.secret.kind == "none":
            return ""
        if profile.secret.kind == "environment":
            token = os.environ.get(profile.secret.name)
            if not valid_token(token):
                raise SecretError("environment")
            return token
        store = self.store
        if store is None:
            store = encrypted_store(self.user_directory)
        return store.get(profile)
