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

    @property
    def needs_mmproj(self) -> bool:
        return self.mmproj == MMPROJ_REQUIRED

    @property
    def gguf_only(self) -> bool:
        return self.formats == (catalog.FORMAT_GGUF,)


SECTIONS: dict[str, SectionSpec] = {
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
    "MiniMaxH3PromptRewriter": _at(("model", "models")),
    "MiniMaxH3PromptWriter8B": _at(("model", "models_8b")),
    "MiniMaxH3PromptWriterOmni": _at(("model", "models_omni")),
    "MiniMaxH3GuidedWriter": _at(("model", "writers")),
    "MiniMaxH3GuidedWriterRef": _at(("model", "writers")),
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
    return entry


def _local_copy(repo: str, name: str) -> str:
    """Where one file of an entry already sits on this machine, or an empty string.

    The same three places the nodes look before they download anything: beside a
    folder the entry names, flat in ``models/LLM``, and in the per-repository
    folder a pair is fetched into.
    """
    if not name:
        return ""
    found = paths.catalog_file(repo, name)
    if found:
        return found if os.path.isfile(found) else ""
    if not paths.looks_like_repo_id(repo):
        return ""
    try:
        candidates = (
            os.path.join(paths.models_root(), name),
            os.path.join(paths.local_dir_for_repo(repo), name),
        )
    except Exception:
        return ""
    return next((one for one in candidates if os.path.isfile(one)), "")


def _say(lines: list, level: str, text: str) -> None:
    lines.append({"level": level, "text": text})


def _worst(lines: list) -> str:
    levels = {line["level"] for line in lines}
    if "bad" in levels:
        return "bad"
    return "warn" if "warn" in levels else "good"


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
        f"{header['blocks']} blocks of width {header['width']}. That fits.",
    )
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


def _check_remote(section: str, entry: dict, lines: list) -> float | None:
    """What the Hub can be asked without moving any weights."""
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
            count, size = download.repo_size(repo, allow=wanted)
        except Exception as error:
            _say(lines, "warn", f"'{repo}' could not be read: {error}")
            return None
        if count < len(wanted):
            _say(
                lines, "bad",
                f"'{repo}' exists, but only {count} of the {len(wanted)} named files are in "
                f"it. Check the spelling of {' and '.join(repr(one) for one in wanted)}.",
            )
            return None
        gigabytes = round(size / 1_000_000_000, 2)
        _say(
            lines, "good",
            f"'{repo}' has all {count} named files, {gigabytes:g} GB in total. The header "
            f"itself can only be read once the file is here, so the shape is checked on the "
            f"first run.",
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
    lines: list[dict] = []
    size: float | None = None

    if entry.get("format") == catalog.FORMAT_GGUF:
        model = _local_copy(str(entry.get("repo") or ""), str(entry.get("file") or ""))
        projector = _local_copy(str(entry.get("repo") or ""), str(entry.get("mmproj") or ""))
        if model:
            _check_local_gguf(section, entry, model, projector, lines)
        else:
            size = _check_remote(section, entry, lines)
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
            size = _check_remote(section, entry, lines)

    if not lines:
        _say(lines, "warn", "Nothing could be checked from here.")
    return {"verdict": _worst(lines), "lines": lines, "download_gb": size}


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
    seeded = seed_names_of(section)
    for entry in entries:
        entry["seeded"] = entry.get("name") in seeded
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
        "found": [one for one in choices(section) if one.startswith(SCANNED_PREFIXES)],
        "restorable": catalog.restorable(section),
    }


def seed_names_of(section: str) -> set[str]:
    """Names the packaged list carries here, so the window can say which are the pack's."""
    return catalog.seed_names(section)


def gguf_problem(section: str, path: str) -> str:
    """Why this GGUF cannot serve in this list, or an empty string when it can."""
    found = spec(section)
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
