"""Local administrative files; no routes, credential values or import-time I/O."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .api_contract import ContractError, model_id
from .api_profiles import ProfileSet

SUBDIR = "minimax_h3_rewriter"
PROFILES = "api_profiles.json"
SECRETS = "api_secrets.enc"
MAX_FILE = 2 << 20


class ConfigError(RuntimeError):
    """Configuration errors deliberately omit file contents and OS exceptions."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def parse_json(data):
    def invalid_constant(_value):
        raise ValueError("Non-finite JSON number.")

    return json.loads(data, object_pairs_hook=_unique_pairs, parse_constant=invalid_constant)


def public_profiles() -> list[dict]:
    """Recipient information for the node UI, without credential references."""
    return [{"id": one.id, "name": one.name, "base_url": one.base_url,
             "model": one.model if model_id(one.model) else "", "network": one.network,
             "modalities": sorted(one.capabilities.modalities)}
            for one in load_profiles().profiles]


def user_root(user_directory=None) -> Path:
    """Use only ComfyUI's configured user directory or an explicit local CLI path.

    Never fall back to the package, a model directory, HOME or another app.
    Graph inputs do not accept user_directory.
    """
    selected = user_directory
    if selected is None:
        try:
            import folder_paths

            selected = folder_paths.get_user_directory()
        except Exception:
            pass
    root = None
    try:
        candidate = Path(selected) if selected else None
        if candidate is not None and candidate.is_absolute() and candidate.is_dir():
            root = candidate.resolve(strict=True)
            if root.is_relative_to(Path(__file__).resolve().parent.parent):
                root = None
    except (OSError, TypeError, ValueError, RuntimeError):
        pass
    if root is None:
        raise ConfigError("An existing ComfyUI user directory is required; package storage is disabled.")
    return root / SUBDIR


def checked_path(root: Path, name: str) -> Path:
    if name not in (PROFILES, SECRETS, "api_secrets.lock"):
        raise ConfigError("Unsupported API configuration file.")
    destination = root / name
    try:
        linked = root.resolve() != root or destination.resolve() != destination
    except (OSError, RuntimeError):
        raise ConfigError("The API configuration location is unavailable.") from None
    if linked:
        raise ConfigError("Linked API configuration files or directories are not supported.")
    return destination


def read_bytes(root: Path, name: str, *, missing=None):
    path = checked_path(root, name)
    failed = False
    try:
        with path.open("rb") as handle:
            body = handle.read(MAX_FILE + 1)
    except FileNotFoundError:
        return missing
    except OSError:
        failed = True
    if failed:
        raise ConfigError("Cannot read the API configuration file.")
    if len(body) > MAX_FILE:
        raise ConfigError("The API configuration file exceeds its size limit.")
    return body


def atomic_write(root: Path, name: str, data: bytes) -> None:
    """The caller must encrypt secrets BEFORE calling this function."""
    if not isinstance(data, bytes) or len(data) > MAX_FILE:
        raise ConfigError("Invalid API configuration size.")
    destination = checked_path(root, name)
    temporary = None
    failed = False
    try:
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        checked_path(root, name)
        fd, temporary = tempfile.mkstemp(prefix="." + name + ".", suffix=".tmp", dir=root)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError:
        failed = True
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
    if failed:
        raise ConfigError("Cannot save the API configuration; the previous file was preserved.")


def load_profiles(user_directory=None) -> ProfileSet:
    body = read_bytes(user_root(user_directory), PROFILES)
    if body is None:
        return ProfileSet(())
    result = None
    try:
        result = ProfileSet.from_dict(parse_json(body))
    except (ValueError, TypeError, OverflowError, RecursionError):
        pass
    if result is None:
        raise ConfigError("Invalid API profiles. Fix the local api_profiles.json; it was not changed.")
    return result


def save_profiles(profiles: ProfileSet, user_directory=None) -> None:
    if not isinstance(profiles, ProfileSet):
        raise ContractError("Validated server-side profiles are required.")
    data = json.dumps(profiles.to_dict(), ensure_ascii=True, indent=2).encode("utf-8") + b"\n"
    from .api_secrets import _exclusive

    root = user_root(user_directory)
    with _exclusive(root):
        atomic_write(root, PROFILES, data)


def config_stamp(user_directory=None) -> tuple:
    """Cache identity only; schema/cache checks never decrypt a key."""
    root = user_root(user_directory)
    parts = []
    for name in (PROFILES, SECRETS):
        path = checked_path(root, name)
        try:
            stat = path.stat()
            parts.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            parts.append(None)
        except OSError:
            raise ConfigError("The API configuration location is unavailable.") from None
    return tuple(parts)
