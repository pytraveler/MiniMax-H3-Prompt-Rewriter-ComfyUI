"""Models already pulled for Ollama, offered without a second copy on disk.

Somebody who runs Ollama has the writers and the captioners this pack wants
sitting on their disk already, and asking them to download the same quant again
into ``models/LLM`` is the whole of issue #12.

Nothing here is a new backend. Ollama stores what it downloads as a plain GGUF:
a blob whose first four bytes are ``GGUF``, which ``gguf_engine``, ``mtmd_engine``
and ``server_engine`` load by path like any other file. What this module adds is
the index -- turning a store into ``(label, path)`` pairs the existing scans
already know how to hand around.

Three things about the layout are worth knowing, because they are what make the
index cheap:

- **A manifest is the pairing.** ``manifests/<registry>/<namespace>/<name>/<tag>``
  is a small JSON listing the model's layers by media type, and a multimodal
  model carries its projector as another layer of the same manifest. Locally
  that answers the question ``_pair_mmproj`` has to guess at by comparing file
  names: two blobs named together in one manifest came from one conversion.
- **The blobs are content-addressed and extensionless.** ``blobs/sha256-<hex>``,
  the digest's colon turned into a dash. No name, no ``.gguf`` -- which is fine,
  because every scan in ``discovery`` decides what a file is by reading its
  header rather than its name.
- **Ollama's own template, params and system layers are ignored.** The GGUF
  carries a chat template of its own and the pack applies its own prompt; taking
  Ollama's would mean honouring its Modelfile, which is a different program's
  configuration and not ours to interpret.

**Where it looks, and why not everywhere.** The automatic roots are the three
places a local store can be, and they are the same strings on every platform
because Ollama's layout does not vary -- only ``~`` does. A store on the far side
of a virtual machine or a container is deliberately *not* found automatically:
reaching ``\\\\wsl$\\<distro>\\...`` starts a stopped WSL distribution, and this
index is rebuilt every time a dropdown is populated. Opening a ComfyUI tab must
not boot somebody's virtual machine. Those stores are named by hand instead --
``MINIMAX_H3_OLLAMA_MODELS``, or ``ollama_stores`` in ``models.json`` -- which
covers WSL, a Docker volume and a store moved to another drive with one
mechanism and no code that knows what WSL is.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from . import discovery, paths

log = logging.getLogger(__name__)

STORE_ENV = "MINIMAX_H3_OLLAMA_MODELS"
CATALOG_KEY = "ollama_stores"

OLLAMA_ENV = "OLLAMA_MODELS"

DEFAULT_ROOTS = (
    os.path.join("~", ".ollama", "models"),
    os.path.join(os.sep, "usr", "share", "ollama", ".ollama", "models"),
)

MODEL_LAYER = "application/vnd.ollama.image.model"
PROJECTOR_LAYER = "application/vnd.ollama.image.projector"

LIBRARY_PREFIX = ("registry.ollama.ai", "library")

MANIFEST_DEPTH = 5
MANIFEST_MAX_BYTES = 1 << 20

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Entry:
    """One model in a store: what to call it, and the files behind it.

    Two sizes, because a multimodal model is offered twice and the two answers
    are not the same number. As a captioner it is the model and its projector,
    both of which are loaded. As a writer it is the model alone -- the projector
    stays on disk, and quoting the pair would overstate what running it costs by
    a gigabyte or more on exactly the models where that matters.
    """

    name: str
    model: str
    projector: str = ""
    size: float = 0.0
    total: float = 0.0
    root: str = ""


def _named_roots() -> list[str]:
    """Stores the user named, which may be anywhere this machine can reach.

    A path that arrived this way was typed by the person running the server, in
    their own environment or in their own copy of ``models.json`` -- the file
    ``paths.refuse_network_path`` already exempts by name. So a UNC path is
    allowed here and refused below for the roots nobody asked for.
    """
    found: list[str] = []
    raw = os.environ.get(STORE_ENV) or ""
    found.extend(part for part in raw.split(os.pathsep) if part.strip())

    try:
        from . import catalog

        found.extend(catalog.ollama_stores())
    except Exception:
        log.debug("[minimax_h3_rewriter.ollama_store] catalog unreadable", exc_info=True)
    return found


def roots() -> list[str]:
    """Every store to index, hand-named ones first, each one only once."""
    found: list[str] = []
    seen: set[str] = set()

    def add(value: str, named: bool) -> None:
        text = os.path.expanduser((value or "").strip().strip('"'))
        if not text:
            return
        if not named and paths.is_network_path(text):
            log.debug("[minimax_h3_rewriter.ollama_store] %s is remote, not scanned", text)
            return
        try:
            if not os.path.isdir(os.path.join(text, "manifests")):
                return
        except OSError:
            return
        key = os.path.normcase(os.path.abspath(text))
        if key in seen:
            return
        seen.add(key)
        found.append(text)

    for value in _named_roots():
        add(value, True)
    add(os.environ.get(OLLAMA_ENV) or "", False)
    for value in DEFAULT_ROOTS:
        add(value, False)
    return found


def _manifest_files(root: str) -> list[str]:
    """Every manifest under one store, depth-limited against a loop of links."""
    found: list[str] = []
    stack = [(os.path.join(root, "manifests"), 0)]
    while stack:
        directory, level = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda one: one.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if level < MANIFEST_DEPTH:
                        stack.append((entry.path, level + 1))
                elif entry.is_file():
                    found.append(entry.path)
            except OSError:
                continue
    return sorted(found)


def model_name(root: str, manifest: str) -> str:
    """``registry/namespace/name/tag`` on disk as ``name:tag`` on screen."""
    try:
        relative = os.path.relpath(manifest, os.path.join(root, "manifests"))
    except ValueError:
        return ""
    parts = [part for part in relative.replace(os.sep, "/").split("/") if part and part != "."]
    if len(parts) < 2 or parts[0] == "..":
        return ""
    name, tag = parts[:-1], parts[-1]
    if len(name) > len(LIBRARY_PREFIX) and tuple(name[: len(LIBRARY_PREFIX)]) == LIBRARY_PREFIX:
        name = name[len(LIBRARY_PREFIX):]
    return "/".join(name) + ":" + tag


def _layers(manifest: str) -> dict[str, str]:
    """``{media type: digest}`` for one manifest, or empty if it is not one."""
    try:
        if os.path.getsize(manifest) > MANIFEST_MAX_BYTES:
            return {}
        with open(manifest, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    found: dict[str, str] = {}
    for layer in data.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        media, digest = layer.get("mediaType"), layer.get("digest")
        if isinstance(media, str) and isinstance(digest, str) and media not in found:
            found[media] = digest
    return found


def _blob(root: str, digest: str) -> str:
    """The file one digest names, or "" when it is absent or is not a digest.

    The shape is checked rather than trusted. A manifest is a file like any
    other, and a ``digest`` of ``sha256:../../..`` would otherwise be a path
    leaving the store -- cheap to refuse, and the refusal costs nothing real
    because every digest Ollama writes is 64 hex characters.
    """
    text = (digest or "").strip()
    prefix = "sha256:"
    if not text.startswith(prefix) or not _DIGEST.match(text[len(prefix):]):
        return ""
    path = os.path.join(root, "blobs", "sha256-" + text[len(prefix):])
    try:
        return path if os.path.isfile(path) else ""
    except OSError:
        return ""


def _size_gb(*files: str) -> float:
    total = 0
    for path in files:
        if not path:
            continue
        try:
            total += os.path.getsize(path)
        except OSError:
            return 0.0
    return total / 1024 ** 3


def entries() -> list[Entry]:
    """Every model in every store, one entry per distinct set of files.

    Tags sharing a blob -- ``qwen3:8b`` and ``qwen3:latest`` usually do -- are
    one model with two names, so the first name in path order stands for both
    rather than the same download being offered twice.
    """
    found: list[Entry] = []
    seen: set[tuple[str, str]] = set()

    for root in roots():
        for manifest in _manifest_files(root):
            name = model_name(root, manifest)
            if not name:
                continue
            layers = _layers(manifest)
            model = _blob(root, layers.get(MODEL_LAYER, ""))
            if not model:
                continue
            projector = _blob(root, layers.get(PROJECTOR_LAYER, ""))
            key = (
                os.path.normcase(os.path.abspath(model)),
                os.path.normcase(os.path.abspath(projector)) if projector else "",
            )
            if key in seen:
                continue
            seen.add(key)
            found.append(
                Entry(
                    name=name,
                    model=model,
                    projector=projector,
                    size=_size_gb(model),
                    total=_size_gb(model, projector),
                    root=root,
                )
            )
    return found


def _usable(path: str) -> dict | None:
    """The header of a blob that is a language model, or ``None``.

    A store holds whatever was pulled, embedding models included, and a blob
    that llama.cpp cannot read at all is not impossible -- Ollama is free to
    keep something else under a media type we do not know. Reading four
    kilobytes settles it, and the answer is cached per file.
    """
    header = discovery.gguf_header(path)
    if not header["arch"] or header["kind"] != "model":
        return None
    if header["arch"] in discovery.ENCODER_ARCHS:
        return None
    return header


def scan_writers() -> list[tuple[str, str]]:
    """``(label, path)`` for every Ollama model that can write a prompt."""
    found: list[tuple[str, str]] = []
    for entry in entries():
        header = _usable(entry.model)
        if header is None:
            continue
        found.append((f"{entry.name} [{header['arch']}, {entry.size:.1f} GB]", entry.model))
    return found


def scan_captioners(arch: str | None = None) -> list[tuple[str, str, str]]:
    """``(label, model, projector)`` for every Ollama model with a projector.

    ``arch`` narrows it the way ``discovery.scan_captioner_gguf`` does: any pair
    will caption, but only one architecture can carry the 8B rewriter's LoRA.
    """
    found: list[tuple[str, str, str]] = []
    for entry in entries():
        if not entry.projector:
            continue
        header = _usable(entry.model)
        if header is None:
            continue
        if arch is not None and header["arch"] != arch:
            continue
        carried = discovery.gguf_header(entry.projector)
        modalities = ", ".join(
            name
            for name, present in (("vision", carried["vision"]), ("audio", carried["audio"]))
            if present
        ) or "unknown"
        label = f"{entry.name} [+mmproj, {modalities}, {entry.total:.1f} GB]"
        found.append((label, entry.model, entry.projector))
    return found
