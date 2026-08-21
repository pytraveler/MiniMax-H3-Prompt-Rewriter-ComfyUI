"""Finding a usable Qwen3.6-27B and deciding whether it will actually work.

The adapter is bound to one base model, but that base ships in many builds — the
52 GB bf16 original plus FP8, AWQ, GPTQ and NVFP4 repackings around 19-29 GB.
Every one of them keeps the same architecture fingerprint in ``config.json``, so
a 4 KB fetch answers "is this the right model" before any of the weights move.

What differs is the *runtime*: a quantized checkpoint needs its own loader
package, and PEFT needs a LoRA dispatcher for that layer type or the adapter
cannot be attached at all. Both are knowable up front, so a repository is
reported as usable, usable-after-a-pip-install, or unsupported — never
discovered 20 GB into a download.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
from dataclasses import dataclass, field

from .constants import install_command

log = logging.getLogger(__name__)

#: ``qwen3_5`` is the full multimodal config; ``qwen3_5_text`` is the language
#: model on its own. The adapter only ever touches language-model weights, so a
#: text-only repack is just as usable — and roughly a third of the download.
MODEL_TYPES = ("qwen3_5", "qwen3_5_text")
HIDDEN_SIZE = 5120
NUM_LAYERS = 64
VOCAB_SIZE = 248320


@dataclass(frozen=True)
class Shape:
    """The checkpoint an adapter's LoRA layers were cut to fit.

    Two adapters, two shapes. Everything that judges a candidate base model does
    it by comparing four numbers with the ones the adapter expects, so the four
    travel together rather than being read off module constants -- which is what
    made the check answer for the 27B no matter who was asking.
    """

    name: str
    model_types: tuple[str, ...]
    hidden_size: int
    num_layers: int
    vocab_size: int


SHAPE_27B = Shape("Qwen3.6-27B", MODEL_TYPES, HIDDEN_SIZE, NUM_LAYERS, VOCAB_SIZE)

SHAPE_8B = Shape("Qwen3-VL-8B-Instruct", ("qwen3_vl", "qwen3_vl_text"), 4096, 36, 151936)

SCAN_FOLDERS = ("LLM", "transformers", "diffusers")
GGUF_FOLDERS = ("LLM", "unet_gguf", "transformers")
CONFIG_NAME = "config.json"

#: what llama.cpp calls this architecture in general.architecture
GGUF_ARCH = "qwen35"
GGUF_SCAN_DEPTH = 2

#: Shape the adapter was trained against, as llama.cpp spells it in the header.
#:
#: The architecture string alone is not enough: Qwen3.5-9B is also ``qwen35``,
#: and offering it would send somebody into llama.cpp's own refusal --
#: "tensor 'blk.0.attn_gate.weight' has incorrect shape (hint: maybe wrong base
#: model?)" -- after a 5 GB download. The 9B has 32 blocks of width 4096 where
#: the adapter needs 64 of 5120, and those two numbers are in the 4 KB header.
GGUF_BLOCK_COUNT = 64
GGUF_EMBEDDING_LENGTH = 5120

#: The same three numbers for the multimodal 8B rewriter, whose base is
#: Qwen3-VL-8B-Instruct. Its adapter is a different LoRA on a different
#: architecture, so nothing here is shared with the 27B above.
GGUF_ARCH_8B = "qwen3vl"
GGUF_BLOCK_COUNT_8B = 36
GGUF_EMBEDDING_LENGTH_8B = 4096

#: What the shape refusals call each base, in the voice a user would use.
BASE_NAME = "Qwen3.6-27B"
BASE_NAME_8B = "Qwen3-VL-8B-Instruct"

HEADER_KEYS = (
    "general.architecture",
    "general.type",
    "adapter.type",
    "clip.has_vision_encoder",
    "clip.has_audio_encoder",
)

#: quant_method -> (pip package, import name, PEFT can attach LoRA)
#:
#: 'fp8' looks self-contained -- Transformers ships the integration and PEFT
#: wraps its FP8Linear like any other nn.Linear -- but the forward pass calls
#: out to the `kernels` package, and fails only once generation starts. Listing
#: it here moves that failure to before the download.
QUANT_RUNTIME = {
    "none": ("", "", True),
    "fp8": ("kernels", "kernels", True),
    "bitsandbytes": ("bitsandbytes", "bitsandbytes", True),
    "bitsandbytes_4bit": ("bitsandbytes", "bitsandbytes", True),
    "bitsandbytes_8bit": ("bitsandbytes", "bitsandbytes", True),
    "awq": ("autoawq", "awq", True),
    "gptq": ("gptqmodel", "gptqmodel", True),
    "hqq": ("hqq", "hqq", True),
    "eetq": ("eetq", "eetq", True),
    "aqlm": ("aqlm", "aqlm", True),
    "torchao": ("torchao", "torchao", True),
    "compressed-tensors": ("compressed-tensors", "compressed_tensors", False),
    "modelopt": ("nvidia-modelopt", "modelopt", False),
    "quanto": ("optimum-quanto", "optimum_quanto", False),
    "fbgemm_fp8": ("fbgemm-gpu", "fbgemm_gpu", False),
}

#: quant_method -> what the pip install still does not buy you.
#:
#: Naming the package is honest but incomplete for FP8: `kernels` is a loader,
#: and the matrix multiply itself is downloaded from the Hub the first time a
#: token is generated. Somebody who installs the package and hits *that* has
#: paid for a 28.8 GB download twice over, so the caveat travels with the
#: instruction rather than living only in the README.
QUANT_CAVEAT = {
    "fp8": (
        "installing it is only half of it: the FP8 matmul is a Triton kernel that "
        "transformers then downloads from 'kernels-community/finegrained-fp8' on the "
        "first generation, and that needs a build matching this torch and CUDA version"
    ),
}


@dataclass
class ModelReport:
    """What a ``config.json`` says about a candidate base model."""

    source: str
    usable: bool = False
    architecture_ok: bool = False
    quant_method: str = "none"
    missing_package: str = ""
    lora_supported: bool = True
    details: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        head = "usable" if self.usable else "NOT usable"
        quant = self.quant_method if self.quant_method != "none" else "unquantized"
        lines = [f"{self.source}: {head} ({quant})"]
        lines.extend(f"  - {problem}" for problem in self.problems)
        return "\n".join(lines)


def _text_config(config: dict) -> dict:
    return config.get("text_config") or config


def quant_method(config: dict) -> str:
    for holder in (config, _text_config(config)):
        quant = holder.get("quantization_config")
        if isinstance(quant, dict) and quant.get("quant_method"):
            return str(quant["quant_method"]).lower()
    return "none"


def is_prequantized(config: dict) -> bool:
    return quant_method(config) != "none"


def _installed(import_name: str) -> bool:
    if not import_name:
        return True
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError):
        return False


def evaluate(config: dict | None, source: str, shape: Shape = SHAPE_27B) -> ModelReport:
    """Judge a candidate from its ``config.json`` alone, against one adapter's shape."""
    report = ModelReport(source=source)
    if not config:
        report.problems.append("config.json is missing or unreadable")
        return report

    text = _text_config(config)
    report.details = {
        "model_type": config.get("model_type"),
        "hidden_size": text.get("hidden_size"),
        "num_hidden_layers": text.get("num_hidden_layers"),
        "vocab_size": text.get("vocab_size"),
    }

    expected = {
        "hidden_size": shape.hidden_size,
        "num_hidden_layers": shape.num_layers,
        "vocab_size": shape.vocab_size,
    }
    mismatched = [
        f"{key} is {report.details.get(key)!r}, the adapter needs {value!r}"
        for key, value in expected.items()
        if report.details.get(key) != value
    ]
    if report.details.get("model_type") not in shape.model_types:
        mismatched.insert(
            0,
            f"model_type is {report.details.get('model_type')!r}, "
            f"the adapter needs one of {' or '.join(shape.model_types)}",
        )
    report.architecture_ok = not mismatched
    if mismatched:
        report.problems.append(
            f"this is not {shape.name}, so the adapter's LoRA layers do not exist in it"
        )
        report.problems.extend(mismatched)
        return report

    report.quant_method = quant_method(config)
    package, import_name, lora_supported = QUANT_RUNTIME.get(
        report.quant_method, (report.quant_method, report.quant_method.replace("-", "_"), False)
    )
    report.lora_supported = lora_supported

    if not lora_supported:
        # Deliberately *instead of* the missing-package line, not alongside it.
        # The package would load the weights fine; PEFT would still have nowhere
        # to hang the LoRA, so the run fails exactly as it does now. Printing an
        # install command next to "this cannot work" invites somebody to spend a
        # pip install and a 20 GB download proving the second line right.
        report.problems.append(
            f"PEFT has no LoRA dispatcher for '{report.quant_method}' layers, so the adapter "
            f"cannot be attached to this build. No package changes that: pick a bf16 or "
            f"bitsandbytes 4-bit entry from the model list instead."
        )
    elif not _installed(import_name):
        report.missing_package = package
        message = (
            f"the '{report.quant_method}' checkpoint needs the '{package}' package, "
            f"which is not installed in this Python environment. Install it with:\n"
            f"      {install_command(package)}"
        )
        caveat = QUANT_CAVEAT.get(report.quant_method)
        if caveat:
            message += f"\n    Note: {caveat}."
        report.problems.append(message)

    report.usable = report.architecture_ok and lora_supported and not report.missing_package
    return report


