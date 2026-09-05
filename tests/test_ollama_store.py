"""Reading an Ollama store: what is offered, what is skipped, what is refused.

The layout under test is the real one, taken off a 0.33.3 install: a manifest per
tag under ``manifests/<registry>/<namespace>/<name>/<tag>``, layers addressed by
digest, and blobs named ``sha256-<hex>`` with no extension. What matters here is
the index, not the loading -- llama.cpp reading these blobs is llama.cpp's own
business and is not mocked into a passing test.
"""

import importlib
import json
import os
import pathlib
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    _package = types.ModuleType(_PKG)
    _package.__path__ = [str(ROOT / _PKG)]
    sys.modules[_PKG] = _package

store = importlib.import_module(f"{_PKG}.ollama_store")
discovery = importlib.import_module(f"{_PKG}.discovery")

MODEL_DIGEST = "a" * 64
PROJECTOR_DIGEST = "b" * 64
OTHER_DIGEST = "c" * 64
VISION_DIGEST = "d" * 64

MODEL_BYTES = 8192
PROJECTOR_BYTES = 4096


def _header(arch="qwen3", kind="model", vision=False, audio=False):
    return {
        "arch": arch,
        "kind": kind,
        "vision": vision,
        "audio": audio,
        "blocks": 36,
        "width": 4096,
        "context": 32768,
        "kv_per_token": 0,
    }


def _write_blob(root: pathlib.Path, digest: str, size: int = 1024) -> pathlib.Path:
    blobs = root / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    path = blobs / f"sha256-{digest}"
    path.write_bytes(b"GGUF" + b"\0" * (size - 4))
    return path


def _write_manifest(root: pathlib.Path, name: str, tag: str, layers: list[tuple[str, str]]) -> None:
    directory = root / "manifests" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / tag).write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "layers": [
                    {"mediaType": media, "digest": f"sha256:{digest}", "size": 1024}
                    for media, digest in layers
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def one_store(tmp_path, monkeypatch):
    """A store with a text model and a multimodal one, and nothing else in reach."""
    root = tmp_path / "models"
    _write_blob(root, MODEL_DIGEST, MODEL_BYTES)
    _write_blob(root, VISION_DIGEST, MODEL_BYTES)
    _write_blob(root, PROJECTOR_DIGEST, PROJECTOR_BYTES)
    _write_manifest(
        root, "registry.ollama.ai/library/qwen3", "8b", [(store.MODEL_LAYER, MODEL_DIGEST)]
    )
    _write_manifest(
        root,
        "registry.ollama.ai/library/moondream",
        "latest",
        [(store.MODEL_LAYER, VISION_DIGEST), (store.PROJECTOR_LAYER, PROJECTOR_DIGEST)],
    )

    monkeypatch.setattr(store, "DEFAULT_ROOTS", ())
    monkeypatch.delenv(store.OLLAMA_ENV, raising=False)
    monkeypatch.setenv(store.STORE_ENV, str(root))
    return root


def test_a_library_model_is_named_the_way_ollama_names_it(one_store):
    """``registry.ollama.ai/library/qwen3/8b`` on disk is ``qwen3:8b`` on screen."""
    assert store.model_name(str(one_store), str(
        one_store / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / "8b"
    )) == "qwen3:8b"


def test_a_model_from_elsewhere_keeps_its_registry(tmp_path):
    """``ollama list`` shows the full path for anything outside the library."""
    manifest = tmp_path / "manifests" / "hf.co" / "bartowski" / "Qwen3-8B-GGUF" / "Q4_K_M"
    assert store.model_name(str(tmp_path), str(manifest)) == "hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M"


def test_the_manifest_pairs_the_projector(one_store, monkeypatch):
    """No name comparison: one manifest names both files, so the pair is certain."""
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header(vision=True))

    found = store.scan_captioners()
    assert len(found) == 1
    label, model, projector = found[0]
    assert label.startswith("moondream:latest [+mmproj, vision,")
    assert os.path.basename(model) == f"sha256-{VISION_DIGEST}"
    assert os.path.basename(projector) == f"sha256-{PROJECTOR_DIGEST}"


