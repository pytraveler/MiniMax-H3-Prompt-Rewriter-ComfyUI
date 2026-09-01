"""The official llama.cpp binaries, fetched on demand.

``llama-cpp-python`` is the fast path when it is already installed, and a
dependency nobody should have to fight when it is not. Its prebuilt CUDA wheels
fail on ordinary consumer hardware in two unrelated ways -- the ones through
``cu130`` are compiled with AVX-512 and die with ``0xC000001D`` on any consumer
Intel 12th-14th generation chip, and ``cu132`` drops AVX-512 but ships PTX that
a driver older than CUDA 13.2 refuses to compile -- and building a wheel from
source needs a compiler, a CUDA toolkit and an hour.

The upstream release archives have neither problem. They carry fourteen CPU
backend variants and pick one at run time, which is exactly why the same model
runs under ``llama-cli`` on a machine where the wheel crashes. The CUDA archive
carries native SASS and no PTX at all (``sm_86 sm_89 sm_120a sm_121a``), so the
driver never has to JIT anything.

So: if the wheel is there, use it; otherwise fetch about 30 MB of official
binaries and run the rewriter in a subprocess.

``auto`` takes CUDA where the card can actually run it and Vulkan everywhere
else. Vulkan is 32 MB against 511 MB, works on AMD and Intel too, and is the
only build upstream ships for Linux with any GPU acceleration at all, at roughly
half the tokens per second. Anyone who would rather keep the download small can
ask for it by name.

None of which applies when the machine already has an llama.cpp:
``MINIMAX_H3_LLAMA_BIN`` names one outright, ``llama_bin.txt`` in the user
directory says the same to a server whose environment you cannot set, PATH is
read when neither is there, and whatever is found is run as it is. That is the
only road to a CUDA llama.cpp on Linux, since no CUDA archive is published for
it.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tarfile
import zipfile

log = logging.getLogger(__name__)

#: Pinned so a workflow behaves the same next month. Raise it deliberately.
RELEASE = "b10310"
REPO = "ggml-org/llama.cpp"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/download/{RELEASE}"

BACKENDS = ("auto", "vulkan", "cuda", "cpu")
DEFAULT_BACKEND = "vulkan"

#: An llama.cpp this machine already has, named outright.
#:
#: Either the executable or the directory the binaries live in. Set it when
#: the wanted build is not the first one on PATH, or when there is no PATH
#: entry at all -- a container that runs ComfyUI as a service usually has
#: none. The captioner is looked for beside whatever this names, because
#: every build puts llama-mtmd-cli next to llama-completion.
BIN_ENV = "MINIMAX_H3_LLAMA_BIN"

#: The same thing written down, for a server whose environment is not yours.
#:
#: An export in a shell reaches a server started from that shell and nothing
#: else: a systemd unit, a container entrypoint or a launcher script hands the
#: process an environment of its own, and the variable above is simply absent
#: there. This file is read from ComfyUI's own user directory instead, beside
#: the model list -- one path in it, blank lines and '#' comments ignored.
BIN_FILE = "llama_bin.txt"

#: (sys.platform, backend) -> archive names within the release.
#:
#: The CUDA entry is two archives: the runtime libraries live in a separate
#: ``cudart`` download, and without them the CUDA backend silently fails to
#: load -- ``llama-cli --list-devices`` prints "(none)" and everything falls
#: back to the CPU. Verified here with a stripped PATH.
ASSETS: dict[tuple[str, str], tuple[str, ...]] = {
    ("win32", "vulkan"): (f"llama-{RELEASE}-bin-win-vulkan-x64.zip",),
    ("win32", "cuda"): (
        f"llama-{RELEASE}-bin-win-cuda-13.3-x64.zip",
        "cudart-llama-bin-win-cuda-13.3-x64.zip",
    ),
    ("win32", "cpu"): (f"llama-{RELEASE}-bin-win-cpu-x64.zip",),
    ("linux", "vulkan"): (f"llama-{RELEASE}-bin-ubuntu-vulkan-x64.tar.gz",),
    ("linux", "cpu"): (f"llama-{RELEASE}-bin-ubuntu-x64.tar.gz",),
    ("darwin", "cpu"): (f"llama-{RELEASE}-bin-macos-arm64.tar.gz",),
}

#: Upstream publishes no CUDA build for Linux at all -- only CPU, Vulkan, ROCm,
#: SYCL and OpenVINO. Asking for CUDA there is a mistake worth naming rather
#: than a silent fallback to something slower.
UNAVAILABLE = {
    ("linux", "cuda"): (
        "upstream publishes no CUDA build of llama.cpp for Linux; the Vulkan build "
        "runs on NVIDIA cards too, and one you compiled yourself is run as it is. "
        "llama-cpp-python is not one of those, whatever it was compiled with: the "
        "wheel is a set of shared libraries loaded from Python and carries no "
        "llama-completion and no llama-mtmd-cli at all, so there is nothing in it "
        "to name here. Build llama.cpp itself -- 'cmake -B build -DGGML_CUDA=ON' "
        "then 'cmake --build build --target llama-completion llama-mtmd-cli' -- and "
        f"give its bin folder to the node: write it into {BIN_FILE} in ComfyUI's "
        f"user directory, name it in {BIN_ENV}, or put it on PATH"
    ),
    ("darwin", "cuda"): "macOS has no CUDA; the macOS build uses Metal",
    ("darwin", "vulkan"): "upstream publishes no Vulkan build for macOS",
}

#: ``llama-completion``, emphatically not ``llama-cli``.
#:
#: As of b10310 ``llama-cli`` is an interactive terminal UI: it draws a spinner
#: and an ASCII banner *to stdout* and then waits, so a one-shot run never ends
#: and the caller reads 800 KB of animation frames instead of a rewrite.
#: ``llama-completion`` takes the same arguments and writes nothing but the
#: completion. Older builds shipped only ``llama-cli``, which behaved like
#: today's ``llama-completion``, so that name stays as a fallback.
EXE = ".exe" if sys.platform == "win32" else ""
BINARIES = (f"llama-completion{EXE}", f"llama-cli{EXE}")
BINARY = BINARIES[0]
SUBDIR = "runtime"

MTMD_BINARIES = (f"llama-mtmd-cli{EXE}",)

SERVER_BINARIES = (f"llama-server{EXE}",)


#: Compute capabilities the CUDA archive actually carries code for.
#:
#: It ships native SASS and *no PTX* -- ``cuobjdump --list-elf`` reports exactly
#: ``sm_86 sm_89 sm_120a sm_121a`` -- which is what makes it immune to the
#: driver-versus-toolkit problem that kills the cu132 wheel. The price is that a
#: card outside this set has nothing to run and nothing to JIT from, so 'auto'
#: must send it to Vulkan rather than to a 511 MB download it cannot use.
CUDA_CAPABILITIES = {(8, 6), (8, 9), (12, 0), (12, 1)}


def nvidia_capability() -> tuple[int, int] | None:
    """The compute capability of the card ComfyUI is using, if it is NVIDIA."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return tuple(torch.cuda.get_device_capability(0))
    except Exception:
        log.debug("[minimax_h3_rewriter.llamacpp] no CUDA device visible", exc_info=True)
        return None


