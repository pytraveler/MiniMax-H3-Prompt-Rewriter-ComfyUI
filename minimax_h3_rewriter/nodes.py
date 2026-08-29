"""The ComfyUI nodes exposed by this package."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import re

from . import (
    aspect,
    catalog,
    cli_engine,
    clip_caption,
    devices,
    discovery,
    download,
    engine,
    gguf_engine,
    guide_prompt,
    guides,
    llamacpp,
    media,
    mtmd_engine,
)
from .catalog import FORMAT_GGUF, FORMAT_TRANSFORMERS
from .constants import (
    ADAPTER_FILES,
    ADAPTER_REPO,
    ATTN_IMPLEMENTATIONS,
    BASE_MODEL_REPO,
    BASE_SKIP_SUFFIXES,
    DURATION_MAX,
    DURATION_MIN,
    GGUF_RUNTIMES,
    MERGE_AUTO,
    MERGE_LORA,
    NO_REASONING,
    OUTPUT_FIELDS,
    QUANTIZATIONS,
    REF_OUTPUT_FIELDS,
    RESOLUTIONS,
    RUNTIME_AUTO,
    RUNTIME_BINARY,
    RUNTIME_WHEEL,
)
from .fields import missing, split_fields, split_sections
from .paths import (
    adapter_is_complete,
    base_model_is_complete,
    catalog_file,
    models_root,
    resolve_source,
)
from .progress import NodeProgress, TransferReporter
from .prompt_template import build_messages

log = logging.getLogger(__name__)

CATEGORY = "MiniMax-H3"
OPTIONS_TYPE = "H3_REWRITER_OPTIONS"

DEFAULT_OPTIONS = {
    "max_new_tokens": 2048,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "repetition_penalty": 1.05,
    "attn_implementation": "sdpa",
    "adapter": ADAPTER_REPO,
    "use_lora": True,
    "merge_lora": MERGE_AUTO,
    "auto_download": True,
    "gpu_layers": -1,
    "n_ctx": 8192,
    "gguf_runtime": RUNTIME_AUTO,
    "llama_backend": "auto",
    "device": devices.AUTO,
    "trust_remote_code": False,
}

BASE_SPEC = {
    "default_repo": BASE_MODEL_REPO,
    "complete": base_model_is_complete,
    "allow": None,
    "skip_suffixes": BASE_SKIP_SUFFIXES,
    "label": "Base model",
}
ADAPTER_SPEC = {
    "default_repo": ADAPTER_REPO,
    "complete": adapter_is_complete,
    "allow": ADAPTER_FILES,
    "skip_suffixes": (),
    "label": "Prompt-rewriter LoRA",
}

LOCAL_PREFIX = "on disk: "


@dataclass
class Choice:
    """Where a chosen model lives and how it has to be run."""

    reference: str
    fmt: str = FORMAT_TRANSFORMERS
    file: str = ""
    local: bool = False


_MODEL_MAP: dict[str, Choice] = {}


def _build_model_map() -> dict[str, Choice]:
    mapping: dict[str, Choice] = {}
    try:
        for entry in catalog.load():
            mapping[entry.label] = Choice(reference=entry.repo, fmt=entry.fmt, file=entry.file)
    except Exception:
        log.warning("[minimax_h3_rewriter._build_model_map] catalog unreadable", exc_info=True)
    try:
        for label, path in discovery.scan_local():
            mapping[f"{LOCAL_PREFIX}{label}"] = Choice(reference=path, local=True)
    except Exception:
        log.warning("[minimax_h3_rewriter._build_model_map] local scan failed", exc_info=True)
    try:
        for label, path in discovery.scan_local_gguf():
            mapping[f"{LOCAL_PREFIX}{label}"] = Choice(reference=path, fmt=FORMAT_GGUF, local=True)
    except Exception:
        log.warning("[minimax_h3_rewriter._build_model_map] gguf scan failed", exc_info=True)

    _MODEL_MAP.clear()
    _MODEL_MAP.update(mapping)
    return mapping


PROBLEM_PREFIX = "!! "


def _announce(choices: list[str]) -> list[str]:
    """Put a broken model list at the top of the dropdown, where it is unmissable.

    First, so it becomes the default on a new node and cannot be scrolled past.
    The entries below it are the packaged defaults, which is exactly the state
    that used to be indistinguishable from "my edit did nothing".
    """
    trouble = catalog.problem()
    if not trouble:
        return choices
    return [f"{PROBLEM_PREFIX}{trouble} — showing the packaged list instead"] + choices


def _refuse_problem(choice: str) -> None:
    if choice.startswith(PROBLEM_PREFIX):
        raise RuntimeError(
            f"{choice[len(PROBLEM_PREFIX):]}\n\nFix {catalog.user_file()} — the 'Open model "
            f"list' button opens it — then refresh the browser tab. ComfyUI need not restart."
        )


def model_choices() -> list[str]:
    choices = list(_build_model_map())
    return _announce(choices or [BASE_MODEL_REPO])


def _resolve_model_choice(choice: str) -> Choice:
    _refuse_problem(choice)
    found = _MODEL_MAP.get(choice)
    if found is None:
        found = _build_model_map().get(choice)
    if found is not None:
        return found
    if choice and choice.lower().endswith(".gguf") and os.path.isfile(choice):
        return Choice(reference=choice, fmt=FORMAT_GGUF, local=True)
    if choice and ("/" in choice or os.path.isabs(choice)):
        return Choice(reference=choice)
    raise RuntimeError(
        f"'{choice}' is not in the model list any more. Pick another entry, or add it back "
        f"with the 'Open model list' button ({catalog.user_file()})."
    )


_WRITER_MAP: dict[str, Choice] = {}


def _build_writer_map() -> dict[str, Choice]:
    """The guided writers' model list: any GGUF, not just the adapter's base.

    Nothing here is verified against an architecture, because there is nothing to
    match: the format lives in the system prompt, so the only requirement is that
    the file is a GGUF language model with a chat template. That is what makes a
    4B on an 8 GB card a real answer rather than a consolation prize.
    """
    mapping: dict[str, Choice] = {}
    try:
        for entry in catalog.writers():
            mapping[entry.label] = Choice(reference=entry.repo, fmt=FORMAT_GGUF, file=entry.file)
    except Exception:
        log.warning("[minimax_h3_rewriter._build_writer_map] catalog unreadable", exc_info=True)
    try:
        for label, path in discovery.scan_writer_gguf():
            mapping[f"{LOCAL_PREFIX}{label}"] = Choice(reference=path, fmt=FORMAT_GGUF, local=True)
    except Exception:
        log.warning("[minimax_h3_rewriter._build_writer_map] gguf scan failed", exc_info=True)

    _WRITER_MAP.clear()
    _WRITER_MAP.update(mapping)
    return mapping


def writer_choices() -> list[str]:
    choices = list(_build_writer_map())
    return _announce(choices or ["(no GGUF model found — see the model list)"])


def _resolve_writer_choice(choice: str) -> Choice:
    _refuse_problem(choice)
    found = _WRITER_MAP.get(choice)
    if found is None:
        found = _build_writer_map().get(choice)
    if found is not None:
        return found
    if choice and choice.lower().endswith(".gguf") and os.path.isfile(choice):
        return Choice(reference=choice, fmt=FORMAT_GGUF, local=True)
    raise RuntimeError(
        f"'{choice}' is not in the writer model list any more. Pick another entry, drop a "
        f"'.gguf' into ComfyUI's models/LLM folder, or add it under \"writers\" in "
        f"{catalog.user_file()}."
    )


@dataclass
class CaptionerChoice:
    """A captioner is two files that have to come from the same conversion."""

    reference: str
    file: str = ""
    mmproj: str = ""
    local: bool = False


_CAPTIONER_MAP: dict[str, CaptionerChoice] = {}


def _build_captioner_map() -> dict[str, CaptionerChoice]:
    mapping: dict[str, CaptionerChoice] = {}
    try:
        for entry in catalog.captioners():
            if not entry.mmproj:
                log.warning(
                    "[minimax_h3_rewriter._build_captioner_map] '%s' has no 'mmproj', skipping",
                    entry.name,
                )
                continue
            mapping[entry.label] = CaptionerChoice(
                reference=entry.repo, file=entry.file, mmproj=entry.mmproj
            )
    except Exception:
        log.warning("[minimax_h3_rewriter._build_captioner_map] catalog unreadable", exc_info=True)
    try:
        for label, model_path, mmproj_path in discovery.scan_captioner_gguf():
            mapping[f"{LOCAL_PREFIX}{label}"] = CaptionerChoice(
                reference=model_path, mmproj=mmproj_path, local=True
            )
    except Exception:
        log.warning("[minimax_h3_rewriter._build_captioner_map] gguf scan failed", exc_info=True)

    _CAPTIONER_MAP.clear()
    _CAPTIONER_MAP.update(mapping)
    return mapping


def captioner_choices() -> list[str]:
    choices = list(_build_captioner_map())
    return _announce(choices or ["(no multimodal model found — see the model list)"])


def _resolve_captioner_choice(choice: str) -> CaptionerChoice:
    _refuse_problem(choice)
    found = _CAPTIONER_MAP.get(choice)
    if found is None:
        found = _build_captioner_map().get(choice)
    if found is not None:
        return found
    raise RuntimeError(
        f"'{choice}' is not in the captioner list any more. Pick another entry, put a '.gguf' "
        f"and its 'mmproj' together in one folder under ComfyUI's models/LLM, or add it under "
        f"\"captioners\" in {catalog.user_file()}."
    )


def _verify_base_model(
    reference: str,
    progress: NodeProgress,
    shape: discovery.Shape = discovery.SHAPE_27B,
    default_repo: str = BASE_MODEL_REPO,
) -> None:
    """Refuse a wrong or unloadable base model before any weights move.

    ``shape`` is which adapter is about to be applied. The two are cut to
    different checkpoints, and a check that always answered for the 27B would
    wave through a Qwen3-VL of the wrong size and then fail long afterwards,
    with the download already paid for.
    """
    if os.path.isdir(reference):
        report = discovery.inspect_local(reference, shape)
    else:
        repo_id, local_dir = resolve_source(reference, default_repo)
        if os.path.isdir(local_dir) and discovery.read_local_config(local_dir):
            report = discovery.inspect_local(local_dir, shape)
        elif repo_id:
            progress.text(f"Checking {repo_id}", force=True)
            report = discovery.inspect_repo(repo_id, shape=shape)
            if not report.details:
                return
        else:
            return

    if not report.usable:
        raise RuntimeError("This base model cannot run the prompt-rewriter LoRA.\n" + report.summary())
    log.info(
        "[minimax_h3_rewriter._verify_base_model] %s is usable (%s)",
        report.source, report.quant_method,
    )


def _fetch(repo_id: str, dest_dir: str, allow, skip_suffixes, progress: NodeProgress) -> None:
    reporter = TransferReporter(progress, 1, f"Downloading {repo_id}")
    download.sync_repo(
        repo_id,
        dest_dir,
        allow=allow,
        skip_suffixes=skip_suffixes,
        on_progress=reporter,
        on_status=lambda message: progress.text(message, force=True),
        on_total=reporter.set_total,
    )


def _ensure_present(value: str, spec: dict, auto_download: bool, progress: NodeProgress) -> str:
    """Return a local directory holding the model, downloading it when allowed."""
    repo_id, local_dir = resolve_source(value, spec["default_repo"])
    if spec["complete"](local_dir):
        return local_dir

    if not repo_id:
        raise RuntimeError(
            f"{spec['label']} was not found in '{local_dir}'. Point it at a Hugging Face "
            f"repository id (for example '{spec['default_repo']}') or a complete local folder."
        )
    if not auto_download:
        raise RuntimeError(
            f"{spec['label']} is missing from '{local_dir}' and auto_download is off. "
            f"Enable it, or fetch '{repo_id}' manually into that folder."
        )

    _fetch(repo_id, local_dir, spec["allow"], spec["skip_suffixes"], progress)
    if not spec["complete"](local_dir):
        raise RuntimeError(f"{spec['label']} is still incomplete after downloading into '{local_dir}'.")
    return local_dir


def _ensure_file(repo_id: str, filename: str, label: str, auto_download: bool, progress: NodeProgress) -> str:
    """Return a local path to one file of a repository, fetching it when allowed."""
    if not filename:
        raise RuntimeError(f"{label}: no file name given for repository '{repo_id}'.")

    on_disk = catalog_file(repo_id, filename)
    if on_disk:
        if os.path.isfile(on_disk):
            return on_disk
        raise RuntimeError(
            f"{label}: '{on_disk}' does not exist. The entry points at a folder on this "
            f"machine, so nothing is downloaded — check the path and the file name."
        )
    if os.path.isabs(repo_id):
        raise RuntimeError(
            f"{label}: '{repo_id}' is not a folder on this machine. Give an existing folder "
            f"with '{filename}' inside it, or a Hugging Face repository id."
        )

    destination = os.path.join(models_root(), filename)
    if os.path.isfile(destination) and os.path.getsize(destination) > 0:
        return destination
    if not auto_download:
        raise RuntimeError(
            f"{label} is missing from '{destination}' and auto_download is off. "
            f"Enable it, or fetch '{filename}' from '{repo_id}' into that folder."
        )

    _fetch(repo_id, models_root(), (filename,), (), progress)
    if not os.path.isfile(destination):
        raise RuntimeError(f"{label}: '{filename}' was not present after downloading from '{repo_id}'.")
    return destination


def _ensure_pair(
    repo_id: str, file: str, mmproj: str, label: str, auto_download: bool, progress: NodeProgress
) -> tuple[str, str]:
    """Return local paths to a model and its projector, fetching both if allowed.

    They go into a folder of their own rather than into the flat models/LLM, so
    the pair stays obvious to the local scan: one projector beside one model
    needs no name matching at all.
    """
    on_disk = tuple(catalog_file(repo_id, name) for name in (file, mmproj))
    if all(on_disk):
        missing = [path for path in on_disk if not os.path.isfile(path)]
        if missing:
            raise RuntimeError(
                f"{label}: {', '.join(repr(path) for path in missing)} does not exist. The entry "
                f"points at a folder on this machine, so nothing is downloaded — check the path "
                f"and the file names."
            )
        return on_disk[0], on_disk[1]

    directory = os.path.join(models_root(), repo_id.rstrip("/").split("/")[-1])
    targets = tuple(os.path.join(directory, name) for name in (file, mmproj))

    def complete() -> bool:
        return all(os.path.isfile(path) and os.path.getsize(path) > 0 for path in targets)

    if complete():
        return targets
    if os.path.isabs(repo_id):
        raise RuntimeError(
            f"{label}: '{repo_id}' is not a folder on this machine. Give an existing folder "
            f"holding both '{file}' and '{mmproj}', or a Hugging Face repository id."
        )
    if not auto_download:
        raise RuntimeError(
            f"{label} is missing from '{directory}' and auto_download is off. Enable it, or "
            f"fetch '{file}' and '{mmproj}' from '{repo_id}' into that folder."
        )

    _fetch(repo_id, directory, (file, mmproj), (), progress)
    if not complete():
        raise RuntimeError(
            f"{label}: '{file}' and '{mmproj}' were not both present after downloading from "
            f"'{repo_id}'."
        )
    return targets


_EXTENDED_LOCAL = re.compile(r"^\\\\[?.]\\[A-Za-z]:")


def _refuse_remote(value: str) -> None:
    """Refuse a network location that arrived through a widget.

    Paths in ``models.json`` are the user's own and may point wherever they
    like, a NAS included; that file never leaves the machine unless somebody
    sends it. This string is the other kind. It rides inside the workflow JSON,
    so a graph downloaded from anywhere gets to choose it, and merely *looking*
    at a UNC path is an authentication attempt against whatever host is named --
    it happens on the ``isfile`` call, long before anything decides the adapter
    is unusable. Nothing legitimate is lost: a share holding your models is
    reachable through a drive letter or a mount point like any other folder.
    """
    text = (value or "").strip()
    if os.name == "nt":
        text = text.replace("/", "\\")
    if not text.startswith("\\\\") or _EXTENDED_LOCAL.match(text):
        return
    raise RuntimeError(
        f"'{value}' is a network path, and the options node will not follow one.\n\n"
        f"This field travels inside the workflow, so a graph from elsewhere could point it "
        f"at any host. Map the share to a drive letter and use that, or put the adapter in "
        f"{catalog.user_file()} instead — paths in that file are yours and are not restricted."
    )


_ADAPTER_MAP: dict[str, str] = {}


def _build_adapter_map() -> dict[str, str]:
    """Label -> what ``_locate_adapter`` should be handed.

    The first entry is the exact string this widget has held since it was a text
    field, so a saved workflow keeps its choice when the field becomes a list.
    It means "whichever build the model list names for the base model you
    picked": the PEFT repository for a transformers base, ``adapters.gguf.file``
    for a GGUF one.

    Below it, every published precision of every adapter family, and then
    whatever is already on disk. Picking a quantisation used to mean editing
    ``models.json``, which is a power-user path rather than an interface.
    """
    mapping: dict[str, str] = {ADAPTER_REPO: ADAPTER_REPO}
    for section in catalog.ADAPTER_SECTIONS:
        try:
            for entry in catalog.adapter_entries(FORMAT_GGUF, section):
                mapping[entry.label] = entry.file
        except Exception:
            log.warning(
                "[minimax_h3_rewriter._build_adapter_map] '%s' unreadable", section, exc_info=True
            )
    try:
        for label, path in discovery.scan_local_gguf_adapters():
            mapping[f"{LOCAL_PREFIX}{label}"] = path
    except Exception:
        log.warning("[minimax_h3_rewriter._build_adapter_map] adapter scan failed", exc_info=True)

    _ADAPTER_MAP.clear()
    _ADAPTER_MAP.update(mapping)
    return mapping


def adapter_choices() -> list[str]:
    return list(_build_adapter_map())


def _resolve_adapter_choice(choice: str) -> str:
    """Turn a label back into a file name, a path or a repository id.

    An unknown string is returned unchanged rather than refused. Everything a
    dropdown cannot express -- a path to a LoRA somebody converted themselves --
    used to be typed into this field, and those workflows keep working; the
    resolution below has always accepted a path, and still does.
    """
    found = _ADAPTER_MAP.get(choice)
    if found is None:
        found = _build_adapter_map().get(choice)
    return choice if found is None else found


def _adapter_source(file_name: str) -> catalog.AdapterSpec | None:
    """Which published family a bare adapter file name belongs to.

    There is more than one repository now, so the file name decides where it is
    fetched from. Without this the 8B adapter would be looked for in the 27B's
    repository, and the download would 404 with nothing to say why.
    """
    for section in catalog.ADAPTER_SECTIONS:
        try:
            spec = catalog.adapter(FORMAT_GGUF, section)
        except Exception:
            log.debug("[minimax_h3_rewriter._adapter_source] '%s' unreadable", section)
            continue
        if not spec.configured:
            continue
        for candidate in (spec, *spec.alternatives):
            if candidate.file and candidate.file.casefold() == file_name.casefold():
                return candidate
    return None


def _announce_adapter(fmt: str, value: str, path: str) -> None:
    """Record which LoRA was applied, and say so louder when it is an unusual one.

    A swapped adapter is invisible from the outside: the node still runs, still
    fills every field, and just writes something other than what it was asked
    for. So the resolved path goes into the log on every run, and a request for
    anything but the configured adapter goes in at warning level -- the point is
    not to forbid it, which would break the person converting their own LoRA,
    but to leave a trace when it was not their idea.
    """
    log.info("[minimax_h3_rewriter._resolve_adapter] prompt-rewriter LoRA: %s", path)
    if not value:
        return

    expected = {ADAPTER_REPO.casefold()}
    for section in catalog.ADAPTER_SECTIONS:
        try:
            spec = catalog.adapter(fmt, section)
        except Exception:
            log.debug("[minimax_h3_rewriter._announce_adapter] catalog unreadable", exc_info=True)
            continue
        for candidate in (spec, *spec.alternatives):
            expected.update(
                part.casefold() for part in (candidate.repo, candidate.file) if part
            )

    if value.casefold() not in expected:
        log.warning(
            "[minimax_h3_rewriter._resolve_adapter] the options node asked for adapter '%s', "
            "which is not the configured one (%s); it resolved to %s. If you did not set that "
            "yourself, check the 'adapter' field of the workflow you loaded.",
            value, ", ".join(sorted(expected)), path,
        )


def _resolve_adapter(
    fmt: str,
    setting: str,
    auto_download: bool,
    progress: NodeProgress,
    section: str = catalog.ADAPTERS_27B,
) -> str:
    """Locate the adapter matching the base model's format.

    ``section`` is which family the node belongs to, and it only decides what
    the widget's first entry -- "whatever the model list names" -- resolves to.
    Every other value names a file, and a file is looked up across all the
    families, so picking the 8B adapter by name in a 27B node still finds it,
    and llama.cpp is the one that says the shapes do not match.
    """
    value = _resolve_adapter_choice((setting or "").strip())
    _refuse_remote(value)
    path = _locate_adapter(fmt, value, auto_download, progress, section)
    _announce_adapter(fmt, value, path)
    return path


def _locate_adapter(
    fmt: str,
    value: str,
    auto_download: bool,
    progress: NodeProgress,
    section: str = catalog.ADAPTERS_27B,
) -> str:
    if fmt == FORMAT_TRANSFORMERS:
        wanted = catalog.adapter(FORMAT_TRANSFORMERS, section)
        default = wanted.repo or ADAPTER_REPO
        if not value or (value == ADAPTER_REPO and section != catalog.ADAPTERS_27B):
            value = default
        if value.lower().endswith(".gguf"):
            raise RuntimeError(
                f"'{os.path.basename(value)}' is a GGUF adapter and this base model is a folder "
                f"of safetensors. Set 'adapter' back to its first entry to take whichever build "
                f"matches the model, or pick a GGUF base model to go with it."
            )
        return _ensure_present(
            value, dict(ADAPTER_SPEC, default_repo=default), auto_download, progress
        )

    spec = catalog.adapter(FORMAT_GGUF, section)

    if value.lower().endswith(".gguf"):
        for candidate in (value, os.path.join(models_root(), value)):
            if os.path.isfile(candidate):
                return candidate
        if not os.path.dirname(value) and "/" not in value:
            source = _adapter_source(value)
            repo = source.repo if source is not None else (spec.repo if spec.configured else "")
            if repo:
                return _ensure_file(repo, value, "Prompt-rewriter LoRA", auto_download, progress)
        raise RuntimeError(
            f"Prompt-rewriter LoRA: '{value}' does not exist, and is not a file name that "
            f"could be fetched from '{spec.repo or 'the configured adapter repository'}'."
        )

    if not spec.configured:
        try:
            nearby = [path for _label, path in discovery.scan_local_gguf_adapters()]
        except Exception:
            nearby = []
        hint = f"\nAdapters found on disk:\n  " + "\n  ".join(nearby) if nearby else ""
        raise RuntimeError(
            "No GGUF build of the prompt-rewriter LoRA is configured. Put the path to a "
            "converted '.gguf' adapter in the options node's 'adapter' field, or set "
            f"adapters.gguf.repo in {catalog.user_file()}. Turning 'use_lora' off runs the "
            "plain base model instead." + hint
        )
    return _ensure_file(spec.repo, spec.file, "Prompt-rewriter LoRA", auto_download, progress)


def _gguf_text(settings: dict, **common) -> str:
    """Generate through whichever GGUF runtime this installation has.

    Both rewriters ask the same question -- resident wheel, or the official
    binaries in a subprocess -- and the answer does not depend on which of them
    is asking, so it is answered once.
    """
    runtime = settings.get("gguf_runtime", RUNTIME_AUTO)
    if runtime == RUNTIME_AUTO:
        runtime = RUNTIME_WHEEL if gguf_engine.available() else RUNTIME_BINARY

    if runtime == RUNTIME_WHEEL:
        if not gguf_engine.available():
            raise RuntimeError(
                f"gguf_runtime is set to '{RUNTIME_WHEEL}', but it is not importable "
                f"here. Install it, or set gguf_runtime to '{RUNTIME_AUTO}' or "
                f"'{RUNTIME_BINARY}' to run the official binaries instead.\n\n"
                + gguf_engine.INSTALL_HINT
            )
        return gguf_engine.rewrite(**common)
    return cli_engine.rewrite(
        backend=settings["llama_backend"],
        auto_download=settings["auto_download"],
        **common,
    )


class MiniMaxH3RewriterOptions:
    """Decoding and loading settings for the prompt rewriter."""

    DESCRIPTION = (
        "Optional settings for the MiniMax-H3 Prompt Rewriter. Leave it unconnected and the "
        "node uses the decoding parameters the adapter was published with."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "max_new_tokens": ("INT", {"default": 2048, "min": 64, "max": 16384, "step": 64}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
                "top_p": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.01}),
                "top_k": ("INT", {"default": 20, "min": 0, "max": 200}),
                "repetition_penalty": ("FLOAT", {"default": 1.05, "min": 1.0, "max": 2.0, "step": 0.01}),
                "attn_implementation": (
                    list(ATTN_IMPLEMENTATIONS),
                    {"default": "sdpa", "tooltip": "Non-GGUF models only."},
                ),
            },
            "optional": {
                "adapter": (
                    adapter_choices(),
                    {
                        "default": ADAPTER_REPO,
                        "tooltip": (
                            "Which build of the prompt-rewriter LoRA to use. The first entry "
                            "means whichever one the model list names for the base model you "
                            "picked. Below it are the published precisions - F16 and the "
                            "smaller Q8_0, which rewrites the same and halves the download - "
                            "and any '.gguf' adapter already in your ComfyUI model folders."
                        ),
                    },
                ),
                "use_lora": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Turn off to run the plain Qwen3.6-27B baseline."},
                ),
                "merge_lora": (
                    list(MERGE_LORA),
                    {
                        "default": MERGE_AUTO,
                        "tooltip": (
                            "Fold the adapter into the base weights once at load, rather than "
                            "running it as its own matmuls on every token. Measured on "
                            "Qwen3-VL-8B: 25 tokens a second against 14. 'auto' merges on an "
                            "unquantized base, where it costs 0.07 s and nothing else, and "
                            "leaves the adapter attached on a bitsandbytes 4-bit or 8-bit one, "
                            "where merging means dequantizing and quantizing back. 'on' merges "
                            "there too, for about 4.5 s at load and the same speed afterwards.\n\n"
                            "A merged run is not word-for-word the unmerged one at the same "
                            "seed: folding the adapter in reassociates the arithmetic, so a "
                            "token here and there comes out different. GGUF is unaffected."
                        ),
                    },
                ),
                "auto_download": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Fetch missing weights from Hugging Face. Turn off to fail instead.",
                    },
                ),
                "gpu_layers": (
                    "INT",
                    {
                        "default": -1,
                        "min": -1,
                        "max": 999,
                        "tooltip": (
                            "GGUF only: layers to put on the GPU. -1 is all of them; lower it to "
                            "fit a smaller card, at the cost of speed."
                        ),
                    },
                ),
                "n_ctx": (
                    "INT",
                    {
                        "default": 8192,
                        "min": 2048,
                        "max": 131072,
                        "step": 1024,
                        "tooltip": "GGUF only: context size llama.cpp allocates.",
                    },
                ),
                "gguf_runtime": (
                    list(GGUF_RUNTIMES),
                    {
                        "default": RUNTIME_AUTO,
                        "tooltip": (
                            "GGUF only: what runs the model. 'auto' uses llama-cpp-python when "
                            "it is importable and the official llama.cpp binaries otherwise. "
                            "Force 'llama.cpp' if an installed wheel is broken; force "
                            "'llama-cpp-python' to keep the model resident between runs, which "
                            "the binaries cannot do."
                        ),
                    },
                ),
                "device": (
                    devices.choices(),
                    {"default": devices.AUTO, "tooltip": devices.tooltip()},
                ),
                "llama_backend": (
                    list(llamacpp.BACKENDS),
                    {
                        "default": "auto",
                        "tooltip": (
                            "GGUF only, and only when llama-cpp-python is absent: which official "
                            "llama.cpp build to fetch. 'auto' takes CUDA on Windows with a "
                            "supported NVIDIA card (511 MB, about twice as fast) and Vulkan "
                            "otherwise. Pick 'vulkan' to keep the download at 34 MB.\n\n"
                            "Nothing is fetched at all when llama.cpp is already here: a build "
                            f"on PATH, one named in {llamacpp.BIN_ENV}, or a path written into "
                            f"user/minimax_h3_rewriter/{llamacpp.BIN_FILE} is run as it is -- "
                            "the file being the one that works when the server's environment "
                            "is not yours to set. That is the way to a CUDA llama.cpp on Linux, "
                            "where upstream publishes no CUDA build."
                        ),
                    },
                ),
                "trust_remote_code": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Non-GGUF models only. Some checkpoints ship their own Python and "
                            "Transformers runs it when the model loads. Off means such a model "
                            "is refused rather than executed. Turn it on only for a model you "
                            "picked and trust — not because a downloaded workflow asked for it."
                        ),
                    },
                ),
            },
        }

    RETURN_TYPES = (OPTIONS_TYPE,)
    RETURN_NAMES = ("options",)
    FUNCTION = "build"
    CATEGORY = CATEGORY

    def build(self, **kwargs):
        options = dict(DEFAULT_OPTIONS)
        options.update(kwargs)
        return (options,)


BYPASS_TOOLTIP = (
    "Hand 'prompt' straight to the output and run no model at all: nothing is "
    "downloaded, nothing is loaded, no VRAM is touched. This is what ComfyUI's own "
    "bypass (Ctrl+B) cannot do here - it only forwards a connected link, and every "
    "input this node writes from is a widget, so bypassing the node the usual way "
    "leaves the nodes downstream with nothing. The section outputs come back empty."
)

SYSTEM_PROMPT_TOOLTIP = (
    "Replace the whole assembled guide with your own system prompt, and the guide is not "
    "even fetched. This is what aims these nodes at something other than MiniMax-H3: the "
    "H3 format lives in that text and nowhere else, so a guide written for LTX, Krea or "
    "Wan makes this a writer for those. 'MiniMax-H3 Guide Prompt (any LLM)' hands you the "
    "stock one on its 'system_prompt' output - the shortest way in is to take it, edit it "
    "and connect it back here.\n\n"
    "Left empty, nothing changes. The task message is never replaced: it carries the "
    "prompt, the aspect ratio and the duration, which any guide needs.\n\n"
    "One consequence to expect. This node splits the answer into the H3 sections, so a "
    "guide that replies with a paragraph fills 'rewritten_prompt' and leaves the section "
    "outputs empty. That is worth knowing rather than worth avoiding."
)

BYPASS_CAPTION_TOOLTIP = (
    "Pass 'previous' through unchanged and run no model at all, which drops this "
    "asset from the chain without unwiring it. Numbering stays correct: each node "
    "numbers what it receives, so the assets after this one close the gap. 'caption' "
    "comes back empty."
)


def _bypassed(unique_id, text: str, names: tuple[str, ...]) -> tuple[str, ...]:
    """Hand the prompt back untouched, with one empty string per section output."""
    NodeProgress(unique_id).finish("bypassed")
    return ((text or "").strip(),) + ("",) * len(names)


def rewrite_t2va(
    model: str,
    prompt: str,
    resolution: str,
    duration: int,
    quantization: str,
    greedy: bool,
    seed: int,
    keep_loaded: bool,
    settings: dict,
    progress: NodeProgress,
) -> str:
    """Run the 27B T2VA adapter and return the rewrite, whichever shape the base is.

    A function rather than a method because two nodes run it: the rewriter this
    file has always exposed, and the universal rewriter's 27B tab. The engine
    plumbing -- which format, which adapter, which of the two GGUF runtimes -- is
    the same either way, and the second copy of it would be the one that stopped
    getting fixed.
    """
    choice = _resolve_model_choice(model)

    decoding = {
        "messages": build_messages(prompt, resolution, int(duration)),
        "seed": int(seed),
        "greedy": greedy,
        "max_new_tokens": int(settings["max_new_tokens"]),
        "temperature": float(settings["temperature"]),
        "top_p": float(settings["top_p"]),
        "top_k": int(settings["top_k"]),
        "repetition_penalty": float(settings["repetition_penalty"]),
    }

    if choice.fmt == FORMAT_GGUF:
        if choice.local:
            model_path = choice.reference
        else:
            model_path = _ensure_file(
                choice.reference, choice.file, "Base model", settings["auto_download"], progress
            )
        log.info("[minimax_h3_rewriter.rewrite] base model: %s", model_path)
        adapter_path = None
        if settings["use_lora"]:
            problem = discovery.gguf_problem(model_path)
            if problem:
                raise RuntimeError(
                    "This GGUF cannot run the prompt-rewriter LoRA.\n  - "
                    + problem
                    + "\nTurn 'use_lora' off to run it as a plain model anyway."
                )
            adapter_path = _resolve_adapter(
                FORMAT_GGUF, settings["adapter"], settings["auto_download"], progress
            )
        common = dict(
            model_path=model_path,
            adapter_path=adapter_path,
            gpu_layers=int(settings["gpu_layers"]),
            n_ctx=int(settings["n_ctx"]),
            keep_loaded=keep_loaded,
            device=settings["device"],
            progress=progress,
            **decoding,
        )
        return _gguf_text(settings, **common)

    _verify_base_model(choice.reference, progress)
    base_dir = _ensure_present(choice.reference, BASE_SPEC, settings["auto_download"], progress)
    log.info("[minimax_h3_rewriter.rewrite] base model: %s", base_dir)
    adapter_dir = None
    if settings["use_lora"]:
        adapter_dir = _resolve_adapter(
            FORMAT_TRANSFORMERS, settings["adapter"], settings["auto_download"], progress
        )
    return engine.rewrite(
        base_dir=base_dir,
        adapter_dir=adapter_dir,
        quantization=quantization,
        attn_implementation=settings["attn_implementation"],
        keep_loaded=keep_loaded,
        device=settings["device"],
        progress=progress,
        trust_remote_code=bool(settings.get("trust_remote_code", False)),
        merge_lora=settings.get("merge_lora", MERGE_AUTO),
        **decoding,
    )


class MiniMaxH3PromptRewriter:
    """Rewrite a short prompt into a structured MiniMax-H3 T2VA description."""

    DESCRIPTION = (
        "Runs the LightX2V MiniMax-H3 T2VA Prompt Rewriter LoRA on Qwen3.6-27B and returns a "
        "structured audio-video description. Weights are downloaded on first use, with progress "
        "shown on the node. GGUF models run through llama-cpp-python when it is installed, and "
        "otherwise through the official llama.cpp binaries, fetched on first use."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "The short prompt to expand into an H3 audio-video description.",
                    },
                ),
                "model": (
                    model_choices(),
                    {
                        "tooltip": (
                            "Base model. Entries prefixed 'on disk:' are already downloaded; the "
                            "rest are fetched on first use. GGUF entries need no extra install: "
                            "without llama-cpp-python the node fetches the official llama.cpp "
                            "binaries. Use the button to edit the list."
                        ),
                    },
                ),
                "resolution": (
                    list(RESOLUTIONS),
                    {"default": "16:9", "socketless": True, "tooltip": aspect.PICKER_TOOLTIP},
                ),
                "duration": (
                    "INT",
                    {
                        "default": 10,
                        "min": DURATION_MIN,
                        "max": DURATION_MAX,
                        "step": 1,
                        "tooltip": "Target clip length in seconds; drives shot count and pacing.",
                    },
                ),
                "quantization": (
                    list(QUANTIZATIONS),
                    {
                        "default": "nf4",
                        "tooltip": (
                            "How to load an unquantized checkpoint: nf4 needs about 16 GB of VRAM, "
                            "int8 about 28 GB, bfloat16 about 54 GB. Ignored for GGUF models and "
                            "for checkpoints that are already quantized."
                        ),
                    },
                ),
                "greedy": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Deterministic decoding. Turn off to sample; see the options node.",
                    },
                ),
                "seed": (
                    "INT",
                    {
                        "default": 42,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                    },
                ),
                "keep_model_loaded": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Keep the 27B model in VRAM after the rewrite. Leave off when the same "
                            "GPU has to run MiniMax-H3 video generation afterwards — or give the "
                            "rewriter a card of its own with 'device' in the options node, and then "
                            "there is nothing to compete with."
                        ),
                    },
                ),
            },
            "optional": {
                "aspect_ratio": (
                    "STRING,COMBO",
                    {"default": "", "widgetType": "STRING", "tooltip": aspect.TOOLTIP},
                ),
                "options": (OPTIONS_TYPE,),
                "bypass": ("BOOLEAN", {"default": False, "tooltip": BYPASS_TOOLTIP}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("rewritten_prompt",) + OUTPUT_FIELDS
    FUNCTION = "rewrite"
    CATEGORY = CATEGORY

    def rewrite(
        self,
        prompt,
        model,
        resolution,
        duration,
        quantization,
        greedy,
        seed,
        keep_model_loaded,
        aspect_ratio=None,
        options=None,
        bypass=False,
        unique_id=None,
    ):
        if bypass:
            return _bypassed(unique_id, prompt, OUTPUT_FIELDS)

        if not (prompt or "").strip():
            raise ValueError("prompt must not be empty")

        resolution = aspect.resolve(aspect_ratio, resolution)

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        progress = NodeProgress(unique_id)
        text = rewrite_t2va(
            model, prompt, resolution, duration, quantization,
            greedy, seed, keep_model_loaded, settings, progress,
        )

        fields = split_fields(text)
        progress.text(text[-2000:] if text else "(empty rewrite)", force=True)
        return (text,) + tuple(fields[name] for name in OUTPUT_FIELDS)


REFERENCE_PLACEHOLDER = (
    "Describe what the reference frames show, one per line:\n"
    "Picture 1: ...\n"
)

REFERENCE_ASSETS_PLACEHOLDER = (
    "List every reference asset, one per line:\n"
    "Picture 1: young woman, long dark hair, blue cardigan, seated by a window\n"
    "Video 1: source clip being edited — handheld walk down a night street\n"
    "Audio 1: voice-timbre reference for the woman\n"
)


def _guided_text(
    mode: str,
    model: str,
    prompt: str,
    resolution: str,
    duration: float,
    references: str,
    greedy: bool,
    seed: int,
    keep_model_loaded: bool,
    settings: dict,
    progress: NodeProgress,
    system_prompt: str = "",
) -> str:
    """Run one guided rewrite and return the model's raw answer.

    ``system_prompt`` stands in for the whole assembled guide, and when it is
    given the guide is never fetched: a writer aimed at another model has no use
    for MiniMax's document, and an offline machine should not be made to
    download one to ignore it.
    """
    choice = _resolve_writer_choice(model)
    if choice.local:
        model_path = choice.reference
    else:
        model_path = _ensure_file(
            choice.reference, choice.file, "Writer model", settings["auto_download"], progress
        )

    if discovery.gguf_header(model_path)["kind"] == "adapter":
        raise RuntimeError(
            f"'{os.path.basename(model_path)}' is a LoRA adapter, not a model that can be run "
            f"on its own. Pick a base model from the list."
        )

    given = (system_prompt or "").strip()
    guide = "" if given else guides.text(
        guide_prompt.GUIDE_FOR_MODE[mode], settings["auto_download"], progress
    )
    messages = guide_prompt.build_messages(
        guide, mode, prompt, resolution, duration, references, system=given
    )

    max_new_tokens = int(settings["max_new_tokens"])
    n_ctx = int(settings["n_ctx"])
    needed = guide_prompt.context_needed(messages, max_new_tokens)
    if needed > n_ctx:
        log.info(
            "[minimax_h3_rewriter._guided_text] raising n_ctx from %d to %d for the %s guide",
            n_ctx, needed, mode,
        )
        progress.text(
            f"{mode}: the guide needs a {needed}-token context, raising n_ctx from {n_ctx}",
            force=True,
        )
        n_ctx = needed

    common = dict(
        model_path=model_path,
        adapter_path=None,
        gpu_layers=int(settings["gpu_layers"]),
        n_ctx=n_ctx,
        keep_loaded=keep_model_loaded,
        device=settings["device"],
        progress=progress,
        messages=messages,
        seed=int(seed),
        greedy=greedy,
        max_new_tokens=max_new_tokens,
        temperature=float(settings["temperature"]),
        top_p=float(settings["top_p"]),
        top_k=int(settings["top_k"]),
        repetition_penalty=float(settings["repetition_penalty"]),
    )

    runtime = settings.get("gguf_runtime", RUNTIME_AUTO)
    if runtime == RUNTIME_AUTO:
        runtime = RUNTIME_WHEEL if gguf_engine.available() else RUNTIME_BINARY

    if runtime == RUNTIME_WHEEL:
        if not gguf_engine.available():
            raise RuntimeError(
                f"gguf_runtime is set to '{RUNTIME_WHEEL}', but it is not importable here. "
                f"Install it, or set gguf_runtime to '{RUNTIME_AUTO}' or '{RUNTIME_BINARY}' to "
                f"run the official binaries instead.\n\n" + gguf_engine.INSTALL_HINT
            )
        return gguf_engine.rewrite(**common)
    return cli_engine.rewrite(
        backend=settings["llama_backend"],
        auto_download=settings["auto_download"],
        **common,
    )


def _report(progress: NodeProgress, text: str, sections: dict, names: tuple[str, ...]) -> None:
    """Leave the answer on the node, and say so when it is not the right shape."""
    absent = missing(sections, names)
    note = ""
    if absent:
        note = (
            f"⚠ {len(absent)} field(s) not found in the answer: {', '.join(absent)}\n"
            f"Lower the temperature, or try a larger writer model.\n\n"
        )
        log.warning("[minimax_h3_rewriter._report] fields missing from the rewrite: %s", absent)
    progress.text(note + (text[-2000:] if text else "(empty rewrite)"), force=True)


class MiniMaxH3GuidedWriter:
    """Write a T2VA/I2VA/FL2VA/L2VA prompt with any model, guided rather than trained."""

    DESCRIPTION = (
        "Writes a MiniMax-H3 audio-video description from MiniMax's own writing guide, which "
        "goes into the system prompt. No LoRA, so any instruction-following GGUF can run it — "
        "a 4B on an 8 GB card instead of Qwen3.6-27B. The guide is fetched from "
        "MiniMaxAI/MiniMax-H3 on first use. Outputs match the LoRA rewriter node, so the two "
        "are interchangeable in a workflow."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "The short prompt to expand into an H3 audio-video description.",
                    },
                ),
                "model": (
                    writer_choices(),
                    {
                        "tooltip": (
                            "Any GGUF language model. Entries prefixed 'on disk:' are already "
                            "in your ComfyUI model folders; the rest are fetched on first use. "
                            "Nothing has to be installed: without llama-cpp-python the node "
                            "runs the official llama.cpp binaries."
                        ),
                    },
                ),
                "task": (
                    list(guide_prompt.BASE_MODES),
                    {
                        "default": "T2VA",
                        "tooltip": (
                            "T2VA: text only. I2VA: the reference image is the first frame. "
                            "FL2VA: first and last frame. L2VA: the reference image is the last "
                            "frame. Everything but T2VA also writes the alignment instruction "
                            "line, with the duration already filled in."
                        ),
                    },
                ),
                "resolution": (
                    list(RESOLUTIONS),
                    {"default": "16:9", "socketless": True, "tooltip": aspect.PICKER_TOOLTIP},
                ),
                "duration": (
                    "INT",
                    {
                        "default": 10,
                        "min": DURATION_MIN,
                        "max": DURATION_MAX,
                        "step": 1,
                        "tooltip": "Target clip length in seconds; drives shot count and pacing.",
                    },
                ),
                "greedy": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "Deterministic decoding. Worth keeping on for small models, which "
                            "drift out of the format when they sample."
                        ),
                    },
                ),
                "seed": (
                    "INT",
                    {
                        "default": 42,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                    },
                ),
                "keep_model_loaded": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Keep the writer in VRAM after the rewrite. Leave off when the same "
                            "GPU has to run MiniMax-H3 video generation afterwards — or send it to "
                            "a second card with 'device' in the options node."
                        ),
                    },
                ),
            },
            "optional": {
                "aspect_ratio": (
                    "STRING,COMBO",
                    {"default": "", "widgetType": "STRING", "tooltip": aspect.TOOLTIP},
                ),
                "reference_material": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": (
                            "This node reads text, not pixels. For I2VA, FL2VA and L2VA, "
                            "describe what the reference frames show — by hand, or from a "
                            "captioner node — so the rewrite is anchored to them.\n\n"
                            + REFERENCE_PLACEHOLDER
                        ),
                    },
                ),
                "options": (OPTIONS_TYPE,),
                "bypass": ("BOOLEAN", {"default": False, "tooltip": BYPASS_TOOLTIP}),
                "system_prompt": (
                    "STRING",
                    {"multiline": True, "default": "", "tooltip": SYSTEM_PROMPT_TOOLTIP},
                ),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING",) * (1 + len(OUTPUT_FIELDS))
    RETURN_NAMES = ("rewritten_prompt",) + OUTPUT_FIELDS
    FUNCTION = "write"
    CATEGORY = CATEGORY

    def write(
        self,
        prompt,
        model,
        task,
        resolution,
        duration,
        greedy,
        seed,
        keep_model_loaded,
        reference_material="",
        system_prompt="",
        aspect_ratio=None,
        options=None,
        bypass=False,
        unique_id=None,
    ):
        if bypass:
            return _bypassed(unique_id, prompt, OUTPUT_FIELDS)

        resolution = aspect.resolve(aspect_ratio, resolution)
        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)
        progress = NodeProgress(unique_id)

        text = _guided_text(
            task, model, prompt, resolution, duration, reference_material,
            greedy, seed, keep_model_loaded, settings, progress, system_prompt,
        )
        _head, sections = split_sections(text, OUTPUT_FIELDS)
        _report(progress, text, sections, OUTPUT_FIELDS)
        return (text,) + tuple(sections[name] for name in OUTPUT_FIELDS)


class MiniMaxH3GuidedWriterRef:
    """Write a full-reference (Ref2VA) prompt with any model."""

    DESCRIPTION = (
        "Writes a MiniMax-H3 full-reference (Ref2VA) description — six sections, with "
        "<Subject>/<Picture>/<Video>/<Audio> labels and a retention analysis — from MiniMax's "
        "own full-reference guide, which goes into the system prompt. No LoRA, so any "
        "instruction-following GGUF can run it. The guide is fetched from MiniMaxAI/MiniMax-H3 "
        "on first use."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "What the target video should show, and how it uses the references.",
                    },
                ),
                "reference_assets": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": (
                            "Required. One asset per line — the node reads text, not pixels, so "
                            "this is all the model knows about them. Label them Picture N, "
                            "Video N or Audio N and say what each one is for.\n\n"
                            + REFERENCE_ASSETS_PLACEHOLDER
                        ),
                    },
                ),
                "model": (
                    writer_choices(),
                    {
                        "tooltip": (
                            "Any GGUF language model. The full-reference guide is the longer of "
                            "the two, so a 4B will hold the format but a 9B keeps the labels "
                            "consistent across all six sections."
                        ),
                    },
                ),
                "resolution": (
                    list(RESOLUTIONS),
                    {"default": "16:9", "socketless": True, "tooltip": aspect.PICKER_TOOLTIP},
                ),
                "duration": (
                    "INT",
                    {
                        "default": 10,
                        "min": DURATION_MIN,
                        "max": DURATION_MAX,
                        "step": 1,
                        "tooltip": "Target clip length in seconds; drives shot count and pacing.",
                    },
                ),
                "greedy": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Deterministic decoding. Keep it on unless the result is too plain.",
                    },
                ),
                "seed": (
                    "INT",
                    {
                        "default": 42,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                    },
                ),
                "keep_model_loaded": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Keep the writer in VRAM after the rewrite.",
                    },
                ),
            },
            "optional": {
                "aspect_ratio": (
                    "STRING,COMBO",
                    {"default": "", "widgetType": "STRING", "tooltip": aspect.TOOLTIP},
                ),
                "options": (OPTIONS_TYPE,),
                "bypass": ("BOOLEAN", {"default": False, "tooltip": BYPASS_TOOLTIP}),
                "system_prompt": (
                    "STRING",
                    {"multiline": True, "default": "", "tooltip": SYSTEM_PROMPT_TOOLTIP},
                ),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING",) * (1 + len(REF_OUTPUT_FIELDS))
    RETURN_NAMES = ("rewritten_prompt",) + REF_OUTPUT_FIELDS
    FUNCTION = "write"
    CATEGORY = CATEGORY

    def write(
        self,
        prompt,
        reference_assets,
        model,
        resolution,
        duration,
        greedy,
        seed,
        keep_model_loaded,
        system_prompt="",
        aspect_ratio=None,
        options=None,
        bypass=False,
        unique_id=None,
    ):
        if bypass:
            return _bypassed(unique_id, prompt, REF_OUTPUT_FIELDS)

        resolution = aspect.resolve(aspect_ratio, resolution)
        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)
        progress = NodeProgress(unique_id)

        text = _guided_text(
            guide_prompt.REF_MODE, model, prompt, resolution, duration, reference_assets,
            greedy, seed, keep_model_loaded, settings, progress, system_prompt,
        )
        _head, sections = split_sections(text, REF_OUTPUT_FIELDS, fallback="detailed_description")
        _report(progress, text, sections, REF_OUTPUT_FIELDS)
        return (text,) + tuple(sections[name] for name in REF_OUTPUT_FIELDS)


GUIDE_PROMPT_FORMATS = ("plain", "chatml")


def single_prompt(system: str, user: str, form: str = "plain") -> str:
    """The two messages as one string, for an LLM node that takes only one.

    ComfyUI's own ``TextGenerate`` has a single ``prompt`` input and wraps
    whatever it receives in the model's chat template, which puts the guide
    inside the user turn. ``plain`` accepts that and joins the two, which is what
    a ``StringConcatenate`` in the middle of the graph would have done.

    ``chatml`` writes the turns out instead: Qwen's tokenizer skips its own
    template as soon as the text starts with ``<|im_start|>``, so the guide lands
    in a real system turn. That branch also skips Qwen's thinking suppression, so
    the empty think block is written here -- without it the model spends hundreds
    of tokens reasoning before the rewrite, which is the same reason
    ``chat_template`` renders with ``enable_thinking=False``.
    """
    system, user = (system or "").strip(), (user or "").strip()
    if form == "chatml":
        return (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )
    return f"{system}\n\n{user}".strip()


class MiniMaxH3GuidePrompt:
    """The guide-based system and user messages, for any other LLM node to run."""

    DESCRIPTION = (
        "Builds the system and user prompt from MiniMax's writing guide and returns them as "
        "strings, without running anything. Wire them into whatever LLM node you already use — "
        "local, API, or remote — when you would rather not run the model here. Costs no VRAM "
        "and no time. The third output is both of them in one string, for a node that takes a "
        "single prompt — ComfyUI's own 'Generate Text' among them."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "task": (
                    list(guide_prompt.ALL_MODES),
                    {
                        "default": "T2VA",
                        "tooltip": "Ref2VA uses the full-reference guide and its six output sections.",
                    },
                ),
                "resolution": (
                    list(RESOLUTIONS),
                    {"default": "16:9", "socketless": True, "tooltip": aspect.PICKER_TOOLTIP},
                ),
                "duration": (
                    "INT",
                    {"default": 10, "min": DURATION_MIN, "max": DURATION_MAX, "step": 1},
                ),
            },
            "optional": {
                "aspect_ratio": (
                    "STRING,COMBO",
                    {"default": "", "widgetType": "STRING", "tooltip": aspect.TOOLTIP},
                ),
                "reference_material": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": (
                            "What the reference frames or assets show. Required for Ref2VA.\n\n"
                            + REFERENCE_ASSETS_PLACEHOLDER
                        ),
                    },
                ),
                "auto_download": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "Fetch the guide from MiniMaxAI/MiniMax-H3 if it is not already in "
                            "the ComfyUI user directory."
                        ),
                    },
                ),
                "format": (
                    list(GUIDE_PROMPT_FORMATS),
                    {
                        "default": "plain",
                        "tooltip": (
                            "How the third output joins the two. 'plain' puts a blank line "
                            "between them and lets the LLM node apply the model's own chat "
                            "template, which lands the guide in the user turn. 'chatml' writes "
                            "the turns out instead, so a Qwen text encoder takes the guide as a "
                            "real system message and skips its thinking block; on a model that "
                            "is not ChatML, leave this on 'plain'."
                        ),
                    },
                ),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("system_prompt", "user_prompt", "prompt")
    FUNCTION = "build"
    CATEGORY = CATEGORY

    def build(
        self,
        prompt,
        task,
        resolution,
        duration,
        reference_material="",
        aspect_ratio=None,
        auto_download=True,
        format="plain",
        unique_id=None,
    ):
        resolution = aspect.resolve(aspect_ratio, resolution)
        progress = NodeProgress(unique_id)
        guide = guides.text(guide_prompt.GUIDE_FOR_MODE[task], auto_download, progress)
        messages = guide_prompt.build_messages(
            guide, task, prompt, resolution, int(duration), reference_material
        )
        system, user = messages[0]["content"], messages[1]["content"]
        joined = single_prompt(system, user, format)
        progress.finish(
            f"{task} · system {len(system)} chars · user {len(user)} chars · {format}\n"
            f"about {guide_prompt.context_needed(messages, 0)} tokens of context before the answer"
        )
        return (system, user, joined)


CAPTION_ROLES = ("Subject", "Picture", "Video", "Audio")

CAPTION_INSTRUCTIONS = {
    "Subject": (
        "Describe the main subject so it can be recognised again in another shot: who or what "
        "it is, age and build if it is a person, hair, clothing and their colours, and any "
        "distinguishing object it carries. State only what is visible. No interpretation, no "
        "mood, no story."
    ),
    "Picture": (
        "Describe this frame as a shot: overall visual style, shot size and camera angle, where "
        "the subject sits in the frame, the environment and key props, the lighting and colour. "
        "State only what is visible."
    ),
    "Video": (
        "Describe what happens in this clip: the subjects and how they look, the actions in "
        "order, how the camera moves, and any cuts and their pacing. State only what is visible."
    ),
    "Audio": (
        "Describe the SOUND, not the words. Cover: the speaker's voice timbre, apparent age and "
        "gender, delivery and speaking rate, any accent; the music, its instrumentation, tempo "
        "and dynamics; and the background ambience and physical sound effects. Do not transcribe "
        "or summarise what is said."
    ),
}

CAPTION_LENGTHS = {
    "brief": "Answer in one sentence.",
    "standard": "Answer in two or three sentences.",
    "detailed": "Answer in four to six sentences, covering every listed aspect.",
}

INSTRUCTIONS_TOOLTIP = (
    "What each reference is asked, as JSON, written by the narrow band under each square "
    "on the strip. Two shapes are accepted per slot: a plain string, which is *added* to "
    "the role's own question, or {\"text\": ..., \"add\": false}, which asks it "
    "*instead*. It is kept as a widget so the text travels with the workflow and through "
    "the API; the interface hides it. A slot missing from the map is asked the usual "
    "question."
)


@dataclass(frozen=True)
class Question:
    """One reference's own wording, and whether it replaces the role's or joins it."""

    text: str
    add: bool = True


def slot_instructions(raw: str) -> dict[str, Question]:
    """Per-reference questions out of the strip's JSON widget.

    A bare string means "add this to the role's question", which is the
    forgiving reading and the right default for anything hand-written: an
    instruction like "do not mention the window" is a rule about an answer, and
    on its own it is not a question at all -- a model handed only that answers
    the rule rather than describing anything.

    Anything unreadable means every asset is asked its role's own question,
    which is the same rule the switch map follows: a corrupted widget should
    cost the wording, never the run.
    """
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    found: dict[str, Question] = {}
    for name, value in parsed.items():
        if not isinstance(name, str):
            continue
        if isinstance(value, str):
            text, add = value, True
        elif isinstance(value, dict):
            text = value.get("text")
            add = bool(value.get("add", True))
            if not isinstance(text, str):
                continue
        else:
            continue
        if text.strip():
            found[name] = Question(text.strip(), add)
    return found


def caption_question(role: str, length: str, question: Question | None = None) -> str:
    """What one asset is actually asked, in the one place all three nodes share.

    Both halves of this were learned the same way, from two instructions that
    wanted opposite things. "Always answer 'blah blah blah'" is a whole question
    and has to replace the role's, or the model reads two contradictory
    instructions and settles it by doing the more concrete one. "Never mention
    the window" is a rule and has to join the role's question, or there is
    nothing left asking for a description and the model simply answers "No".

    So the band says which it is, and the length line follows the question: a
    replacement owns the shape of its own answer, an addition leaves the preset
    where it was.

    The no-reasoning line survives either way. It is about which channel the
    model answers on rather than about what it says, and losing it is how a
    thinking model writes its deliberation into the reference block.
    """
    stock = CAPTION_INSTRUCTIONS[role]
    length_line = CAPTION_LENGTHS[length]
    if question is None or not question.text:
        return f"{stock} {length_line} {NO_REASONING}"
    if question.add:
        return f"{stock} {question.text} {length_line} {NO_REASONING}"
    return f"{question.text} {NO_REASONING}"


_ASSET_LINE = re.compile(r"^[ \t]*(Subject|Picture|Video|Audio)[ \t]+(\d+)[ \t]*:", re.IGNORECASE | re.MULTILINE)


def next_index(previous: str, role: str) -> int:
    """The next free number for this role, counted within its own category.

    Which is the guide's own rule: "``<Video N>`` and ``<Audio N>`` are numbered
    independently. Each index indicates only the label's order within its own
    category." So a chain of four assets can be Picture 1, Picture 2, Video 1,
    Audio 1 -- not 1 through 4.
    """
    highest = 0
    for match in _ASSET_LINE.finditer(previous or ""):
        if match.group(1).lower() == role.lower():
            highest = max(highest, int(match.group(2)))
    return highest + 1


class MiniMaxH3ReferenceCaption:
    """Describe one reference asset, and add it to a Ref2VA reference block."""

    DESCRIPTION = (
        "Describes an image, an audio clip or a video with a small multimodal model, and turns "
        "the result into one labelled line of 'reference_assets' for the Ref2VA writer. Chain "
        "several by wiring reference_assets into the next node's 'previous'; each label is "
        "numbered within its own category, as the guide requires. Runs through the same "
        "llama.cpp binaries as the rest of the pack, so there is nothing to install."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "role": (
                    list(CAPTION_ROLES),
                    {
                        "default": "Picture",
                        "tooltip": (
                            "Subject: a person, object or place to reuse. Picture: this frame is "
                            "a concrete first/last/key frame. Video: a clip whose structure or "
                            "content is reused. Audio: a sound or voice reference. The role "
                            "picks both the label and what the model is asked for."
                        ),
                    },
                ),
                "model": (
                    captioner_choices(),
                    {
                        "tooltip": (
                            "A multimodal GGUF and its projector. Entries prefixed 'on disk:' "
                            "are pairs already in your ComfyUI model folders. Not every "
                            "multimodal GGUF works: llama.cpp's mtmd has to understand the "
                            "projector, and some current models abort while loading it. Ignored "
                            "entirely while 'clip' is connected."
                        ),
                    },
                ),
                "length": (
                    list(CAPTION_LENGTHS),
                    {"default": "standard", "tooltip": "How much the model is asked to write."},
                ),
                "seed": (
                    "INT",
                    {"default": 42, "min": 0, "max": 0xFFFFFFFF, "control_after_generate": True},
                ),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "A frame, or a batch of frames from a clip."}),
                "audio": ("AUDIO",),
                "video": ("VIDEO",),
                "clip": (
                    "CLIP",
                    {
                        "tooltip": (
                            "A multimodal text encoder loaded by 'CLIPLoader' — Qwen3-VL or "
                            "Gemma-4. Connect it and this asset is described by that model "
                            "instead of by 'model', which stays loaded between runs rather than "
                            "being read off disk each time. Only Gemma-4 E2B, E4B and 12B can "
                            "hear audio. Leave it unconnected and nothing changes."
                        ),
                    },
                ),
                "previous": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": "reference_assets from the previous caption node in the chain.",
                    },
                ),
                "description": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": (
                            "Type the description yourself and no model is run at all — the "
                            "fastest way to add an asset you can describe in a few words."
                        ),
                    },
                ),
                "instruction": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Override what the model is asked. Empty uses the role's own question.",
                    },
                ),
                "max_frames": (
                    "INT",
                    {
                        "default": media.DEFAULT_MAX_FRAMES,
                        "min": 1,
                        "max": 64,
                        "tooltip": (
                            "How many frames to take from an IMAGE batch, spread evenly. All of "
                            "them would overflow the context and the wall clock."
                        ),
                    },
                ),
                "context_size": (
                    "INT",
                    {
                        "default": mtmd_engine.CONTEXT_FROM_MODEL,
                        "min": 0,
                        "max": 131072,
                        "step": 1024,
                        "tooltip": (
                            "0 sizes the context from the references and the card: llama.cpp "
                            "reserves the whole KV cache up front, and a model trained for 256k "
                            "would ask for tens of GB of it before looking at anything. Set a "
                            "number to say it yourself; too small a value fails the run outright "
                            "rather than truncating."
                        ),
                    },
                ),
                "options": (OPTIONS_TYPE,),
                "bypass": ("BOOLEAN", {"default": False, "tooltip": BYPASS_CAPTION_TOOLTIP}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("reference_assets", "caption")
    FUNCTION = "describe"
    CATEGORY = CATEGORY

    def describe(
        self,
        role,
        model,
        length,
        seed,
        image=None,
        audio=None,
        video=None,
        clip=None,
        previous="",
        description="",
        instruction="",
        max_frames=media.DEFAULT_MAX_FRAMES,
        context_size=mtmd_engine.CONTEXT_FROM_MODEL,
        options=None,
        bypass=False,
        unique_id=None,
    ):
        if bypass:
            NodeProgress(unique_id).finish("bypassed")
            return ((previous or "").strip(), "")

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)
        progress = NodeProgress(unique_id)

        caption = (description or "").strip()
        if not caption:
            if image is None and audio is None and video is None:
                raise ValueError(
                    f"{role}: nothing to describe. Connect an image, an audio clip or a video, "
                    f"or type the description into 'description'."
                )
            typed = (instruction or "").strip()
            asked = caption_question(role, length, Question(typed, add=False) if typed else None)

            if clip is not None:
                kinds = set()
                if image is not None:
                    kinds.add("image")
                if video is not None:
                    kinds.add("video")
                if audio is not None:
                    kinds.add("audio")
                clip_caption.check(clip, kinds)
                caption = clip_caption.describe(
                    clip,
                    instruction=asked,
                    image=image,
                    audio=audio,
                    video=video,
                    max_frames=int(max_frames),
                    seed=int(seed),
                    settings=settings,
                    progress=progress,
                )
            else:
                choice = _resolve_captioner_choice(model)
                if choice.local:
                    model_path, mmproj_path = choice.reference, choice.mmproj
                else:
                    model_path, mmproj_path = _ensure_pair(
                        choice.reference, choice.file, choice.mmproj, "Captioner",
                        settings["auto_download"], progress,
                    )

                header = discovery.gguf_header(mmproj_path)
                if audio is not None and not header["audio"]:
                    raise RuntimeError(
                        f"'{os.path.basename(mmproj_path)}' was built without an audio encoder, so "
                        f"this model cannot hear the clip. Pick a captioner whose label lists "
                        f"'audio'."
                    )
                if (image is not None or video is not None) and not header["vision"]:
                    raise RuntimeError(
                        f"'{os.path.basename(mmproj_path)}' was built without a vision encoder, so "
                        f"this model cannot see. Pick a captioner whose label lists 'vision'."
                    )

                caption = mtmd_engine.describe(
                    model_path=model_path,
                    mmproj_path=mmproj_path,
                    instruction=asked,
                    image=image,
                    audio=audio,
                    video=video,
                    max_frames=int(max_frames),
                    gpu_layers=int(settings["gpu_layers"]),
                    n_ctx=int(context_size),
                    seed=int(seed),
                    greedy=True,
                    max_new_tokens=min(int(settings["max_new_tokens"]), 1024),
                    temperature=float(settings["temperature"]),
                    top_p=float(settings["top_p"]),
                    top_k=int(settings["top_k"]),
                    device=settings["device"],
                    backend=settings["llama_backend"],
                    auto_download=settings["auto_download"],
                    progress=progress,
                )

        caption = " ".join(caption.split())
        line = f"{role} {next_index(previous, role)}: {caption}"
        block = f"{(previous or '').rstrip()}\n{line}".strip()
        progress.finish(line)
        return (block, caption)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3PromptRewriter": MiniMaxH3PromptRewriter,
    "MiniMaxH3RewriterOptions": MiniMaxH3RewriterOptions,
    "MiniMaxH3GuidedWriter": MiniMaxH3GuidedWriter,
    "MiniMaxH3GuidedWriterRef": MiniMaxH3GuidedWriterRef,
    "MiniMaxH3GuidePrompt": MiniMaxH3GuidePrompt,
    "MiniMaxH3ReferenceCaption": MiniMaxH3ReferenceCaption,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3PromptRewriter": "MiniMax-H3 Prompt Rewriter",
    "MiniMaxH3RewriterOptions": "MiniMax-H3 Rewriter Options",
    "MiniMaxH3GuidedWriter": "MiniMax-H3 Prompt Writer (T2VA/I2VA/FL2VA/L2VA)",
    "MiniMaxH3GuidedWriterRef": "MiniMax-H3 Prompt Writer (Ref2VA)",
    "MiniMaxH3GuidePrompt": "MiniMax-H3 Guide Prompt (any LLM)",
    "MiniMaxH3ReferenceCaption": "MiniMax-H3 Reference Caption",
}
