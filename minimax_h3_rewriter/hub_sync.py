"""The same downloads, moved by ``huggingface_hub`` instead of by hand.

``download.py`` fetches one ranged connection at a time, which is the honest
floor for a transfer nobody has to install anything for. The Comfy-Org
repositories are Xet-backed, though, and ``hf_xet`` fetches the chunks of a
*single* file over many connections at once -- which is the whole of the
difference somebody reports as "the CLI took five minutes and the node took an
hour". This module is that second route, chosen by the ``downloader`` option.

Three things are load-bearing:

- **Nothing here is imported until it is used.** The release workflow installs
  ``requests`` and nothing else, then imports ``nodes``; ``huggingface_hub`` is
  not a dependency of this pack and must never become one on that path. Module
  level here is stdlib plus ``download``.
- **The two backends select the same files.** ``select_files`` is called here
  too, rather than translating ``allow`` into ``allow_patterns`` globs -- that
  translation is lossy in two places (a bare file name matches at any depth
  here but only at the root under fnmatch, and the suffix test here is
  case-insensitive), and two backends that disagree about *which* files to
  fetch would be a bug nobody could see.
- **Files already on disk never reach huggingface_hub.** A finished LFS file
  sitting in ``local_dir`` with no hub metadata beside it makes hub sha256 the
  whole thing to adopt it: fifty gigabytes of hashing, on this thread, with no
  progress reported. Deciding what is missing ourselves skips that entirely.

The per-file loop is deliberate rather than ``snapshot_download``: that one
hands the same ``tqdm_class`` to a byte bar and a file-count bar and drives the
per-file ones through a private aggregate, so a caption saying which file is
moving cannot be built from it. The parallelism given up is *between* files;
the Xet win is inside one, and that is the part worth having.

Verified against huggingface_hub 1.14.0.
"""

from __future__ import annotations

import logging
import os
import threading

from . import download
from .constants import DOWNLOADER_BUILTIN, install_command

log = logging.getLogger(__name__)

PACKAGES = ("huggingface_hub", "hf_xet")

INSTALL_HINT = (
    "The 'huggingface_hub' downloader is not available in this Python environment:\n"
    f"    {install_command(' '.join(PACKAGES))}\n"
    "Then restart ComfyUI.\n\n"
    "Installing it is optional. With 'downloader' on its default the nodes move the bytes "
    "themselves and need nothing extra; this route is worth having for the large diffusion "
    "weights, because the Comfy-Org repositories are Xet-backed and hf_xet fetches the chunks "
    "of one file over many connections at once."
)


def available() -> bool:
    """Whether this route can run at all, without importing anything heavy.

    ``hf_xet`` is not required: huggingface_hub works without it and is still
    the faster of the two on a slow single connection. It is only what makes
    the difference large, which is why it is named in the hint and not here.
    """
    import importlib.util

    try:
        return all(
            importlib.util.find_spec(name) is not None
            for name in ("huggingface_hub", "tqdm")
        )
    except (ImportError, ValueError):
        return False


def _hub():
    try:
        import huggingface_hub
    except ImportError as error:
        raise download.DownloadError(INSTALL_HINT) from error
    return huggingface_hub


def _is_interrupt(error: BaseException) -> bool:
    """Whether this is ComfyUI's cancel button rather than a failed transfer.

    It must travel out untouched: wrapped in a DownloadError it would be
    reported as a download that went wrong, and retried by anything that
    retries.
    """
    try:
        import comfy.model_management
    except ImportError:
        return False
    return isinstance(error, comfy.model_management.InterruptProcessingException)


def _destination(dest_dir: str, repo_path: str) -> str:
    """Where a repository file lands -- the same place ``build_tasks`` puts it."""
    return os.path.join(dest_dir, *download._safe_parts(repo_path))


def _finished(dest_dir: str, item) -> bool:
    path = _destination(dest_dir, item.path)
    if not os.path.isfile(path):
        return False
    local = os.path.getsize(path)
    return local == item.size if item.size else local > 0