def resolve_backend(backend: str) -> str:
    """Turn ``auto`` into the fastest backend this machine can actually run.

    Not the smallest download: on Windows with a supported NVIDIA card that
    means CUDA, which is roughly twice as fast as Vulkan and roughly fifteen
    times the download. Anyone who would rather keep the 34 MB version can pick
    ``vulkan`` explicitly.
    """
    if backend and backend != "auto":
        return backend
    capability = nvidia_capability()
    if capability in CUDA_CAPABILITIES and (sys.platform, "cuda") in ASSETS:
        return "cuda"
    if (sys.platform, DEFAULT_BACKEND) in ASSETS:
        return DEFAULT_BACKEND
    return "cpu"


def assets(backend: str) -> tuple[str, ...]:
    key = (sys.platform, backend)
    if key in UNAVAILABLE:
        raise RuntimeError(f"llama.cpp {backend}: {UNAVAILABLE[key]}.")
    names = ASSETS.get(key)
    if not names:
        raise RuntimeError(
            f"No llama.cpp {backend} build is published for {sys.platform}. "
            f"Available here: {', '.join(sorted(b for p, b in ASSETS if p == sys.platform))}."
        )
    return names


def root() -> str:
    """Where fetched runtimes live: beside the model list, not inside the pack.

    A pack directory is replaced wholesale when ComfyUI Manager updates a node,
    which would throw the download away and fetch it again. The user directory
    survives that.
    """
    try:
        import folder_paths

        base = folder_paths.get_user_directory()
    except Exception:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_user")
    return os.path.join(base, "minimax_h3_rewriter", SUBDIR)


