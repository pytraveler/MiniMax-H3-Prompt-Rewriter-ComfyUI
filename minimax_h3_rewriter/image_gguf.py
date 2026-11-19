"""Generate the H3 Image caption with a model from the local GGUF lists."""

from __future__ import annotations

import asyncio

from . import image_prompt as image, qwen_engine
from .api_contract import ContractError
from .api_media import prepare_images_async
from .discovery import check_encoders
from .nodes import DEFAULT_OPTIONS, _ensure_pair, _resolve_captioner_choice, run_messages, valid_writer
from .qwen_options import QwenOptions
from .qwen_prompt import split_reasoning

SELECT_MODEL = "Select a GGUF model"


def _expand_text(text, ratio, model, settings, seed, keep_loaded, progress):
    valid = valid_writer(model)
    if valid is not True:
        raise ContractError(valid)
    messages, field = image.chat_request(text, (), ratio)
    raw = run_messages(model, messages, settings["temperature"] == 0, seed, keep_loaded, settings, progress,
                       label="H3 Image")
    caption, reasoning = split_reasoning(raw)
    return caption, reasoning, field


async def _expand_edit(text, references, ratio, model, settings, thinking, seed, keep_loaded, max_side, progress, cancelled):
    if settings["max_new_tokens"] >= settings["n_ctx"]:
        raise ContractError("Increase n_ctx in Rewriter Options to leave room for the input and the requested output tokens.")
    runtime = QwenOptions(
        max_new_tokens=settings["max_new_tokens"], context_size=settings["n_ctx"],
        temperature=settings["temperature"], top_p=settings["top_p"], top_k=settings["top_k"],
        repetition_penalty=settings["repetition_penalty"], thinking=thinking,
        device=settings["device"], gpu_layers=settings["gpu_layers"],
        llama_backend=settings["llama_backend"], auto_download=settings["auto_download"],
    )
    choice = _resolve_captioner_choice(model)
    if choice.local:
        files = (choice.reference, choice.mmproj)
    else:
        files = await asyncio.to_thread(_ensure_pair, choice.reference, choice.file, choice.mmproj,
                                        "Image Prompt Writer", settings, progress)
    check_encoders(files[1], {"image"})
    copies = await prepare_images_async(references, max_side=max_side, byte_limit=64 << 20, cancelled=cancelled)
    markers = tuple({"type": "image"} for _ in copies)
    messages, field = image.chat_request(text, references, ratio, attachments=markers)
    caption, reasoning, _ = await asyncio.to_thread(
        qwen_engine.generate_gguf, *files, messages, copies, runtime, seed, runtime.temperature == 0, keep_loaded,
        progress, label="H3 Image",
    )
    return caption, reasoning, field


async def expand(text, references, ratio, model, max_length, thinking, seed, keep_loaded, options, max_side,
                 progress, *, cancelled=None):
    if not model or model == SELECT_MODEL:
        raise ContractError("Select a vision GGUF model and its projector for Image Edit."
                            if references else "Select a GGUF writer model for Text-to-Image.")
    settings = {**DEFAULT_OPTIONS, "max_new_tokens": max_length, "auto_download": False}
    if options:
        settings.update(options)
    if references:
        content, reasoning, field = await _expand_edit(text, references, ratio, model, settings, thinking, seed,
                                                       keep_loaded, max_side, progress, cancelled)
    else:
        content, reasoning, field = await asyncio.to_thread(_expand_text, text, ratio, model, settings, seed,
                                                           keep_loaded, progress)
    caption, ratio = image.read_expansion(content, ratio, field, budget="max_length or max_new_tokens")
    return caption, reasoning, ratio, [f"Local model: {model}"]