def test_a_writer_is_not_charged_for_a_projector_it_never_loads(one_store, monkeypatch):
    """The same model in both lists, and the sizes differ because the loads do.

    Caught on a real 9B: it read ``6.1 GB`` as a writer as well, which is the
    model plus an mmproj that the writer path never opens.
    """
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header(vision=True))

    entry = [one for one in store.entries() if one.name == "moondream:latest"][0]
    assert entry.size == pytest.approx(MODEL_BYTES / 1024 ** 3)
    assert entry.total == pytest.approx((MODEL_BYTES + PROJECTOR_BYTES) / 1024 ** 3)
    assert entry.size < entry.total


def test_a_text_model_is_a_writer_and_not_a_captioner(one_store, monkeypatch):
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header())

    labels = [label for label, _ in store.scan_writers()]
    assert any(label.startswith("qwen3:8b [qwen3,") for label in labels)
    assert "qwen3:8b" not in " ".join(label for label, _, _ in store.scan_captioners())


def test_an_embedding_model_is_not_offered_as_a_writer(one_store, monkeypatch):
    """A store holds whatever was pulled, and ``nomic-embed-text`` cannot write."""
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header(arch="nomic-bert"))
    assert store.scan_writers() == []


def test_a_blob_that_is_not_a_gguf_is_skipped(one_store, monkeypatch):
    """Unreadable is not fatal: the entry is left out, the list still builds."""
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header(arch="", kind=""))
    assert store.scan_writers() == []
    assert store.scan_captioners() == []


def test_two_tags_of_one_download_are_one_entry(one_store, monkeypatch):
    """``qwen3:8b`` and ``qwen3:latest`` share a blob; offering both is noise."""
    _write_manifest(
        one_store,
        "registry.ollama.ai/library/qwen3",
        "latest",
        [(store.MODEL_LAYER, MODEL_DIGEST)],
    )
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header())

    names = [entry.name for entry in store.entries()]
    assert names.count("qwen3:8b") + names.count("qwen3:latest") == 1


def test_a_missing_blob_drops_the_entry(one_store, monkeypatch):
    """A half-pulled model is in the manifests before it is in the blobs."""
    _write_manifest(
        one_store, "registry.ollama.ai/library/ghost", "7b", [(store.MODEL_LAYER, OTHER_DIGEST)]
    )
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header())
    assert "ghost:7b" not in [entry.name for entry in store.entries()]


def test_a_digest_cannot_walk_out_of_the_store(one_store, monkeypatch):
    """A manifest is a file, so its digest is checked rather than trusted."""
    outside = one_store.parent / "secret.gguf"
    outside.write_bytes(b"GGUF")
    escape = "../../" + outside.name
    _write_manifest(
        one_store, "registry.ollama.ai/library/evil", "1b", [(store.MODEL_LAYER, escape)]
    )
    monkeypatch.setattr(discovery, "gguf_header", lambda path: _header())

    assert store._blob(str(one_store), f"sha256:{escape}") == ""
    assert "evil:1b" not in [entry.name for entry in store.entries()]


def test_a_named_store_may_be_a_unc_path_and_an_unnamed_one_may_not(tmp_path, monkeypatch):
    """The hand-written path is the escape hatch; the automatic scan stays local.

    Reaching a UNC path is an act with consequences -- an authentication attempt
    against whatever host it names, and for ``\\\\wsl$`` a stopped virtual machine
    that starts. Doing it because somebody asked is fine; doing it while filling
    a dropdown is not.
    """
    monkeypatch.setattr(store, "DEFAULT_ROOTS", ())
    monkeypatch.delenv(store.STORE_ENV, raising=False)
    monkeypatch.setenv(store.OLLAMA_ENV, r"\\somehost\share\models")
    assert store.roots() == []

    reached = []

    def watch(path):
        reached.append(path)
        return False

    monkeypatch.setattr(store.os.path, "isdir", watch)
    monkeypatch.setenv(store.STORE_ENV, r"\\wsl$\Ubuntu\usr\share\ollama\.ollama\models")
    monkeypatch.delenv(store.OLLAMA_ENV, raising=False)
    store.roots()
    assert any("wsl$" in path for path in reached), "a named store must be looked at"


def test_a_store_without_manifests_is_not_a_store(tmp_path, monkeypatch):
    """Pointing at ``~/.ollama`` instead of ``~/.ollama/models`` is the usual slip."""
    monkeypatch.setattr(store, "DEFAULT_ROOTS", ())
    monkeypatch.delenv(store.OLLAMA_ENV, raising=False)
    monkeypatch.setenv(store.STORE_ENV, str(tmp_path))
    assert store.roots() == []