def install_dir(backend: str) -> str:
    return os.path.join(root(), f"{RELEASE}-{backend}")


def bin_file() -> str:
    """``<ComfyUI user>/minimax_h3_rewriter/llama_bin.txt``, read if it is there."""
    return os.path.join(os.path.dirname(root()), BIN_FILE)


def find_binary(directory: str, names: tuple[str, ...] = BINARIES) -> str:
    """Locate the completion executable inside an unpacked release, at any depth."""
    for name in names:
        direct = os.path.join(directory, name)
        if os.path.isfile(direct):
            return direct
    for name in names:
        for current, _dirs, files in os.walk(directory):
            if name in files:
                return os.path.join(current, name)
    return ""


def installed(backend: str) -> str:
    """Path to the completion binary for this backend, or "" if not unpacked."""
    directory = install_dir(backend)
    return find_binary(directory) if os.path.isdir(directory) else ""


def _binary_at(value: str, names: tuple[str, ...], source: str) -> str:
    """Resolve a path someone gave us to one of ``names``, or say why not.

    A path that is given but points nowhere useful raises rather than falls
    through: naming a build is an instruction, and quietly downloading a
    different one instead would hide a typo behind half a gigabyte.
    """
    value = (value or "").strip().strip('"')
    if not value:
        return ""
    if EXE and not os.path.exists(value) and os.path.isfile(value + EXE):
        value += EXE

    if os.path.isfile(value):
        if os.path.basename(value) in names:
            return value
        beside = find_binary(os.path.dirname(os.path.abspath(value)), names)
        if beside:
            return beside
        raise RuntimeError(
            f"{source} is '{value}', and none of {', '.join(names)} is that file "
            f"or sits beside it."
        )
    if os.path.isdir(value):
        found = find_binary(value, names)
        if found:
            return found
        raise RuntimeError(
            f"{source} is '{value}', which holds none of {', '.join(names)}."
        )
    raise RuntimeError(f"{source} is '{value}', which does not exist.")


def _named_binary(names: tuple[str, ...]) -> str:
    """The binary ``BIN_ENV`` points at, or "" when the variable is unset."""
    return _binary_at(os.environ.get(BIN_ENV) or "", names, BIN_ENV)


def _file_binary(names: tuple[str, ...]) -> str:
    """The binary ``BIN_FILE`` names, or "" when there is no such file."""
    path = bin_file()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle]
    except OSError:
        return ""
    wanted = next((line for line in lines if line and not line.startswith("#")), "")
    return _binary_at(wanted, names, path)


def _path_binary(names: tuple[str, ...]) -> str:
    """The first of ``names`` on PATH, or "": an llama.cpp built on this machine."""
    for name in names:
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)
    return ""


_ANNOUNCED: set[str] = set()


def _announce(binary: str, source: str) -> str:
    if binary not in _ANNOUNCED:
        _ANNOUNCED.add(binary)
        log.info("[minimax_h3_rewriter.llamacpp] llama.cpp %s: %s", source, binary)
    return binary