class _Position:
    """Per-file byte counts, reported as one rising position across the repository.

    ``on_progress`` is documented in absolute cumulative bytes and every caller
    -- ``TransferReporter`` above all -- assumes it, so each file's own bar has
    to be offset by everything already transferred before it.

    It also has to be made monotonic, which is not obvious until you watch it.
    On the Xet path huggingface_hub opens **two** bars for one file, "reconstructing
    file" and "downloading bytes", and the second is *smaller* than the first
    whenever chunks are deduplicated or already cached. Both are handed the same
    tqdm_class, so reporting each one as it moves walks the progress bar
    backwards. Taking the high-water mark is the honest reading of "how much of
    this file is in place", and the exact figure is reported by ``sync_repo``
    once the file is finished regardless.

    The updates arrive on hf_xet's own worker threads, which is why the counter
    is locked and why the interrupt is only raised from the main one.
    """

    def __init__(self, on_progress):
        self.on_progress = on_progress
        self.base = 0
        self.name = ""
        self.high = 0
        self._lock = threading.Lock()

    def begin(self, base: int, name: str) -> None:
        self.base = base
        self.name = name
        self.high = base

    def moved(self, position: int) -> None:
        if threading.current_thread() is threading.main_thread():
            download._interrupted()
        place = self.base + position
        with self._lock:
            if place <= self.high:
                return
            self.high = place
        self.on_progress(place, self.name)


def _bar_class(position: _Position):
    """A tqdm that draws nothing and counts for us.

    Two behaviours of the pieces this sits between make the obvious version
    wrong. ``huggingface_hub`` builds the bar as ``cls(**kwargs)`` without
    passing ``disable``, so an ordinary subclass prints a progress bar into
    ComfyUI's console. And ``tqdm.update`` returns *before* ``self.n += n``
    when it is disabled, so the counter has to be kept here.
    """
    from tqdm.auto import tqdm as _tqdm

    class Bar(_tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = True
            try:
                super().__init__(*args, **kwargs)
            except TypeError:
                super().__init__(disable=True)

        def update(self, n=1):
            self.n += int(n or 0)
            position.moved(self.n)

    return Bar


def sync_repo(
    repo_id: str,
    dest_dir: str,
    allow: tuple[str, ...] | None = None,
    skip_suffixes: tuple[str, ...] = (),
    revision: str = "main",
    on_progress=None,
    on_status=None,
    on_total=None,
) -> dict:
    """Mirror the selected files of ``repo_id`` into ``dest_dir``.

    Signature, callbacks and return value are ``download.sync_repo``'s, so the
    two are interchangeable at the one call site that chooses between them.
    """
    token = download.access_token()
    if on_status is not None:
        on_status(f"Listing {repo_id}")

    files = download.select_files(
        download.list_repo_files(repo_id, revision, token), allow, skip_suffixes
    )
    if not files:
        raise download.DownloadError(f"No matching files found in '{repo_id}'.")

    os.makedirs(dest_dir, exist_ok=True)

    total = sum(item.size for item in files)
    transferred = 0
    pending = []
    for item in files:
        if _finished(dest_dir, item):
            transferred += item.size
        else:
            pending.append(item)

    download.check_space(dest_dir, total - transferred)

    if on_total is not None:
        on_total(total)
    if on_progress is None:
        on_progress = lambda *_: None
    on_progress(transferred, "")

    if not pending:
        on_progress(total, "")
        return _result(repo_id, dest_dir, files, pending, total)

    hub = _hub()
    position = _Position(on_progress)
    bar = _bar_class(position)
    endpoint = download.endpoint()

    for item in pending:
        download._interrupted()
        name = item.path.split("/")[-1]
        position.begin(transferred, name)
        try:
            hub.hf_hub_download(
                repo_id=repo_id,
                filename=item.path,
                revision=revision,
                repo_type="model",
                local_dir=dest_dir,
                token=token,
                endpoint=endpoint,
                library_name=download.USER_AGENT,
                tqdm_class=bar,
            )
        except Exception as error:
            if _is_interrupt(error):
                raise
            raise download.DownloadError(
                f"'{item.path}' could not be fetched from '{repo_id}' through "
                f"huggingface_hub: {error}\n\n"
                f"Setting 'downloader' back to '{DOWNLOADER_BUILTIN}' on the options node uses "
                f"this pack's own transfer instead, which needs nothing installed."
            ) from error
        transferred += item.size
        on_progress(transferred, name)

    on_progress(total, "")
    return _result(repo_id, dest_dir, files, pending, total)


def _result(repo_id: str, dest_dir: str, files: list, pending: list, total: int) -> dict:
    log.info(
        "[minimax_h3_rewriter.hub_sync] %s: %d of %d files fetched into %s",
        repo_id, len(pending), len(files), dest_dir,
    )
    return {
        "repo_id": repo_id,
        "dir": dest_dir,
        "files": len(files),
        "downloaded_files": len(pending),
        "total_bytes": total,
    }