def read_local_config(directory: str) -> dict | None:
    path = os.path.join(directory, CONFIG_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def fetch_remote_config(repo_id: str, revision: str = "main") -> dict | None:
    """Fetch only ``config.json`` — 4 KB against a 20-52 GB download."""
    import requests

    from .download import _headers, access_token, endpoint

    url = f"{endpoint()}/{repo_id}/resolve/{revision}/{CONFIG_NAME}"
    try:
        response = requests.get(url, headers=_headers(access_token()), timeout=(15, 30))
    except Exception as error:
        log.warning("[minimax_h3_rewriter.fetch_remote_config] %s: %s", repo_id, error)
        return None
    if response.status_code >= 400:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def inspect_local(directory: str, shape: Shape = SHAPE_27B) -> ModelReport:
    return evaluate(read_local_config(directory), directory, shape)


def inspect_repo(repo_id: str, revision: str = "main", shape: Shape = SHAPE_27B) -> ModelReport:
    return evaluate(fetch_remote_config(repo_id, revision), repo_id, shape)


def _hf_cache_roots() -> list[str]:
    home = os.environ.get("HF_HOME")
    if home:
        yield_paths = [os.path.join(home, "hub")]
    else:
        yield_paths = [os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")]
    hub = os.environ.get("HF_HUB_CACHE")
    if hub:
        yield_paths.insert(0, hub)
    return [path for path in yield_paths if os.path.isdir(path)]


def _comfy_roots(names: tuple[str, ...] = SCAN_FOLDERS) -> list[str]:
    try:
        import folder_paths
    except ImportError:
        return []

    roots = []
    for name in names:
        try:
            candidates = list(folder_paths.get_folder_paths(name))
        except KeyError:
            candidates = []
        try:
            candidates.append(os.path.join(folder_paths.models_dir, name))
        except Exception:
            log.debug("[minimax_h3_rewriter._comfy_roots] no models_dir", exc_info=True)
        # A registered folder need not exist: extra_model_paths.yaml maps them in
        # from anywhere and some entries are simply wrong.
        for path in candidates:
            if os.path.isdir(path) and path not in roots:
                roots.append(path)
    return roots


def _snapshot_dirs(cache_root: str) -> list[str]:
    found = []
    try:
        entries = os.listdir(cache_root)
    except OSError:
        return found
    for entry in entries:
        if not entry.startswith("models--"):
            continue
        snapshots = os.path.join(cache_root, entry, "snapshots")
        if not os.path.isdir(snapshots):
            continue
        try:
            revisions = os.listdir(snapshots)
        except OSError:
            continue
        found.extend(os.path.join(snapshots, revision) for revision in revisions)
    return found


def scan_local(shape: Shape = SHAPE_27B) -> list[tuple[str, str]]:
    """Return ``(label, directory)`` for every local checkpoint that fits.

    Both the ComfyUI model folders and the Hugging Face cache are searched, so a
    copy pulled by any other tool is found rather than downloaded twice. A
    directory only qualifies once its weights are actually there: a Hugging Face
    cache entry can hold nothing but ``config.json``, and offering that as a
    choice would fail at load time instead of here.
    """
    from .paths import base_model_is_complete

    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def consider(directory: str, name: str) -> None:
        key = os.path.normcase(os.path.abspath(directory))
        if key in seen:
            return
        seen.add(key)
        config = read_local_config(directory)
        if not config:
            return
        report = evaluate(config, directory, shape)
        if not report.architecture_ok or not base_model_is_complete(directory):
            return
        label = name
        if report.quant_method != "none":
            label += f" [{report.quant_method}]"
        if not report.usable:
            label += " (unusable)"
        found.append((label, directory))

    for root in _comfy_roots():
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            consider(os.path.join(root, entry), entry)

    for cache_root in _hf_cache_roots():
        for snapshot in _snapshot_dirs(cache_root):
            repo = os.path.basename(os.path.dirname(os.path.dirname(snapshot)))
            pretty = repo.replace("models--", "", 1).replace("--", "/")
            consider(snapshot, f"HF cache: {pretty}@{os.path.basename(snapshot)[:8]}")

    return found


_GGUF_HEADER_CACHE: dict[tuple[str, int, int], dict] = {}


def gguf_header(path: str) -> dict:
    """Architecture, type and shape of a GGUF, read from its header only.

    The type matters: a converted LoRA carries the *same* architecture as the
    model it was trained on, so architecture alone would offer a 3.5 GB adapter
    as if it were a base model. The shape matters for the same reason in the
    other direction — see ``GGUF_BLOCK_COUNT``.

    Cached per file identity, so a folder of large quants costs no more than a
    stat each after the first pass.
    """
    empty = {"arch": "", "kind": "", "blocks": None, "width": None, "vision": False, "audio": False}
    try:
        stat = os.stat(path)
    except OSError:
        return empty
    key = (os.path.normcase(path), stat.st_size, int(stat.st_mtime))
    cached = _GGUF_HEADER_CACHE.get(key)
    if cached is not None:
        return cached

    header = dict(empty)
    try:
        from . import gguf_meta

        def also(found: dict) -> tuple[str, ...]:
            arch = found.get("general.architecture")
            return (f"{arch}.block_count", f"{arch}.embedding_length") if arch else ()

        value = gguf_meta.keys(path, HEADER_KEYS, probe=also, verify=True).get

        header["arch"] = str(value("general.architecture") or "")
        header["kind"] = str(value("general.type") or "")
        if not header["kind"]:
            header["kind"] = "adapter" if value("adapter.type") is not None else "model"
        header["vision"] = bool(value("clip.has_vision_encoder") or False)
        header["audio"] = bool(value("clip.has_audio_encoder") or False)
        if header["arch"]:
            blocks = value(f"{header['arch']}.block_count")
            width = value(f"{header['arch']}.embedding_length")
            header["blocks"] = int(blocks) if blocks is not None else None
            header["width"] = int(width) if width is not None else None
    except Exception:
        log.debug("[minimax_h3_rewriter.gguf_header] %s unreadable", path, exc_info=True)

    _GGUF_HEADER_CACHE[key] = header
    return header


def gguf_info(path: str) -> tuple[str, str]:
    """``(general.architecture, general.type)``."""
    header = gguf_header(path)
    return header["arch"], header["kind"]


def gguf_architecture(path: str) -> str:
    return gguf_header(path)["arch"]


def gguf_problem(
    path: str,
    arch: str = GGUF_ARCH,
    blocks_wanted: int = GGUF_BLOCK_COUNT,
    width_wanted: int = GGUF_EMBEDDING_LENGTH,
    base: str = BASE_NAME,
) -> str:
    """Why this GGUF cannot host the adapter, or "" when it can.

    An adapter is bound to one shape. llama.cpp does catch the mismatch, but
    only once the weights are in memory, and it reports it as a tensor shape
    error that says nothing about which model to use instead.

    The shape is a parameter because there are two rewriters now. Everything
    that makes the message worth reading -- naming the base, naming both shapes,
    saying what happens if you run it anyway -- is the same for either.
    """
    header = gguf_header(path)
    if not header["arch"]:
        return f"'{os.path.basename(path)}' is not a readable GGUF file"
    if header["arch"] != arch:
        return (
            f"'{os.path.basename(path)}' is a '{header['arch']}' model; the adapter needs "
            f"'{arch}' ({base})"
        )
    if header["kind"] == "adapter":
        return f"'{os.path.basename(path)}' is a LoRA adapter, not a base model"

    blocks, width = header["blocks"], header["width"]
    if blocks is None or width is None:
        return ""
    if blocks != blocks_wanted or width != width_wanted:
        return (
            f"'{os.path.basename(path)}' has {blocks} blocks of width {width}; the adapter was "
            f"trained on {blocks_wanted} of {width_wanted} ({base}). It shares the '{arch}' "
            f"architecture but not the shape, so llama.cpp cannot attach the LoRA to it — "
            f"the model will run, but as a plain one with no rewriter."
        )
    return ""


def gguf_problem_8b(path: str) -> str:
    """The same question for the multimodal 8B rewriter's base model."""
    return gguf_problem(
        path, GGUF_ARCH_8B, GGUF_BLOCK_COUNT_8B, GGUF_EMBEDDING_LENGTH_8B, BASE_NAME_8B
    )


def _gguf_candidates(root: str, depth: int) -> list[str]:
    found = []
    stack = [(root, 0)]
    while stack:
        directory, level = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if level < depth:
                    stack.append((entry.path, level + 1))
            elif entry.name.lower().endswith(".gguf"):
                found.append(entry.path)
    return found


def _scan_gguf(kind: str, arch: str | None = GGUF_ARCH) -> list[tuple[str, str]]:
    """``(label, path)`` for local GGUFs of one kind, optionally one architecture.

    ``arch=None`` accepts anything readable, which is what the guided writers
    want: they carry the format in the prompt rather than in an adapter, so any
    instruction-following model will do and the label says which one it is.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for root in _comfy_roots(GGUF_FOLDERS):
        for path in _gguf_candidates(root, GGUF_SCAN_DEPTH):
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            header = gguf_header(path)
            if not header["arch"] or header["kind"] != kind:
                continue
            if arch is not None and header["arch"] != arch:
                continue
            try:
                size = os.path.getsize(path) / 1024 ** 3
            except OSError:
                size = 0.0
            tag = "gguf" if arch is not None else header["arch"]
            label = f"{os.path.basename(path)} [{tag}, {size:.1f} GB]"
            if arch is not None and kind == "model" and gguf_problem(path):
                label += " (wrong size for the adapter)"
            found.append((label, path))

    return found


def scan_local_gguf() -> list[tuple[str, str]]:
    """Return ``(label, path)`` for local GGUF *base models* of this architecture.

    Only the header is read, and the answer is cached per file identity, so a
    folder of large quants costs no more than a stat each after the first pass.
    """
    return _scan_gguf("model")


def scan_local_gguf_adapters() -> list[tuple[str, str]]:
    """Return ``(label, path)`` for every local GGUF LoRA adapter, any architecture.

    No longer filtered to one architecture: there are two rewriters now, and a
    converted LoRA for either is a legitimate answer. The label carries the
    architecture instead, so ``qwen35`` and ``qwen3vl`` are told apart at a
    glance. A mismatched pair is refused by llama.cpp, by name -- which is more
    than a scan that silently left the file out of the list could manage.
    """
    return _scan_gguf("adapter", arch=None)


def scan_writer_gguf() -> list[tuple[str, str]]:
    """Return ``(label, path)`` for every local GGUF base model, any architecture."""
    return _scan_gguf("model", arch=None)


def _pair_mmproj(model: str, projectors: list[str], models: int = 1) -> str:
    """Pick the projector belonging to one model within the same folder.

    One model and one projector alone together is unambiguous, and a folder per
    captioner is what the node's own downloads produce. Otherwise the names are
    compared: ``mmproj-Qwen2.5-Omni-3B-Q8_0.gguf`` shares a stem with
    ``Qwen2.5-Omni-3B-Q4_K_M.gguf`` and with nothing else in the folder.

    ``models`` matters more than it looks. A flat ``models/LLM`` holding a dozen
    unrelated GGUFs and exactly one projector used to satisfy "only one
    projector, so it must be the right one", and every model in that folder was
    offered paired with it -- a 27B text model handed a projector built for an
    8B, which loads and then writes gibberish rather than failing. So the
    shortcut applies only when there is nothing else it could belong to.

    Pairing the wrong two produces gibberish rather than an error, so an
    ambiguous folder yields nothing instead of a guess.
    """
    if len(projectors) == 1 and models <= 1:
        return projectors[0]

    def stem_of(path: str, drop_mmproj: bool = False) -> str:
        name = os.path.splitext(os.path.basename(path))[0].lower()
        if drop_mmproj:
            name = name.replace("mmproj", "")
        return name.strip("-_. ")

    stem = stem_of(model)
    best, best_score = "", 0
    for projector in projectors:
        other = stem_of(projector, drop_mmproj=True)
        score = len(os.path.commonprefix([stem, other]))
        if other and other in stem:
            score = max(score, len(other))
        if stem and stem in other:
            score = max(score, len(stem))
        if score > best_score:
            best, best_score = projector, score
    return best if best_score >= 6 else ""


def scan_captioner_gguf(arch: str | None = None) -> list[tuple[str, str, str]]:
    """Return ``(label, model path, mmproj path)`` for local multimodal pairs.

    A captioner is two files, and they have to come from the same conversion.
    Only folders that hold both are offered, so the node never starts a run that
    is missing half of itself.

    ``arch`` narrows the answer to one architecture, which is what the 8B
    rewriter wants: any multimodal pair will caption, but only a ``qwen3vl`` one
    can carry its LoRA.
    """
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    by_directory: dict[str, tuple[list[str], list[str]]] = {}

    for root in _comfy_roots(GGUF_FOLDERS):
        for path in _gguf_candidates(root, GGUF_SCAN_DEPTH):
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            header = gguf_header(path)
            if not header["arch"]:
                continue
            directory = os.path.dirname(key)
            models, projectors = by_directory.setdefault(directory, ([], []))
            if header["kind"] == "mmproj":
                projectors.append(path)
            elif header["kind"] == "model":
                models.append(path)

    for _directory, (models, projectors) in sorted(by_directory.items()):
        if not projectors:
            continue
        for model in sorted(models):
            # After counting the models, not before: how many there are is what
            # decides whether a lone projector in the folder can be trusted.
            if arch is not None and gguf_header(model)["arch"] != arch:
                continue
            projector = _pair_mmproj(model, projectors, len(models))
            if not projector:
                log.info(
                    "[minimax_h3_rewriter.scan_captioner_gguf] no obvious projector for %s among %s",
                    model, [os.path.basename(p) for p in projectors],
                )
                continue
            header = gguf_header(projector)
            modalities = ", ".join(
                name for name, present in (("vision", header["vision"]), ("audio", header["audio"])) if present
            ) or "unknown"
            try:
                size = (os.path.getsize(model) + os.path.getsize(projector)) / 1024 ** 3
            except OSError:
                size = 0.0
            label = f"{os.path.basename(model)} [+mmproj, {modalities}, {size:.1f} GB]"
            found.append((label, model, projector))

    return found