def external(names: tuple[str, ...] = BINARIES) -> str:
    """An llama.cpp that is already here: given by name, or found on PATH."""
    return _named_binary(names) or _file_binary(names) or _path_binary(names)


def available() -> bool:
    """True when a runtime is reachable: unpacked here, or already on the machine."""
    if any(installed(backend) for _platform, backend in ASSETS if _platform == sys.platform):
        return True
    try:
        return bool(external())
    except RuntimeError:
        return False


def asset_size(name: str) -> int:
    """``Content-Length`` of one release asset, asked of the host that serves it.

    Not the releases API. That is rate limited to sixty anonymous requests an
    hour per address, and when the budget runs out it answers 403 with no sizes
    at all -- which leaves the progress bar without a denominator and therefore
    empty, exactly when a 511 MB download makes a progress bar worth having. A
    HEAD follows the redirect to release-assets.githubusercontent.com, carries
    no rate limit, and comes from the same place the bytes will.
    """
    import requests

    try:
        response = requests.head(
            f"{DOWNLOAD_URL}/{name}", allow_redirects=True, timeout=(15, 30)
        )
        if response.status_code >= 400:
            return 0
        return int(response.headers.get("Content-Length") or 0)
    except Exception:
        log.debug("[minimax_h3_rewriter.llamacpp] HEAD %s failed", name, exc_info=True)
        return 0


def asset_sizes(names: tuple[str, ...]) -> dict[str, int]:
    return {name: asset_size(name) for name in names}


def _safe_extract(archive: str, destination: str) -> None:
    """Unpack, refusing any member that would land outside the destination."""
    destination = os.path.abspath(destination)

    def inside(path: str) -> bool:
        target = os.path.abspath(os.path.join(destination, path))
        return target == destination or target.startswith(destination + os.sep)

    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            for member in handle.namelist():
                if not inside(member):
                    raise RuntimeError(f"refusing archive member outside the target: {member!r}")
            handle.extractall(destination)
        return

    def link_target_inside(member: tarfile.TarInfo) -> bool:
        """Whether a link points somewhere the extraction is allowed to reach.

        SONAME symlinks -- libllama.so.0 -> libllama.so.0.0.10310 -- ship in
        every Linux and macOS release, and the binaries link against the link
        rather than the file behind it, so refusing links outright refuses the
        archive. Only a target that leaves the destination is the zip-slip risk.

        The two kinds resolve from different places, and it matters. tarfile
        writes a symlink's linkname verbatim, so it is read relative to the
        link's own directory; a hardlink's is joined onto the extraction root
        instead. Using one base for both counts the link's own depth twice and
        waves through 'build/bin/x -> ../../secret', two levels above the
        destination, as though it were 'secret' inside it.
        """
        if os.path.isabs(member.linkname):
            return False
        base = os.path.dirname(member.name) if member.issym() else ""
        return inside(os.path.normpath(os.path.join(base, member.linkname)))

    with tarfile.open(archive) as handle:
        for member in handle.getmembers():
            if not inside(member.name):
                raise RuntimeError(f"refusing archive member outside the target: {member.name!r}")
            if (member.issym() or member.islnk()) and not link_target_inside(member):
                raise RuntimeError(
                    f"refusing archive link pointing outside the target: "
                    f"{member.name!r} -> {member.linkname!r}"
                )
        handle.extractall(destination)

    # tar releases unpack into build/bin/; leave the layout alone, find_binary
    # walks it, and the executable bit is already set by tarfile.


def _make_executable(directory: str) -> None:
    if sys.platform == "win32":
        return
    for current, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(current, name)
            if name.startswith("llama-") or name.endswith(".so") or ".so." in name:
                try:
                    os.chmod(path, os.stat(path).st_mode | 0o111)
                except OSError:
                    log.debug("[minimax_h3_rewriter.llamacpp] chmod failed: %s", path)


def download_size(backend: str) -> int:
    return sum(asset_sizes(assets(backend)).values())


