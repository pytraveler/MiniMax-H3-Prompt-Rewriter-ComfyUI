"""Full LightX2V Qwen checkpoints and their GGUF conversions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import re

from . import catalog, discovery, model_sections, paths

log = logging.getLogger(__name__)
SECTION = "qwen_rewriters"
NODE = "MiniMaxH3QwenRewriter"
LOCAL = model_sections.LOCAL_PREFIX


def family(*names):
    """The fine-tuning task must be explicit in a model's name or source."""
    found = set(re.findall(r"(?i)(?<![a-z])(Ref2VA|FL2VA)(?![a-z])", " ".join(names)))
    found = {one.lower() for one in found}
    if len(found) != 1:
        raise ValueError("Name this checkpoint Ref2VA or FL2VA in the model list; its fine-tuning task is ambiguous.")
    return "Ref2VA" if found == {"ref2va"} else "FL2VA"


@dataclass(frozen=True)
class Choice:
    reference: str
    fmt: str
    family: str
    file: str = ""
    mmproj: str = ""
    local: bool = False
    copy_of: str = ""


def _build_model_map():
    mapping, held = {}, []
    for entry in catalog._entries(catalog._data(), SECTION):
        try:
            task = family(entry.name, entry.repo, entry.file)
        except ValueError:
            log.warning("[minimax_h3_rewriter.qwen] %s: name the FL2VA or Ref2VA checkpoint explicitly", entry.name)
            continue
        if entry.is_gguf and not entry.mmproj:
            continue
        mapping[entry.label] = Choice(entry.repo, entry.fmt, task, entry.file, entry.mmproj)
        held.append((entry.label, model_sections.on_disk(SECTION, entry.fmt, entry.repo, entry.file, entry.mmproj)))
    try:
        for label, model, projector in discovery.scan_captioner_gguf(arch="qwen35"):
            try:
                task = family(os.path.basename(model))
            except ValueError:
                continue
            mapping[LOCAL + label] = Choice(model, "gguf", task, mmproj=projector, local=True,
                                            copy_of=model_sections.copy_of(held, model, projector))
        for root in paths.llm_roots():
            if not os.path.isdir(root):
                continue
            for entry in os.scandir(root):
                if not entry.is_dir():
                    continue
                try:
                    task = family(entry.name)
                except ValueError:
                    continue
                if paths.base_model_is_complete(entry.path) and not native_problem(entry.path):
                    mapping[LOCAL + entry.name] = Choice(entry.path, "transformers", task, local=True,
                                                         copy_of=model_sections.copy_of(held, entry.path))
    except (OSError, ImportError):
        log.info("[minimax_h3_rewriter.qwen] local model scan unavailable")
    return mapping


def model_choices():
    choices = [label for label, choice in _build_model_map().items() if not choice.copy_of]
    return (["!! " + catalog.problem()] if catalog.problem() else []) + (choices or ["Add a Qwen Ref2VA or FL2VA model in Model list"])


def resolve(label):
    mapping = _build_model_map()
    current = model_sections.label_now(mapping, SECTION, label)
    if current:
        return mapping[current]
    raise ValueError("This Qwen rewriter is not in the model list. Choose an offered model or add its files through Model list.")


def native_problem(directory):
    return config_problem(discovery.read_local_config(directory) or {})


def config_problem(config):
    if not isinstance(config, dict):
        return "The checkpoint has no readable config.json."
    text = config.get("text_config", {})
    if (config.get("model_type") != "qwen3_5" or not config.get("vision_config")
            or (text.get("hidden_size"), text.get("num_hidden_layers")) not in ((4096, 32), (5120, 64))):
        return "Use a full Qwen3.5 9B or Qwen3.8 27B multimodal rewriter, including its vision tower and language-model head."
    return ""


def check_native(entry):
    """The model-list check uses the full checkpoint, never a LoRA base shape."""
    reference = str(entry.get("repo") or "")
    directory = paths.present_folder(reference)
    config = discovery.read_local_config(directory) if directory else discovery.fetch_remote_config(reference)
    trouble = config_problem(config)
    return {"verdict": "bad" if trouble else "good", "download_gb": None,
            "lines": [{"level": "bad" if trouble else "good", "text": trouble or
                       "Full multimodal Qwen checkpoint: supported 9B/27B shape, vision configuration present. No LoRA is needed."}]}


def gguf_problem(filename):
    header = discovery.gguf_header(filename)
    if header.get("arch") != "qwen35" or (header.get("width"), header.get("blocks")) not in ((4096, 32), (5120, 64)):
        return "Use the qwen35 GGUF conversion of a full 9B or 27B Ref2VA/FL2VA checkpoint."
    return ""


def ensure(choice, settings, progress):
    from dataclasses import asdict
    from .nodes import BASE_SPEC, _ensure_pair, _ensure_present

    options = asdict(settings)
    if choice.fmt == "gguf":
        if choice.local:
            pair = choice.reference, choice.mmproj
        else:
            pair = _ensure_pair(choice.reference, choice.file, choice.mmproj, "Qwen rewriter", options, progress)
        problem = gguf_problem(pair[0])
        if problem:
            raise ValueError(problem)
        if not pair[1] or not discovery.gguf_header(pair[1]).get("vision"):
            raise ValueError("The Qwen GGUF rewriter requires the vision mmproj from the same checkpoint conversion.")
        return pair
    spec = dict(BASE_SPEC, default_repo=choice.reference, label="Full Qwen rewriter")
    directory = _ensure_present(choice.reference, spec, options, progress)
    problem = native_problem(directory)
    if problem:
        raise ValueError(problem)
    return directory, ""
