"""Resolution of local model directories under the ComfyUI models tree."""

from __future__ import annotations

import json
import logging
import os
import re

from .constants import ADAPTER_FILES, MODELS_SUBDIR

log = logging.getLogger(__name__)


def _models_root() -> str:
    import folder_paths

    try:
        registered = folder_paths.get_folder_paths(MODELS_SUBDIR)
    except KeyError:
        registered = []
    if registered:
        return registered[0]

    root = os.path.join(folder_paths.models_dir, MODELS_SUBDIR)
    try:
        folder_paths.add_model_folder_path(MODELS_SUBDIR, root)
    except Exception:
        log.debug("[minimax_h3_rewriter._models_root] could not register %s", MODELS_SUBDIR)
    return root


def models_root() -> str:
    """Return ``ComfyUI/models/LLM``, creating it on first use."""
    root = _models_root()
    os.makedirs(root, exist_ok=True)
    return root


def local_dir_for_repo(repo_id: str) -> str:
    return os.path.join(models_root(), repo_id.rstrip("/").split("/")[-1])


def looks_like_repo_id(value: str) -> bool:
    return "/" in value and not os.path.isabs(value) and "\\" not in value


_EXTENDED_LOCAL = re.compile(r"^\\\\[?.]\\[A-Za-z]:")


def is_network_path(value: str) -> bool:
    """Whether this string names a UNC share rather than something on this machine."""
    text = (value or "").strip()
    if os.name == "nt":
        text = text.replace("/", "\\")
    return text.startswith("\\\\") and not _EXTENDED_LOCAL.match(text)


def refuse_network_path(value: str, field: str, advice: str) -> None:
    """Refuse a network location that arrived from outside this machine.

    Paths written into ``models.json`` by hand are the user's own and may point
    wherever they like, a NAS included; that file never leaves the machine unless
    somebody sends it. A string that arrived over the wire is the other kind --
    inside a downloaded workflow, or in a request to the ComfyUI API, which has
    no CSRF token and is routinely served on ``--listen``. Merely *looking* at a
    UNC path is an authentication attempt against whatever host it names: it
    happens on the ``isfile`` call, long before anything judges the model.

    Nothing legitimate is lost. A share holding your models is reachable through
    a drive letter or a mount point like any other folder, and ``advice`` says so
    in the words that fit wherever this is being called from.
    """
    if not is_network_path(value):
        return
    raise RuntimeError(f"'{value}' is a network path, and {field} will not follow one.\n\n{advice}")


def catalog_file(repo: str, name: str) -> str:
    """Where one file of a catalog entry sits on disk, or ``""`` if it is on the Hub.

    A ``models.json`` entry names either a Hugging Face repository or something
    already on this machine, and there are two natural ways to write the second:
    ``repo`` as the folder with ``file`` a name inside it, or ``file`` as the
    whole path with ``repo`` left out. Both are accepted, because both are what
    people actually type -- and the alternative is a file that is right there on
    the disk and cannot be named.

    The answer is a path, not a promise: the caller still has to check it exists.
    """
    name = (name or "").strip()
    repo = (repo or "").strip()
    if not name:
        return ""
    if os.path.isabs(name):
        return os.path.normpath(name)
    if repo and os.path.isdir(repo):
        return os.path.normpath(os.path.join(repo, name))
    return ""


def resolve_source(value: str, default_repo: str) -> tuple[str, str]:
    """Map a user-entered model reference to ``(repo_id, local_dir)``.

    ``repo_id`` is empty when the reference points at a directory that is not
    managed by this package, in which case nothing is ever downloaded into it.
    """
    value = (value or "").strip()
    if not value:
        value = default_repo

    if os.path.isabs(value):
        return "", os.path.normpath(value)

    candidate = os.path.join(models_root(), value)
    if os.path.isdir(candidate) and not looks_like_repo_id(value):
        return "", candidate

    if looks_like_repo_id(value):
        return value, local_dir_for_repo(value)

    return "", candidate


def _shard_names(directory: str) -> list[str] | None:
    index = os.path.join(directory, "model.safetensors.index.json")
    if not os.path.isfile(index):
        return None
    try:
        with open(index, "r", encoding="utf-8") as handle:
            weight_map = json.load(handle).get("weight_map", {})
    except (OSError, ValueError):
        return None
    return sorted(set(weight_map.values()))


def _present(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 0


def base_model_is_complete(directory: str) -> bool:
    if not _present(os.path.join(directory, "config.json")):
        return False

    shards = _shard_names(directory)
    if shards is not None:
        return all(_present(os.path.join(directory, name)) for name in shards)

    try:
        entries = os.listdir(directory)
    except OSError:
        return False
    return any(name.endswith(".safetensors") for name in entries)


def adapter_is_complete(directory: str) -> bool:
    return all(_present(os.path.join(directory, name)) for name in ADAPTER_FILES)