def _packaged(
    backend: str, auto_download: bool, progress=None, wanted: tuple[str, ...] = BINARIES
) -> str:
    """The pack's own runtime: the unpacked one, or a fresh download of it.

    ``wanted`` is what the caller was actually looking for, and only reaches the
    refusal: the archive carries the whole set, so it is the same download
    either way, but a captioner told that PATH holds no ``llama-completion``
    reports a search nobody ran.
    """
    backend = resolve_backend(backend)
    existing = installed(backend)
    if existing:
        return existing

    try:
        names = assets(backend)
    except RuntimeError as error:
        raise RuntimeError(f"{error}\n\n{where_looked(backend, wanted)}") from error
    directory = install_dir(backend)

    if not auto_download:
        raise RuntimeError(
            f"The llama.cpp {backend} runtime is not in '{directory}' and auto_download is off. "
            f"Enable it, or unpack {', '.join(names)} from "
            f"https://github.com/{REPO}/releases/tag/{RELEASE} into that folder."
            f"\n\n{where_looked(backend, wanted)}"
        )

    from . import download

    if progress is not None:
        progress.text(f"Fetching llama.cpp {backend}\nasking for the download size", force=True)
    sizes = asset_sizes(names)
    # Partial knowledge is no knowledge: a denominator missing one of two
    # archives would drive the bar past 100% and then stop.
    total = sum(sizes.values()) if all(sizes.values()) else 0
    staging = directory + ".part"
    os.makedirs(staging, exist_ok=True)
    download.check_space(staging, total * 3 if total else 0)

    reporter = None
    if progress is not None and total:
        from .progress import TransferReporter

        reporter = TransferReporter(progress, total, f"Fetching llama.cpp {backend}")

    transferred = 0
    for name in names:
        destination = os.path.join(staging, name)
        expected = sizes.get(name, 0)
        # An earlier attempt that got through the first archive should not pay
        # for it twice; the half-finished one resumes from its own .part file.
        if expected and os.path.isfile(destination) and os.path.getsize(destination) == expected:
            transferred += expected
            if reporter is not None:
                reporter(transferred, name)
            continue

        task = download.FileTask(
            path=name,
            url=f"{DOWNLOAD_URL}/{name}",
            dest=destination,
            size=expected,
            already=0,
        )

        def report(position: int, label: str, _name=name) -> None:
            if reporter is not None:
                reporter(position, _name)
            elif progress is not None:
                # Say why the bar is not moving rather than leave it looking stuck.
                progress.text(
                    f"Fetching llama.cpp {backend} · {_name}\n"
                    f"{download.human_size(position)} (total size unknown, no bar)"
                )

        try:
            transferred += download.download_task(task, None, transferred, report)
        except download.DownloadError as error:
            raise RuntimeError(f"{error}\n\n{by_hand(backend)}") from error

    for index, name in enumerate(names, start=1):
        if progress is not None:
            progress.set_total(len(names))
            progress.update(index - 1, f"Unpacking llama.cpp {backend}\n{name} ({index}/{len(names)})")
        _safe_extract(os.path.join(staging, name), staging)
        os.remove(os.path.join(staging, name))
    if progress is not None:
        progress.update(len(names), f"Unpacking llama.cpp {backend}\ndone")
    _make_executable(staging)

    if not find_binary(staging):
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(
            f"The llama.cpp {backend} archive unpacked without any of {', '.join(BINARIES)} "
            f"in it. Release {RELEASE} may have changed its layout."
        )

    # Publish only once complete, so an interrupted download never leaves a
    # directory that looks installed.
    if os.path.isdir(directory):
        shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(os.path.dirname(directory), exist_ok=True)
    os.replace(staging, directory)

    binary = find_binary(directory)
    log.info("[minimax_h3_rewriter.llamacpp] %s runtime ready at %s", backend, binary)
    return binary


