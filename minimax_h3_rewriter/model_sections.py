"""What each list in ``models.json`` is for, and which node reads which.

The knowledge was spread across five map builders and a hardcoded array in
JavaScript: ``nodes._build_model_map`` knew the 27B wanted ``models``,
``writer_8b`` knew it wanted ``models_8b``, and ``model_list_button.js`` knew
which nodes deserved a button at all. Nothing knew all of it, so nothing could
answer the question the model-list window asks -- "this node is open, what may
go in its dropdown, and what has to be true of it?"

That is what this table is. It holds no behaviour: the shapes and architecture
strings still live in ``discovery``, the reading and writing still live in
``catalog``, and the choice lists are still built by the nodes themselves. This
only says which of them belongs to which list, in one place a test can compare
against the JavaScript.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from dataclasses import dataclass

from . import catalog, discovery, paths

log = logging.getLogger(__name__)

MMPROJ_UNUSED = "unused"
MMPROJ_OPTIONAL = "optional"
MMPROJ_REQUIRED = "required"


@dataclass(frozen=True)
class SectionSpec:
    """One list in ``models.json``, and what an entry in it has to be."""

    key: str
    title: str
    blurb: str
    formats: tuple[str, ...]
    default_format: str
    mmproj: str = MMPROJ_UNUSED
    mmproj_needs: tuple[str, ...] = ()
    shape: discovery.Shape | None = None
    gguf: tuple[str, int, int] | None = None
    base_name: str = ""
    renders_template: bool = False

    @property
    def needs_mmproj(self) -> bool:
        return self.mmproj == MMPROJ_REQUIRED

    @property
    def gguf_only(self) -> bool:
        return self.formats == (catalog.FORMAT_GGUF,)


SECTIONS: dict[str, SectionSpec] = {
    "qwen_rewriters": SectionSpec(
        key="qwen_rewriters", title="Full Qwen rewriters",
        blurb=("Full LightX2V Qwen3.5 9B / Qwen3.8 27B checkpoints, or their GGUF conversions. "
               "Name the task Ref2VA or FL2VA in the entry name or source. These are full models; no LoRA is loaded."),
        formats=(catalog.FORMAT_TRANSFORMERS, catalog.FORMAT_GGUF),
        default_format=catalog.FORMAT_TRANSFORMERS,
        mmproj=MMPROJ_REQUIRED, mmproj_needs=("vision",),
    ),
    "models": SectionSpec(
        key="models",
        title="27B rewriter",
        blurb=(
            "Base models for the prompt-rewriter LoRA. The adapter is cut to one "
            "checkpoint, so anything here has to be that checkpoint."
        ),
        formats=(catalog.FORMAT_TRANSFORMERS, catalog.FORMAT_GGUF),
        default_format=catalog.FORMAT_TRANSFORMERS,
        shape=discovery.SHAPE_27B,
        gguf=(
            discovery.GGUF_ARCH,
            discovery.GGUF_BLOCK_COUNT,
            discovery.GGUF_EMBEDDING_LENGTH,
        ),
        base_name=discovery.BASE_NAME,
        renders_template=True,
    ),
    "models_8b": SectionSpec(
        key="models_8b",
        title="8B rewriter",
        blurb=(
            "Base models for the multimodal 8B rewriter, which reads the frames "
            "itself. A different architecture from the 27B: an entry from one "
            "list will not load in the other's node."
        ),
        formats=(catalog.FORMAT_TRANSFORMERS, catalog.FORMAT_GGUF),
        default_format=catalog.FORMAT_TRANSFORMERS,
        mmproj=MMPROJ_REQUIRED,
        mmproj_needs=("vision",),
        shape=discovery.SHAPE_8B,
        gguf=(
            discovery.GGUF_ARCH_8B,
            discovery.GGUF_BLOCK_COUNT_8B,
            discovery.GGUF_EMBEDDING_LENGTH_8B,
        ),
        base_name=discovery.BASE_NAME_8B,
    ),
    "models_omni": SectionSpec(
        key="models_omni",
        title="Omni rewriter",
        blurb=(
            "Base models for the Omni rewriter, which reads frames, clips and "
            "sound. The projector has to carry the audio encoder as well -- a "
            "vision-only build loads and then hears nothing."
        ),
        formats=(catalog.FORMAT_TRANSFORMERS, catalog.FORMAT_GGUF),
        default_format=catalog.FORMAT_TRANSFORMERS,
        mmproj=MMPROJ_REQUIRED,
        mmproj_needs=("vision", "audio"),
        shape=discovery.SHAPE_OMNI,
        gguf=(
            discovery.GGUF_ARCH_OMNI,
            discovery.GGUF_BLOCK_COUNT_OMNI,
            discovery.GGUF_EMBEDDING_LENGTH_OMNI,
        ),
        base_name=discovery.BASE_NAME_OMNI,
    ),
    "writers": SectionSpec(
        key="writers",
        title="Guided writers",
        blurb=(
            "Models for the guided writer nodes. Those carry the format in the "
            "system prompt instead of in a LoRA, so any instruction-following "
            "GGUF with a chat template works and nothing has to match a shape."
        ),
        formats=(catalog.FORMAT_GGUF,),
        default_format=catalog.FORMAT_GGUF,
        renders_template=True,
    ),
    "captioners": SectionSpec(
        key="captioners",
        title="Captioners",
        blurb=(
            "Multimodal pairs for the caption nodes. Publishing a GGUF and an "
            "mmproj is not the same as llama.cpp's mtmd being able to load "
            "them, so try one before relying on it."
        ),
        formats=(catalog.FORMAT_GGUF,),
        default_format=catalog.FORMAT_GGUF,
        mmproj=MMPROJ_REQUIRED,
        mmproj_needs=("vision",),
    ),
}


@dataclass(frozen=True)
class NodeSection:
    """One dropdown on one node, and the list it is fed from.

    Python only ever needs the section: ``sections_of`` is what the window's
    request is answered from. The widget name is carried anyway because the
    browser needs it -- an entry added from one node belongs in the dropdown of
    every other node fed from that list -- and the copy that does the work is
    the one in ``model_list_button.js``. Keeping the name here is what lets
    ``test_model_sections`` compare the two tables whole instead of half.
    """

    widget: str
    section: str


def _at(*pairs: tuple[str, str]) -> tuple[NodeSection, ...]:
    return tuple(NodeSection(widget, section) for widget, section in pairs)


NODE_SECTIONS: dict[str, tuple[NodeSection, ...]] = {
    "MiniMaxH3QwenRewriter": _at(("model", "qwen_rewriters")),
    "MiniMaxH3PromptRewriter": _at(("model", "models")),
    "MiniMaxH3PromptWriter8B": _at(("model", "models_8b")),
    "MiniMaxH3PromptWriterOmni": _at(("model", "models_omni")),
    "MiniMaxH3GuidedWriter": _at(("model", "writers")),
    "MiniMaxH3GuidedWriterRef": _at(("model", "writers")),
    "MiniMaxH3PromptReducer": _at(("model", "writers")),
    "MiniMaxH3ReferenceCaption": _at(("model", "captioners")),
    "MiniMaxH3MultiReferenceCaption": _at(("model", "captioners")),
    "MiniMaxH3UniversalWriter": _at(
        ("caption_model", "captioners"), ("writer_model", "writers")
    ),
    "MiniMaxH3UniversalRewriter": _at(
        ("model_27b", "models"), ("model_8b", "models_8b"), ("model_omni", "models_omni")
    ),
}


def spec(section: str) -> SectionSpec:
    found = SECTIONS.get(section)
    if found is None:
        raise KeyError(f"'{section}' is not a model list in this pack")
    return found


def for_node(node_id: str) -> tuple[NodeSection, ...]:
    """The dropdowns one node fills from ``models.json``, in the order it shows them."""
    return NODE_SECTIONS.get(node_id, ())


def sections_of(node_id: str) -> tuple[str, ...]:
    """The lists one node reads, each once, in the order it shows them."""
    seen: list[str] = []
    for entry in for_node(node_id):
        if entry.section not in seen:
            seen.append(entry.section)
    return tuple(seen)


def requirements(section: str) -> list[str]:
    """What has to be true of an entry here, in the words the window prints."""
    found = spec(section)
    if section == "qwen_rewriters":
        return ["SFT: full qwen3_5 checkpoint with vision encoder and LM head (9B or 27B).",
                "GGUF: qwen35, 32 x 4096 or 64 x 5120, plus the matching vision mmproj.",
                "Name the checkpoint Ref2VA or FL2VA explicitly. These checkpoints do not encode audio."]
    lines: list[str] = []

    if found.gguf_only:
        lines.append("GGUF only -- one file run by llama.cpp, not a folder of safetensors.")
    else:
        lines.append(
            "Either a transformers folder or a GGUF file; the GGUF is the low-VRAM route."
        )

    if found.shape is not None:
        lines.append(
            f"transformers: model_type {' or '.join(found.shape.model_types)}, "
            f"{found.shape.num_layers} layers of width {found.shape.hidden_size}, "
            f"vocabulary {found.shape.vocab_size} ({found.shape.name})."
        )
    if found.gguf is not None:
        arch, blocks, width = found.gguf
        lines.append(
            f"GGUF: architecture '{arch}', {blocks} blocks of width {width}. The "
            f"architecture alone is not enough -- other models share it at other "
            f"sizes, and the adapter cannot attach to those."
        )
    else:
        lines.append(
            "Any architecture, as long as the file is a language model with an "
            "embedded chat template. Encoder halves of image pipelines are not."
        )

    if found.needs_mmproj:
        wants = " and ".join(found.mmproj_needs) if found.mmproj_needs else "vision"
        lines.append(
            f"A GGUF here is two files: the model and its 'mmproj' projector, from "
            f"the same conversion. The projector has to carry the {wants} encoder."
        )
    return lines


_CHOICE_SOURCE = {
    "qwen_rewriters": ("qwen_models", "model_choices"),
    "models": ("nodes", "model_choices"),
    "models_8b": ("writer_8b", "model_choices"),
    "models_omni": ("writer_omni", "model_choices"),
    "writers": ("nodes", "writer_choices"),
    "captioners": ("nodes", "captioner_choices"),
}


def choices(section: str) -> list[str]:
    """The dropdown for this list as ``INPUT_TYPES`` would build it right now.

    Taken from the node rather than rebuilt, so what the window puts into an
    open graph is what the next ``/object_info`` will say -- the same labels in
    the same order, the scanned entries included, and the ``!!`` warning first
    when the file is broken.
    """
    module, name = _CHOICE_SOURCE[section]
    try:
        found = importlib.import_module(f".{module}", __package__)
        return list(getattr(found, name)())
    except Exception:
        log.warning(
            "[minimax_h3_rewriter.model_sections] could not read the '%s' dropdown",
            section, exc_info=True,
        )
        return []


_MAP_SOURCE = {
    "qwen_rewriters": ("qwen_models", "_build_model_map"),
    "models": ("nodes", "_build_model_map"),
    "models_8b": ("writer_8b", "_build_model_map"),
    "models_omni": ("writer_omni", "_build_model_map"),
    "writers": ("nodes", "_build_writer_map"),
    "captioners": ("nodes", "_build_captioner_map"),
}


def _map(section: str) -> dict:
    """Every label this list resolves, freshly built, the hidden copies included."""
    module, name = _MAP_SOURCE[section]
    try:
        found = importlib.import_module(f".{module}", __package__)
        return dict(getattr(found, name)())
    except Exception:
        log.warning(
            "[minimax_h3_rewriter.model_sections] could not build the '%s' map",
            section, exc_info=True,
        )
        return {}


def on_disk(section: str, fmt: str, repo: str, file: str = "", mmproj: str = "") -> tuple[str, ...]:
    """Where one entry's files already are, the model first, or ``()`` while it is a download.

    Asked of ``paths.present_*``, which is where the nodes look before they fetch
    anything, in the shape this list runs: a folder, one file, or a model and its
    projector. So a scanned row hidden as a copy of the entry is exactly the file
    the entry would have run.
    """
    try:
        if fmt != catalog.FORMAT_GGUF:
            where = paths.present_folder(repo)
            return (where,) if where else ()
        if spec(section).needs_mmproj:
            return tuple(paths.present_pair(repo, file, mmproj) or ())
        where = paths.present_file(repo, file)
        return (where,) if where else ()
    except Exception:
        log.debug(
            "[minimax_h3_rewriter.model_sections] could not look for '%s' on disk", repo,
            exc_info=True,
        )
        return ()


def copy_of(held: list[tuple[str, tuple[str, ...]]], *where: str) -> str:
    """The entry these scanned files are the download of, or an empty string.

    ``held`` pairs each entry's label with ``on_disk`` for it. A row that is the
    same file as an entry is the same choice twice -- the one the list names
    wins, and the scanned row stays out of the dropdown.
    """
    for label, found in held:
        if found and len(found) == len(where):
            if all(paths.same_path(one, other) for one, other in zip(found, where)):
                return label
    return ""


def label_now(mapping: dict, section: str, remembered: str) -> str:
    """What the dropdown calls a remembered label today, or "" when it names nothing.

    A saved label is still good in three ways: it is in the dropdown as it is;
    it is the scanned row of a file an entry now stands for, hidden and answered
    by ``copy_of``; or it is an entry's label from before its size, VRAM note or
    note was edited, found by name. The last is off while the file does not
    parse -- the list is the packaged one then, and a name matched in it may be
    a different file from the one the user's own entry of that name points at.
    """
    if not remembered:
        return ""
    found = mapping.get(remembered)
    if found is not None:
        return getattr(found, "copy_of", "") or remembered
    if catalog.problem():
        return ""
    renamed = catalog.current_label(section, remembered)
    return renamed if renamed and renamed in mapping else ""


def current_labels(section: str, remembered: list[str]) -> dict[str, str]:
    """Remembered labels that now read differently, mapped to what they read now.

    For the browser, which puts a graph back in step when it opens one. A label
    that is current, or names nothing any more, is left out.
    """
    mapping = _map(section)
    moved = {}
    for label in remembered:
        now = label_now(mapping, section, str(label))
        if now and now != label:
            moved[str(label)] = now
    return moved


def scanned_label(section: str, path: str) -> str:
    """The dropdown row for a file found on disk, or an empty string.

    What a node holding a just-deleted entry can move to: the file is still
    there, and without the entry its scanned row is in the dropdown again.
    """
    if not path:
        return ""
    for label, found in _map(section).items():
        if not label.startswith(SCANNED_PREFIXES) or getattr(found, "copy_of", ""):
            continue
        if paths.same_path(getattr(found, "reference", ""), path):
            return label
    return ""


_NETWORK_ADVICE = (
    "This arrived over the ComfyUI API, which anything that can reach the port may call, "
    "and looking at a network path is already an authentication attempt against the host "
    "it names. Map the share to a drive letter and use that. A path written into "
    "models.json by hand is still unrestricted -- the 'Open models.json' button opens it."
)


def clean_entry(section: str, raw: dict) -> dict:
    """One entry as it should be written, or a refusal naming what is wrong.

    The validator the file has never had. ``catalog._entries`` drops a malformed
    entry with a warning to a console nobody reads, so the only symptom of a typo
    today is a model that is simply not in the dropdown.
    """
    found = spec(section)

    name = str(raw.get("name") or "").strip()
    if not name:
        raise catalog.CatalogWriteError("Give the entry a name -- it is what the dropdown shows.")

    fmt = str(raw.get("format") or found.default_format).strip().lower()
    if fmt not in found.formats:
        offered = " or ".join(f"'{one}'" for one in found.formats)
        raise catalog.CatalogWriteError(
            f"'{found.title}' takes {offered}, not '{fmt}'."
        )

    entry: dict = {"name": name, "format": fmt}
    for field in ("repo", "file", "mmproj"):
        value = str(raw.get(field) or "").strip()
        if value:
            paths.refuse_network_path(value, f"'{field}'", _NETWORK_ADVICE)
            entry[field] = value

    if not entry.get("repo") and not entry.get("file"):
        raise catalog.CatalogWriteError(
            "Give a Hugging Face repository id, or a path to something on this machine. "
            "With neither there is nothing to load."
        )
    if fmt == catalog.FORMAT_GGUF and not entry.get("file"):
        raise catalog.CatalogWriteError(
            "A GGUF entry needs the file name inside the repository, not just the repository."
        )
    if fmt == catalog.FORMAT_TRANSFORMERS:
        stray = [one for one in ("file", "mmproj") if entry.get(one)]
        if stray:
            raise catalog.CatalogWriteError(
                f"A transformers entry is a folder of safetensors, so "
                f"{' and '.join(repr(one) for one in stray)} would be ignored. Choose the "
                f"'gguf' format if this is a single file, or clear those fields."
            )
    if fmt == catalog.FORMAT_GGUF and found.needs_mmproj and not entry.get("mmproj"):
        wants = " and ".join(found.mmproj_needs) if found.mmproj_needs else "vision"
        raise catalog.CatalogWriteError(
            f"'{found.title}' needs the 'mmproj' projector beside the model -- that is where "
            f"the {wants} encoder lives, and the pair comes out of one conversion."
        )

    try:
        size = float(raw.get("download_gb") or 0.0)
    except (TypeError, ValueError):
        raise catalog.CatalogWriteError(
            f"'{raw.get('download_gb')}' is not a download size. Give a number of gigabytes, "
            f"or leave it empty."
        ) from None
    if size < 0:
        raise catalog.CatalogWriteError("A download cannot be a negative number of gigabytes.")
    if size:
        entry["download_gb"] = size

    for field in ("vram", "note"):
        value = str(raw.get(field) or "").strip()
        if value:
            entry[field] = value
    if section == "qwen_rewriters":
        from .qwen_models import family
        try:
            family(entry["name"], entry.get("repo", ""), entry.get("file", ""))
        except ValueError as error:
            raise catalog.CatalogWriteError(str(error)) from None
    return entry


def _local_copy(repo: str, name: str) -> str:
    """Where one file of an entry already sits on this machine, or an empty string.

    The same places the nodes look before they download anything: beside a
    folder the entry names, flat in an ``LLM`` folder, and in the per-repository
    folder a pair is fetched into -- in every ``LLM`` folder, not only the one
    downloads go to.
    """
    if not name:
        return ""
    found = paths.catalog_file(repo, name)
    if found:
        return found if os.path.isfile(found) else ""
    if not paths.looks_like_repo_id(repo):
        return ""
    folder = repo.rstrip("/").split("/")[-1]
    try:
        roots = paths.llm_roots()
    except Exception:
        return ""
    candidates = [os.path.join(root, name) for root in roots]
    candidates += [os.path.join(root, folder, name) for root in roots]
    return next((one for one in candidates if os.path.isfile(one)), "")


def _say(lines: list, level: str, text: str) -> None:
    lines.append({"level": level, "text": text})


def _worst(lines: list) -> str:
    levels = {line["level"] for line in lines}
    if "bad" in levels:
        return "bad"
    return "warn" if "warn" in levels else "good"


_TEMPLATE_PROBE = (
    {"role": "system", "content": "You write video prompts."},
    {"role": "user", "content": "A cat on a windowsill."},
)


def _check_template(section: str, model: str, lines: list) -> None:
    """Whether the file can be turned into a prompt, which the header alone does not say.

    A GGUF without a chat template reads as a perfectly good model -- right
    architecture, right shape -- and the node that renders the template itself
    stops on the first run with "this GGUF has no embedded chat template". Base
    (pretrained) conversions ship without one, and so do some community
    conversions of instruct models. A template the sandbox cannot render stops
    the run the same way, so it is rendered here once rather than only read.

    Where ``llama-mtmd-cli`` applies the template, the binary is the judge and
    falls back to ChatML without one, so there its absence is only a warning.
    """
    from . import chat_template, gguf_meta

    found = spec(section)
    name = os.path.basename(model)
    try:
        metadata = gguf_meta.keys(model, (chat_template.TEMPLATE_KEY, "chat_template"))
    except Exception as error:  # noqa: BLE001 - the header was readable a moment ago
        _say(lines, "warn", f"The chat template in '{name}' could not be read: {error}")
        return

    template = metadata.get(chat_template.TEMPLATE_KEY) or metadata.get("chat_template")
    if not template:
        if found.renders_template:
            _say(
                lines, "bad",
                f"'{name}' carries no chat template, and this node builds its prompt from the "
                f"model's own -- the first run would stop there. Base (pretrained) conversions "
                f"have none; take the instruct build of the same model.",
            )
        else:
            _say(
                lines, "warn",
                f"'{name}' carries no chat template, so llama.cpp falls back to ChatML. That is "
                f"right for Qwen and wrong for most others.",
            )
        return

    if not found.renders_template:
        _say(lines, "good", "It carries its own chat template.")
        return
    try:
        chat_template.render(str(template), [dict(one) for one in _TEMPLATE_PROBE])
    except Exception as error:  # noqa: BLE001 - whatever the template raises is the answer
        _say(
            lines, "bad",
            f"The chat template in '{name}' does not render here ({error}), so the first run "
            f"would stop on it.",
        )
        return
    _say(lines, "good", "Its chat template renders.")


def _check_local_gguf(section: str, entry: dict, model: str, projector: str, lines: list) -> None:
    found = spec(section)
    trouble = gguf_problem(section, model)
    if trouble:
        _say(lines, "bad", trouble)
        return

    header = discovery.gguf_header(model)
    _say(
        lines, "good",
        f"'{os.path.basename(model)}' is a '{header['arch']}' model, "
        f"{header['blocks']} blocks of width {header['width']}"
        + (" plus an MTP draft head, which is never loaded" if header.get("draft_blocks") else "")
        + ". That fits.",
    )
    if found.gguf is not None:
        note = discovery.gguf_base_note(model, found.base_name)
        if note:
            _say(lines, "warn", note)
    _check_template(section, model, lines)
    if not found.needs_mmproj:
        return

    if not projector:
        _say(
            lines, "warn",
            f"The model is here but '{entry.get('mmproj')}' is not, so the projector could "
            f"not be read. It is fetched on the first run.",
        )
        return
    carried = discovery.gguf_header(projector)
    missing = [one for one in found.mmproj_needs if not carried.get(one)]
    if missing:
        _say(
            lines, "bad",
            f"'{os.path.basename(projector)}' carries no {' or '.join(missing)} encoder, so "
            f"this build cannot {'hear' if 'audio' in missing else 'see'}. It is the wrong "
            f"half of a conversion, or a vision-only build of a model that also has sound.",
        )
    else:
        _say(
            lines, "good",
            f"'{os.path.basename(projector)}' carries the "
            f"{' and '.join(found.mmproj_needs)} encoder.",
        )


SHOWN_REPO_FILES = 60
GGUF_SUFFIX = ".gguf"
_SPLIT_PART = re.compile(r"-\d{5}-of-\d{5}\.gguf$", re.IGNORECASE)
_QUANT_TAIL = re.compile(
    r"^(?P<stem>.+?)[-_.](?P<quant>(?:UD-)?(?:I?Q\d\w*|B?F16|F32))$", re.IGNORECASE
)


def suggested_name(path: str) -> str:
    """What the dropdown could call a GGUF file: its stem, with the quantisation apart.

    'Qwen2.5-Omni-7B-Q4_K_M.gguf' reads 'Qwen2.5-Omni-7B Q4_K_M', the way the
    packaged entries are named. A file with no quantisation in its name keeps
    its stem whole.
    """
    stem = path.replace("\\", "/").rsplit("/", 1)[-1]
    if stem.lower().endswith(GGUF_SUFFIX):
        stem = stem[: -len(GGUF_SUFFIX)]
    named = _QUANT_TAIL.match(stem)
    return f"{named.group('stem')} {named.group('quant')}" if named else stem


def _gigabytes(size: int) -> float:
    return round(size / 1_000_000_000, 2)


def _gguf_offers(section: str, files: list[tuple[str, int]]) -> dict:
    """The GGUF files among ``(path, bytes)``, to pick from, smallest first.

    Models and projectors apart, since the projector goes in 'mmproj' -- and a
    list that takes none is offered none. A model split into parts is left out
    and counted instead: an entry names one file, and only that file is fetched.
    """
    models: list[dict] = []
    projectors: list[dict] = []
    split: set[str] = set()
    for path, size in files:
        base = path.replace("\\", "/").rsplit("/", 1)[-1]
        if not base.lower().endswith(GGUF_SUFFIX):
            continue
        part = _SPLIT_PART.search(path)
        if part:
            split.add(path[: part.start()].lower())
            continue
        offer = {"file": path, "gb": _gigabytes(size)}
        if "mmproj" in base.lower():
            projectors.append(offer)
        else:
            offer["name"] = suggested_name(path)
            models.append(offer)

    def order(offer: dict) -> tuple:
        return offer["gb"], offer["file"].lower()

    if spec(section).mmproj == MMPROJ_UNUSED:
        projectors = []
    return {
        "files": sorted(models, key=order),
        "projectors": sorted(projectors, key=order),
        "split": len(split),
    }


def _listed_here(folder: str) -> list[tuple[str, int]]:
    """The files directly inside a folder on this machine, with their sizes."""
    found = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            found.append((name, os.path.getsize(path)))
    return found


def repo_files(section: str, repo: str) -> dict:
    """'Check it' with a repository and no file yet: what is in it to pick from.

    A folder on this machine is read as it stands; anything else that reads as
    a repository id is asked of the Hub, which lists a repository's files and
    their sizes without moving any weights. The network refusal comes first, for
    the same reason it does in ``clean_entry``.
    """
    from . import download

    paths.refuse_network_path(repo, "'repo'", _NETWORK_ADVICE)
    lines: list[dict] = []
    nothing = {"files": [], "projectors": [], "split": 0}
    here = os.path.isdir(repo)
    if not here and not paths.looks_like_repo_id(repo):
        _say(
            lines, "bad",
            f"'{repo}' is neither a folder on this machine nor a Hugging Face repository id "
            f"-- that looks like 'owner/name'.",
        )
        return {"verdict": "bad", "lines": lines, "download_gb": None, **nothing}
    try:
        if here:
            listed = _listed_here(repo)
        else:
            listed = [
                (one.path, one.size)
                for one in download.list_repo_files(repo, token=download.access_token())
            ]
    except Exception as error:
        _say(lines, "bad", f"'{repo}' could not be read: {error}")
        return {"verdict": "bad", "lines": lines, "download_gb": None, **nothing}

    found = spec(section)
    offers = _gguf_offers(section, listed)
    models, projectors, split = offers["files"], offers["projectors"], offers["split"]
    where = "This folder" if here else f"'{repo}'"
    if models:
        held = f"{len(models)} GGUF model{'s' if len(models) != 1 else ''}"
        if found.mmproj != MMPROJ_UNUSED:
            held += f" and {len(projectors)} projector{'s' if len(projectors) != 1 else ''}"
        _say(
            lines, "good",
            f"{where} holds {held}. Pick the model below"
            + (", then its projector" if found.mmproj != MMPROJ_UNUSED else "")
            + ": the file goes into the form, with a name when none is typed yet.",
        )
    else:
        _say(
            lines, "bad",
            f"{where} holds no single-file GGUF model"
            + (", and this list takes GGUF only." if found.gguf_only else
               ". For a folder of safetensors choose the 'transformers' format."),
        )
    if found.needs_mmproj and models and not projectors:
        _say(
            lines, "warn",
            f"There is no projector ('mmproj') in it, and '{found.title}' needs one beside "
            f"the model. The pair comes out of one conversion, so look for a repository "
            f"that carries both.",
        )
    if split:
        _say(
            lines, "note",
            f"{split} more {'is' if split == 1 else 'are'} split into parts and left out: an "
            f"entry names one file, and only that file is fetched.",
        )
    if len(models) > SHOWN_REPO_FILES or len(projectors) > SHOWN_REPO_FILES:
        _say(lines, "note", f"The {SHOWN_REPO_FILES} smallest of each are shown.")
    offers["files"] = models[:SHOWN_REPO_FILES]
    offers["projectors"] = projectors[:SHOWN_REPO_FILES]
    return {"verdict": _worst(lines), "lines": lines, "download_gb": None, **offers}


def _check_remote(section: str, entry: dict, lines: list, picks: dict) -> float | None:
    """What the Hub can be asked without moving any weights.

    A named file the repository does not have puts what it does have into
    ``picks``, so a misspelt name is mended with a click rather than a retype.
    """
    found = spec(section)
    repo = str(entry.get("repo") or "")
    if not paths.looks_like_repo_id(repo):
        _say(
            lines, "warn",
            f"'{repo}' is neither a folder on this machine nor a Hugging Face repository id, "
            f"so nothing could be checked and nothing could be downloaded.",
        )
        return None

    if entry.get("format") == catalog.FORMAT_GGUF:
        from . import download

        wanted = tuple(one for one in (entry.get("file"), entry.get("mmproj")) if one)
        try:
            listed = download.list_repo_files(repo, token=download.access_token())
        except Exception as error:
            _say(lines, "warn", f"'{repo}' could not be read: {error}")
            return None
        chosen = download.select_files(listed, allow=wanted)
        count, size = len(chosen), sum(item.size for item in chosen)
        if count < len(wanted):
            picks.update(_gguf_offers(section, [(one.path, one.size) for one in listed]))
            picks["files"] = picks["files"][:SHOWN_REPO_FILES]
            picks["projectors"] = picks["projectors"][:SHOWN_REPO_FILES]
            offered = picks["files"] or picks["projectors"]
            _say(
                lines, "bad",
                f"'{repo}' exists, but only {count} of the {len(wanted)} named files are in "
                f"it. Check the spelling of {' and '.join(repr(one) for one in wanted)}"
                + (", or pick from what it holds below." if offered else "."),
            )
            return None
        gigabytes = round(size / 1_000_000_000, 2)
        _say(
            lines, "good",
            f"'{repo}' has all {count} named files, {gigabytes:g} GB in total. The header "
            f"itself can only be read once the file is here, so the shape and the chat "
            f"template are checked on the first run -- or by Check it again once it is "
            f"downloaded.",
        )
        return gigabytes

    report = discovery.inspect_repo(repo, shape=found.shape or discovery.SHAPE_27B)
    if not report.details:
        _say(
            lines, "warn",
            f"'{repo}' has no readable config.json -- it may be private, gated, or not a "
            f"transformers repository.",
        )
        return None
    if report.usable:
        _say(lines, "good", f"'{repo}' is {found.shape.name if found.shape else 'usable'} "
                            f"and the adapter can attach to it.")
    else:
        for problem in report.problems:
            _say(lines, "bad" if not report.architecture_ok else "warn", problem)
    return None


def check(section: str, entry: dict) -> dict:
    """Judge one entry as far as it can be judged without downloading weights.

    Local files are read outright -- the header says the architecture, the block
    count and which encoders the projector carries. Anything only on the Hub is
    asked the two questions that can be answered from metadata: does a
    transformers config.json describe the right checkpoint, and do the named GGUF
    files actually exist in that repository. The second is the one that catches a
    typo in ``file``, which today surfaces as a download that fails minutes in.
    """
    found = spec(section)
    if section == "qwen_rewriters" and entry.get("format") != catalog.FORMAT_GGUF:
        from .qwen_models import check_native
        return check_native(entry)
    lines: list[dict] = []
    picks: dict = {}
    size: float | None = None

    if entry.get("format") == catalog.FORMAT_GGUF:
        repo, file, mmproj = (str(entry.get(key) or "") for key in ("repo", "file", "mmproj"))
        where = on_disk(section, catalog.FORMAT_GGUF, repo, file, mmproj)
        model = where[0] if where else _local_copy(repo, file)
        projector = where[1] if len(where) > 1 else _local_copy(repo, mmproj)
        if model:
            _check_local_gguf(section, entry, model, projector, lines)
        else:
            size = _check_remote(section, entry, lines, picks)
    else:
        reference = str(entry.get("repo") or "")
        directory = reference if os.path.isdir(reference) else ""
        if directory:
            report = discovery.inspect_local(directory, found.shape or discovery.SHAPE_27B)
            if report.usable:
                _say(lines, "good", f"'{directory}' is {found.shape.name if found.shape else 'usable'}.")
            else:
                for problem in report.problems:
                    _say(lines, "bad" if not report.architecture_ok else "warn", problem)
        else:
            size = _check_remote(section, entry, lines, picks)

    if not lines:
        _say(lines, "warn", "Nothing could be checked from here.")
    return {"verdict": _worst(lines), "lines": lines, "download_gb": size, **picks}


LOCAL_PREFIX = "on disk: "
OLLAMA_PREFIX = "ollama: "
PROBLEM_PREFIX = "!! "

SCANNED_PREFIXES = (LOCAL_PREFIX, OLLAMA_PREFIX)


def listing(section: str) -> dict:
    """One list as the window shows it: what the file holds, and what the scan found.

    The two are told apart by the prefix rather than by scanning again. The
    scanned half is read-only in the window and is worth showing anyway: without
    it, half the dropdown has no row in the editor and looks like something the
    window has lost.
    """
    entries = []
    for raw in catalog.raw_entries(section):
        entries.append(dict(raw, label=catalog.entry_label(raw)))
    mapping = _map(section)
    copied = {getattr(one, "copy_of", "") for one in mapping.values()} - {""}
    seeded = seed_names_of(section)
    for entry in entries:
        entry["seeded"] = entry.get("name") in seeded
        entry["on_disk"] = bool(_held(section, entry))
        entry["scanned"] = entry["label"] in copied
    found = spec(section)
    return {
        "key": section,
        "title": found.title,
        "blurb": found.blurb,
        "requirements": requirements(section),
        "formats": list(found.formats),
        "default_format": found.default_format,
        "mmproj": found.mmproj,
        "entries": entries,
        "found": [
            label for label, one in mapping.items()
            if label.startswith(SCANNED_PREFIXES) and not getattr(one, "copy_of", "")
        ],
        "restorable": catalog.restorable(section),
    }


def _held(section: str, raw: dict) -> tuple[str, ...]:
    """``on_disk`` for an entry as it is written in the file."""
    fmt = str(raw.get("format") or spec(section).default_format).lower()
    return on_disk(
        section, fmt, str(raw.get("repo") or ""), str(raw.get("file") or ""),
        str(raw.get("mmproj") or ""),
    )


def held_label(section: str, raw: dict) -> str:
    """The scanned row an entry's file falls back to once the entry is gone, or ""."""
    found = _held(section, raw)
    return scanned_label(section, found[0]) if found else ""


def seed_names_of(section: str) -> set[str]:
    """Names the packaged list carries here, so the window can say which are the pack's."""
    return catalog.seed_names(section)


def gguf_problem(section: str, path: str) -> str:
    """Why this GGUF cannot serve in this list, or an empty string when it can."""
    found = spec(section)
    if section == "qwen_rewriters":
        from .qwen_models import gguf_problem as full_qwen_problem
        return full_qwen_problem(path)
    if found.gguf is not None:
        arch, blocks, width = found.gguf
        return discovery.gguf_problem(path, arch, blocks, width, found.base_name)

    header = discovery.gguf_header(path)
    name = os.path.basename(path)
    if not header["arch"]:
        return f"'{name}' is not a readable GGUF file"
    if header["kind"] == "adapter":
        return f"'{name}' is a LoRA adapter, not a base model"
    if header["arch"] in discovery.ENCODER_ARCHS:
        return (
            f"'{name}' is a '{header['arch']}' encoder, not a language model. It has "
            f"nothing to say and no chat template to say it with."
        )
    return ""
