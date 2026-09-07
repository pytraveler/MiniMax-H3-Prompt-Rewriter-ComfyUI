"""The second download backend, as far as it can be tested without the network.

The package is registered by hand rather than imported: running its
``__init__.py`` would pull in nodes.py and with it ComfyUI, which a test run
does not have.

What is worth pinning here is not the transfer -- that needs Hugging Face --
but the three things around it that are easy to break silently: that
``huggingface_hub`` is never imported on a path the release workflow walks,
that the progress bar counts although it is disabled, and that a repository
already on disk asks nothing of either.
"""

import ast
import importlib
import pathlib
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

download = importlib.import_module(f"{_PKG}.download")
hub_sync = importlib.import_module(f"{_PKG}.hub_sync")

SOURCE = _ROOT / _PKG / "hub_sync.py"


def files(*pairs):
    return [download.RepoFile(path=path, size=size) for path, size in pairs]


def put(directory, item):
    """Write a file of the expected size where the backend will look for it."""
    path = pathlib.Path(directory, *download._safe_parts(item.path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * item.size)


class Calls:
    """The three callbacks, recorded."""

    def __init__(self):
        self.progress = []
        self.status = []
        self.total = []

    def kwargs(self):
        return {
            "on_progress": lambda position, name: self.progress.append((position, name)),
            "on_status": self.status.append,
            "on_total": self.total.append,
        }


def test_the_hub_is_not_imported_at_module_level():
    """The release workflow installs requests and imports the pack.

    A top-level ``import huggingface_hub`` here would fail that import and take
    every node in the pack down with it, so the rule is checked in the source
    rather than trusted.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    named = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            named += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            named.append(node.module or "")
    assert not [name for name in named if name.split(".")[0] in {"huggingface_hub", "hf_xet", "tqdm"}]


def test_install_hint_names_this_interpreter():
    assert sys.executable.split("\\")[-1].split("/")[-1] in hub_sync.INSTALL_HINT
    assert "huggingface_hub" in hub_sync.INSTALL_HINT


def test_available_answers_a_bool_either_way():
    assert isinstance(hub_sync.available(), bool)


def test_the_signature_matches_the_built_in_backend():
    """They are swapped at one call site, so they take the same call."""
    import inspect

    assert list(inspect.signature(hub_sync.sync_repo).parameters) == list(
        inspect.signature(download.sync_repo).parameters
    )


def test_the_bar_never_draws_whatever_it_is_asked():
    pytest.importorskip("tqdm")
    bar = hub_sync._bar_class(hub_sync._Position(lambda *_: None))
    assert bar(total=10, disable=False).disable is True


def test_the_bar_counts_although_it_is_disabled():
    """tqdm's own update returns before it adds anything when disabled."""
    pytest.importorskip("tqdm")
    bar = hub_sync._bar_class(hub_sync._Position(lambda *_: None))
    counter = bar(total=500, initial=7)
    counter.update(5)
    counter.update(3)
    assert counter.n == 15


def test_the_bar_reports_positions_across_the_whole_repository():
    seen = []
    pytest.importorskip("tqdm")
    position = hub_sync._Position(lambda place, name: seen.append((place, name)))
    position.begin(1000, "second.bin")
    counter = hub_sync._bar_class(position)(total=500, initial=0)
    counter.update(12)
    assert seen == [(1012, "second.bin")]


def test_two_bars_on_one_file_do_not_walk_the_position_backwards():
    """The Xet path opens "reconstructing file" and "downloading bytes" at once.

    Both are handed the same tqdm_class, and the second counts fewer bytes than
    the first whenever chunks are deduplicated or already cached -- so reporting
    each as it moves runs the progress bar backwards. Seen live: 512120 then
    451576 for one 512 KB file.
    """
    pytest.importorskip("tqdm")
    seen = []
    position = hub_sync._Position(lambda place, name: seen.append(place))
    position.begin(0, "one.safetensors")
    bar = hub_sync._bar_class(position)
    rebuilding = bar(total=500)
    off_the_wire = bar(total=500)

    rebuilding.update(500)
    off_the_wire.update(300)
    assert seen == [500]


def test_the_mark_resets_for_the_next_file():
    pytest.importorskip("tqdm")
    seen = []
    position = hub_sync._Position(lambda place, name: seen.append((place, name)))
    bar = hub_sync._bar_class(position)

    position.begin(0, "one.bin")
    bar(total=500).update(500)
    position.begin(500, "two.bin")
    bar(total=200).update(10)
    assert seen == [(500, "one.bin"), (510, "two.bin")]


def test_the_bar_survives_a_keyword_tqdm_does_not_take():
    """Losing the caption is survivable; losing the download for it is not."""
    seen = []
    pytest.importorskip("tqdm")
    position = hub_sync._Position(lambda place, name: seen.append((place, name)))
    counter = hub_sync._bar_class(position)(total=10, no_such_keyword=1)
    counter.update(4)
    assert seen == [(4, "")]


def test_a_complete_repository_downloads_nothing(tmp_path, monkeypatch):
    """And asks nothing of huggingface_hub, which may not be installed at all."""
    listing = files(("model.safetensors", 12), ("nested/config.json", 4))
    monkeypatch.setattr(download, "list_repo_files", lambda *a, **kw: listing)
    monkeypatch.setattr(hub_sync, "_hub", lambda: pytest.fail("the hub was reached"))
    for item in listing:
        put(tmp_path, item)

    calls = Calls()
    made = hub_sync.sync_repo("some/repo", str(tmp_path), **calls.kwargs())

    assert made["downloaded_files"] == 0
    assert made["files"] == 2
    assert made["total_bytes"] == 16
    assert calls.total == [16]
    assert calls.progress[-1] == (16, "")
    assert calls.status == ["Listing some/repo"]


def test_a_file_of_the_wrong_size_is_not_taken_for_finished(tmp_path):
    """Half a file on disk is a resume, not a hit -- and the sizes come from the listing."""
    item = download.RepoFile(path="model.safetensors", size=12)
    pathlib.Path(tmp_path, "model.safetensors").write_bytes(b"short")
    assert hub_sync._finished(str(tmp_path), item) is False
    pathlib.Path(tmp_path, "model.safetensors").write_bytes(b"x" * 12)
    assert hub_sync._finished(str(tmp_path), item) is True


def test_a_file_lands_where_the_built_in_backend_would_put_it(tmp_path):
    """The two must agree about the path, or each would re-fetch the other's work."""
    item = download.RepoFile(path="nested/config.json", size=4)
    tasks = download.build_tasks("some/repo", str(tmp_path), [item])
    assert hub_sync._destination(str(tmp_path), item.path) == tasks[0].dest


@pytest.mark.parametrize(
    "allow, skip, wanted",
    [
        (("model.safetensors",), (), ["model.safetensors"]),
        (("model.gguf",), (), ["nested/model.gguf"]),          # a bare name, at any depth
        (("nested/model.gguf",), (), ["nested/model.gguf"]),
        (None, (".md",), ["model.safetensors", "nested/model.gguf"]),  # and case-insensitively
    ],
)
def test_the_selection_is_the_built_in_backend_s_own(tmp_path, monkeypatch, allow, skip, wanted):
    """Translated into fnmatch globs these four would not all come out the same.

    A bare file name matches at any depth here but only at the root under
    fnmatch, and the suffix test here ignores case. Calling ``select_files``
    rather than translating is what keeps the two backends fetching the same
    set, so that is what is pinned.
    """
    listing = files(
        ("model.safetensors", 1), ("nested/model.gguf", 2), ("README.MD", 3),
    )
    monkeypatch.setattr(download, "list_repo_files", lambda *a, **kw: listing)
    monkeypatch.setattr(hub_sync, "_hub", lambda: pytest.fail("the hub was reached"))
    for item in listing:
        put(tmp_path, item)

    made = hub_sync.sync_repo("some/repo", str(tmp_path), allow=allow, skip_suffixes=skip)
    assert made["files"] == len(wanted)
    assert made["total_bytes"] == sum(
        item.size for item in listing if item.path in wanted
    )


def test_an_empty_selection_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "list_repo_files", lambda *a, **kw: files(("a.bin", 1)))
    with pytest.raises(download.DownloadError):
        hub_sync.sync_repo("some/repo", str(tmp_path), allow=("nothing.bin",))


def test_a_missing_hub_is_a_download_error_not_an_import_error(monkeypatch):
    """The message has to reach the node, and only DownloadError does."""
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(download.DownloadError) as raised:
        hub_sync._hub()
    assert "huggingface_hub" in str(raised.value)