def where_looked(backend: str, names: tuple[str, ...] = BINARIES) -> str:
    """The places that were searched and what each held, as a block of text.

    "Put it on PATH" is useless advice to someone who did exactly that in a
    shell the server never saw, so a refusal reports the environment this
    process actually has rather than repeating the instruction.
    """
    named = os.environ.get(BIN_ENV)
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    shown = os.pathsep.join(entries)
    if len(shown) > 400:
        shown = shown[:400] + " ..."
    directory = install_dir(resolve_backend(backend))
    written = bin_file()

    lines = [
        f"Where this ComfyUI process (pid {os.getpid()}) looked:",
        f"  {BIN_ENV}: " + (f"'{named}'" if named else "not set in this process"),
        f"  {written}: " + ("read" if os.path.isfile(written) else "no such file"),
        f"  unpacked runtime: {directory}"
        + ("" if os.path.isdir(directory) else " (not there)"),
        f"  PATH: {len(entries)} entries, none holding {' or '.join(names)}",
        f"    {shown}",
    ]
    if not named:
        lines.append(
            "An export in your shell reaches a server started from that shell and "
            "nothing else -- a service is handed an environment of its own."
        )
    if sys.platform == "linux":
        lines.append(
            f"See what this process really has: tr '\\0' '\\n' "
            f"< /proc/{os.getpid()}/environ | grep -E '^(PATH|{BIN_ENV})='"
        )
    lines.append(
        f"The way that needs no environment at all: write the path to your build "
        f"into {written}, or put the build itself in {directory}."
    )
    return "\n".join(lines)


def by_hand(backend: str) -> str:
    """Installing the runtime without a connection, in full.

    A machine that is offline stays offline, and a stack of identical connection
    errors is not advice. The archive is public, small and needed only once, so
    the way through is the way the weights came: fetch it somewhere else and
    unpack it where this looked.
    """
    backend = resolve_backend(backend)
    names = ASSETS.get((sys.platform, backend), ())
    lines = [
        "This runtime can also be installed by hand, which is the way through on a "
        "machine with no connection. Fetch these on one that has:",
        *[f"  {DOWNLOAD_URL}/{name}" for name in names],
        "and unpack them into:",
        f"  {install_dir(backend)}",
        "That folder name is where the node looks, so nothing else is needed, and "
        "the layout inside the archive does not matter -- the binaries are found at "
        "any depth.",
    ]
    if sys.platform != "win32":
        lines.append("Unpack with 'tar -xf', not a file manager: the archive ships its "
                     "shared libraries as symlinks and they have to stay symlinks.")
    return "\n".join(lines)


def ensure(backend: str, auto_download: bool, progress=None) -> str:
    """Return the path to the completion binary, fetching the release if needed.

    Five places, in this order: what ``BIN_ENV`` names and what ``BIN_FILE``
    names, because giving a path is an instruction; then what this pack has
    already unpacked, which is the pinned, known-good one; then PATH, so an
    llama.cpp compiled on this machine is used rather than a second copy
    downloaded beside it; then the download.

    Those given paths are the only road to a CUDA llama.cpp on Linux, where
    upstream publishes no CUDA archive at all. They are also why
    ``llama_backend`` stops mattering once a build is found: it picks which
    archive to fetch, not what an existing binary was compiled against. When
    there is no archive to fetch either, the refusal carries the search rather
    than repeating advice the reader may already have taken.
    """
    named = _named_binary(BINARIES)
    if named:
        return _announce(named, f"named in {BIN_ENV}")

    written = _file_binary(BINARIES)
    if written:
        return _announce(written, f"named in {bin_file()}")

    backend = resolve_backend(backend)
    existing = installed(backend)
    if existing:
        return existing

    on_path = _path_binary(BINARIES)
    if on_path:
        return _announce(on_path, "found on PATH")

    return _packaged(backend, auto_download, progress)


def wheel_cannot_caption() -> str:
    """Why an installed ``llama-cpp-python`` spares a caption run nothing.

    "But I already have llama-cpp-python" is the reasonable thought of anyone
    watching a captioner fetch 32 MB of binaries, and it deserves an answer
    before the download rather than after it fails. A reference asset goes
    through ``llama-mtmd-cli``, a program; the wheel is shared libraries loaded
    by ctypes and ships no executables at all, a CUDA build compiled from source
    included. Empty when there is no wheel, so a caller can print it blindly.
    """
    from . import gguf_engine

    if not gguf_engine.available():
        return ""
    return (
        "llama-cpp-python is installed here and cannot do this job: a reference asset "
        f"goes through {MTMD_BINARIES[0]}, a program, and the wheel is a set of shared "
        "libraries with no executables in it -- however it was compiled. It runs the "
        "writer nodes; the caption nodes need an llama.cpp build, which is what this is "
        "for."
    )


def ensure_mtmd(backend: str, auto_download: bool, progress=None) -> str:
    """Return the path to ``llama-mtmd-cli``, fetching the release if needed.

    The same five places in the same order, since the captioner is built beside
    the completion binary and ships in the same archive: a captioner run on a
    machine whose rewrites went through the binaries downloads nothing.

    Rewrites that did not are the common case, though, and they leave nothing
    here to find: ``gguf_runtime`` defaults to the wheel wherever it imports,
    and a safetensors base never touches llama.cpp at all. Neither has ever
    fetched an archive, so the first caption is what pays for it -- which is
    what ``wheel_cannot_caption`` is there to say before the download starts.

    The other case is a build that has one binary and not the other, which a
    distribution package often is. That is no reason to go without: the archive
    carries both, so it is fetched for this job even though the rewriter runs
    from PATH.
    """
    named = _named_binary(MTMD_BINARIES)
    if named:
        return _announce(named, f"named in {BIN_ENV}")

    written = _file_binary(MTMD_BINARIES)
    if written:
        return _announce(written, f"named in {bin_file()}")

    directory = install_dir(resolve_backend(backend))
    unpacked = find_binary(directory, MTMD_BINARIES) if os.path.isdir(directory) else ""
    if unpacked:
        return unpacked

    on_path = _path_binary(MTMD_BINARIES)
    if not on_path:
        companion = _path_binary(BINARIES)
        if companion:
            on_path = find_binary(os.path.dirname(companion), MTMD_BINARIES)
    if on_path:
        return _announce(on_path, "found on PATH")

    note = wheel_cannot_caption()
    if note:
        log.info("[minimax_h3_rewriter.llamacpp] %s", note)
    try:
        _packaged(backend, auto_download, progress, MTMD_BINARIES)
    except RuntimeError as error:
        raise RuntimeError(f"{error}\n\n{note}".rstrip()) from error
    binary = find_binary(directory, MTMD_BINARIES)
    if not binary:
        raise RuntimeError(
            f"'{MTMD_BINARIES[0]}' is not in '{directory}', not on PATH and not where "
            f"{BIN_ENV} points. Release {RELEASE} should carry it beside {BINARY}; delete "
            f"that folder to fetch the archive again, or build that target in your own "
            f"llama.cpp."
        )
    return binary


def server_beside(captioner: str) -> str:
    """``llama-server`` from the same build as ``captioner``, or "" if absent.

    Deliberately none of the searching the two functions above do. Those resolve
    a runtime the node cannot work without, so they look in five places and
    download when all five come up empty. This one resolves a runtime the node
    is merely faster with, and a search that ranged wider could pair the model
    with a *different* build's server -- an older llama.cpp whose mtmd cannot
    read the projector this one just accepted. Beside the captioner, or not at
    all: an empty answer costs a caption run its speed and nothing else.
    """
    if not captioner:
        return ""
    directory = os.path.dirname(os.path.abspath(captioner))
    for name in SERVER_BINARIES:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return _announce(candidate, "captioning server")
    log.info(
        "[minimax_h3_rewriter.llamacpp.server_beside] no %s beside %s: references will be "
        "described one process at a time",
        SERVER_BINARIES[0], captioner,
    )
    return ""
