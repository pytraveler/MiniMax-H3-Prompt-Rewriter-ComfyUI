"""Loading and running Qwen3.6-27B with the MiniMax-H3 prompt-rewriter LoRA.

The rewriter is a plain Transformers/PEFT model rather than a ComfyUI model
patcher, so it is cached here behind a key and unloaded explicitly. ComfyUI's
own models are evicted before the rewriter is loaded and the rewriter releases
its VRAM after generating unless the caller opts to keep it resident.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import threading

from . import devices
from .constants import normalize_seed
from .progress import NodeProgress

log = logging.getLogger(__name__)

_STATE: dict = {"key": None, "tokenizer": None, "model": None, "processor": None}
_LOCK = threading.RLock()

PREVIEW_TAIL = 280


def _torch():
    import torch

    return torch


def _free_comfy_vram(device: str = devices.AUTO) -> None:
    from . import runner

    runner.free_comfy_vram(device)


def _empty_cache() -> None:
    gc.collect()
    try:
        torch = _torch()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        log.debug("[minimax_h3_rewriter._empty_cache] skipped", exc_info=True)


def _needs_remote_code(directory: str) -> bool:
    """Whether loading this checkpoint would execute Python that ships with it.

    ``auto_map`` in ``config.json`` names modelling code inside the checkpoint,
    which Transformers imports and runs. Detecting it is not permission to run
    it: the answer only decides whether the caller has to have said yes.
    """
    config = os.path.join(directory, "config.json")
    if not os.path.isfile(config):
        return False
    try:
        with open(config, "r", encoding="utf-8") as handle:
            return "auto_map" in json.load(handle)
    except (OSError, ValueError):
        return False


def _prequantized_method(directory: str) -> str:
    """The checkpoint's own quantization scheme, or '' when it carries none."""
    from .discovery import quant_method, read_local_config

    config = read_local_config(directory)
    if not config:
        return ""
    method = quant_method(config)
    return "" if method == "none" else method


def _quantization_config(quantization: str):
    torch = _torch()
    if quantization in ("bfloat16", "float16"):
        return None, getattr(torch, quantization)

    try:
        from transformers import BitsAndBytesConfig
        import bitsandbytes  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            f"quantization='{quantization}' needs bitsandbytes. Install it into the ComfyUI "
            f"Python environment (pip install bitsandbytes) or pick bfloat16/float16. ({error})"
        ) from error

    if quantization == "nf4":
        config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
    elif quantization == "int8":
        config = BitsAndBytesConfig(load_in_8bit=True)
    else:
        raise ValueError(f"unknown quantization '{quantization}'")
    return config, torch.bfloat16


def _inner_class(config: dict):
    """The concrete class for a language model buried inside a wrapper config.

    Qwen2.5-Omni's checkpoint is a thinker, a talker and a vocoder under one
    ``config.json``, and ``AutoModelForImageTextToText`` has no entry for the
    wrapper -- only for the thinker inside it, which is the half the adapter was
    cut for and the only half that writes anything. Asking the auto class for
    the outer type gets "unrecognized configuration class" and no model at all.

    Nothing about that class is hard-coded here: Transformers' own mapping is
    what names it, and the only knowledge added is which way is down. Returns
    ``None`` whenever the outer config is loadable as it stands, which is every
    checkpoint but this family.
    """
    try:
        import transformers
        from transformers.models.auto.modeling_auto import (
            MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING_NAMES as mapping,
        )
    except ImportError:
        return None

    from .discovery import configs

    if not config or config.get("model_type") in mapping:
        return None
    for nested in list(configs(config))[1:]:
        named = mapping.get(nested.get("model_type"))
        candidate = getattr(transformers, named, None) if named else None
        if candidate is not None:
            log.info(
                "[minimax_h3_rewriter._model_class] %s wraps %s, loading it as %s",
                config.get("model_type"), nested.get("model_type"), named,
            )
            return candidate
    return None


def _model_class(directory: str = ""):
    """Pick the auto class the checkpoint's own config can actually be built by.

    A text-only repack declares ``qwen3_5_text``, which has no image-text-to-text
    mapping - asking for the multimodal class there fails outright. The adapter
    only touches language-model weights, so either shape is fine.
    """
    import transformers

    config = {}
    if directory:
        from .discovery import read_local_config

        config = read_local_config(directory) or {}

    inner = _inner_class(config)
    if inner is not None:
        return inner

    text_only = str(config.get("model_type") or "").endswith("_text")
    names = ("AutoModelForCausalLM",) if text_only else (
        "AutoModelForImageTextToText",
        "AutoModelForVision2Seq",
        "AutoModelForCausalLM",
    )
    for name in names:
        candidate = getattr(transformers, name, None)
        if candidate is not None:
            return candidate
    raise RuntimeError("No suitable Transformers auto model class is available.")


def _load_processor(directory: str, remote_code: bool):
    """The checkpoint's own processor, if it has one; ``None`` for text-only.

    A multimodal checkpoint needs one to turn pictures into the tensors the
    model reads, and it is also what renders the chat template with the image
    placeholders in the right places -- the tokenizer alone would drop them.
    Nothing here is fatal: a text-only checkpoint has no processor and does not
    need one, and a checkpoint whose processor cannot be built is simply run
    through the text path.
    """
    from .discovery import read_local_config

    config = read_local_config(directory) or {}
    if str(config.get("model_type") or "").endswith("_text"):
        return None

    try:
        from transformers import AutoProcessor

        return AutoProcessor.from_pretrained(directory, trust_remote_code=remote_code)
    except Exception:
        log.debug("[minimax_h3_rewriter._load_processor] no processor for %s", directory, exc_info=True)
        return None


def _shard_progress_hook(progress: NodeProgress, title: str, scale: float):
    """Route the Transformers weight-loading bar to the node progress bar."""

    def hook(factory, args, kwargs):
        if not hasattr(factory, "update"):
            return factory(*args, **kwargs)

        desc = kwargs.get("desc") or "Loading weights"

        class NodeTqdm(factory):
            def __init__(self, *inner_args, **inner_kwargs):
                inner_kwargs["disable"] = True
                super().__init__(*inner_args, **inner_kwargs)

            def update(self, n=1):
                result = super().update(n)
                total = self.total or 0
                if total:
                    progress.ratio(scale * self.n / total, f"{title}\n{desc}: {self.n}/{total}")
                return result

        return NodeTqdm(*args, **kwargs)

    return hook


def _install_shard_hook(progress: NodeProgress, title: str, scale: float = 0.9):
    try:
        from transformers.utils import logging as hf_logging

        if not hasattr(hf_logging, "set_tqdm_hook"):
            return None, None
        return hf_logging, hf_logging.set_tqdm_hook(_shard_progress_hook(progress, title, scale))
    except Exception:
        log.debug("[minimax_h3_rewriter._install_shard_hook] unavailable", exc_info=True)
        return None, None


def load(
    base_dir: str,
    adapter_dir: str | None,
    quantization: str,
    attn_implementation: str,
    device: str = devices.AUTO,
    progress: NodeProgress | None = None,
    trust_remote_code: bool = False,
):
    """Return ``(tokenizer, model)``, reusing the cached pair when unchanged."""
    device = devices.validate(device)

    remote_code = _needs_remote_code(base_dir)
    if remote_code and not trust_remote_code:
        raise RuntimeError(
            f"'{base_dir}' carries its own model code (its config.json has 'auto_map'), and "
            f"loading it runs that code on this machine with your user's rights.\n\n"
            f"If this is a model you chose and trust, turn 'trust_remote_code' on in the "
            f"options node. Leave it off for anything a workflow picked for you."
        )

    key = (
        os.path.normcase(base_dir),
        os.path.normcase(adapter_dir or ""),
        quantization,
        attn_implementation,
        device,
        remote_code,
    )

    with _LOCK:
        if _STATE["key"] == key and _STATE["model"] is not None:
            return _STATE["tokenizer"], _STATE["model"]

        unload()
        _free_comfy_vram(device)

        from transformers import AutoTokenizer

        if progress is not None:
            progress.set_total(1000)
            progress.ratio(0.0, "Loading tokenizer")

        tokenizer = AutoTokenizer.from_pretrained(base_dir, trust_remote_code=remote_code)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token

        prequantized = _prequantized_method(base_dir)
        if prequantized:
            # Stacking bitsandbytes on top of an AWQ/GPTQ/FP8 checkpoint is not a
            # thing Transformers can do; the checkpoint's own scheme wins.
            if quantization not in ("bfloat16", "float16"):
                log.info(
                    "[minimax_h3_rewriter.load] '%s' is already %s-quantized, ignoring quantization='%s'",
                    base_dir, prequantized, quantization,
                )
            quant_config, dtype = None, _torch().bfloat16
        else:
            quant_config, dtype = _quantization_config(quantization)

        model_kwargs = {
            "dtype": dtype,
            "device_map": devices.device_map(device),
            "attn_implementation": attn_implementation,
        }
        if remote_code:
            model_kwargs["trust_remote_code"] = True
        if quant_config is not None:
            model_kwargs["quantization_config"] = quant_config

        title = f"Loading base model ({prequantized or quantization})"
        if progress is not None:
            progress.ratio(0.02, title)
        hf_logging, previous_hook = (None, None)
        if progress is not None:
            hf_logging, previous_hook = _install_shard_hook(progress, title)

        model_class = _model_class(base_dir)
        try:
            model = model_class.from_pretrained(base_dir, **model_kwargs)
        except (ImportError, ValueError) as error:
            if attn_implementation == "sdpa":
                raise
            log.warning(
                "[minimax_h3_rewriter.load] attn_implementation='%s' unavailable (%s), falling back to sdpa",
                attn_implementation, error,
            )
            model_kwargs["attn_implementation"] = "sdpa"
            model = model_class.from_pretrained(base_dir, **model_kwargs)
        finally:
            if hf_logging is not None:
                hf_logging.set_tqdm_hook(previous_hook)

        if adapter_dir:
            if progress is not None:
                progress.ratio(0.92, "Applying prompt-rewriter LoRA")
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, adapter_dir, is_trainable=False)

        model.eval()
        _STATE.update(
            key=key, tokenizer=tokenizer, model=model,
            processor=_load_processor(base_dir, remote_code),
        )

        if progress is not None:
            progress.ratio(1.0, "Model ready")
        return tokenizer, model


def unload() -> None:
    with _LOCK:
        if _STATE["model"] is None and _STATE["tokenizer"] is None:
            _STATE["key"] = None
            return
        _STATE.update(key=None, tokenizer=None, model=None, processor=None)
    _empty_cache()


def processor():
    """The image/text processor of the loaded checkpoint, or ``None``.

    Cached beside the model rather than fetched per call, because it carries the
    image preprocessing configuration and the chat template, and both are read
    on every generation.
    """
    return _STATE["processor"]


def is_loaded() -> bool:
    return _STATE["model"] is not None


def _input_device(model):
    embeddings = model.get_input_embeddings()
    if embeddings is not None and hasattr(embeddings, "weight"):
        return embeddings.weight.device
    return next(model.parameters()).device


def _apply_template(owner, messages: list[dict]) -> str:
    """Render a chat template, whether or not it takes the thinking switch."""
    try:
        return owner.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
    except TypeError:
        return owner.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def _render_prompt(tokenizer, messages: list[dict[str, str]]) -> str:
    return _apply_template(tokenizer, messages)


def _interrupt_criteria():
    from transformers import StoppingCriteria

    torch = _torch()

    class Interrupted(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs):
            try:
                import comfy.model_management as mm

                flag = mm.processing_interrupted()
            except Exception:
                flag = False
            return torch.full((input_ids.shape[0],), bool(flag), dtype=torch.bool, device=input_ids.device)

    return Interrupted()


def _was_interrupted() -> bool:
    try:
        import comfy.model_management as mm

        return bool(mm.processing_interrupted())
    except Exception:
        return False


def _to_device(inputs, model):
    """Move a tokenizer or processor result onto the model's input device."""
    device = _input_device(model)
    if hasattr(inputs, "to"):
        return inputs.to(device)
    return {name: tensor.to(device) for name, tensor in inputs.items()}


def _run(
    model,
    inputs,
    tokenizer,
    greedy: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    repetition_penalty: float,
    progress: NodeProgress | None = None,
) -> str:
    """Generate once from prepared inputs and stream the answer back.

    Split out from :func:`generate` because the only thing a multimodal run does
    differently is build ``inputs`` -- the sampling, the interrupt handling and
    the streaming are the same, and were not worth having twice.
    """
    from transformers import StoppingCriteriaList, TextIteratorStreamer

    torch = _torch()

    pad_token_id = tokenizer.pad_token_id
    if pad_token_id is None:
        pad_token_id = tokenizer.eos_token_id

    generation_kwargs = {
        "max_new_tokens": max_new_tokens,
        "do_sample": not greedy,
        "repetition_penalty": repetition_penalty,
        "pad_token_id": pad_token_id,
        "eos_token_id": tokenizer.eos_token_id,
    }
    if not greedy:
        generation_kwargs.update(temperature=temperature, top_p=top_p, top_k=top_k)

    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
    call_kwargs = {
        **inputs,
        **generation_kwargs,
        "streamer": streamer,
        "stopping_criteria": StoppingCriteriaList([_interrupt_criteria()]),
    }

    failure: list[BaseException] = []

    def worker():
        try:
            with torch.inference_mode():
                model.generate(**call_kwargs)
        except BaseException as error:  # noqa: BLE001 - surfaced to the caller below
            failure.append(error)
            try:
                streamer.end()
            except Exception:
                log.debug("[minimax_h3_rewriter.generate] streamer.end failed", exc_info=True)

    if progress is not None:
        progress.set_total(max(max_new_tokens, 1))
        progress.update(0, "Generating\n0 tokens")

    thread = threading.Thread(target=worker, name="minimax-h3-rewriter", daemon=True)
    thread.start()

    pieces: list[str] = []
    produced = 0
    for piece in streamer:
        if not piece:
            continue
        pieces.append(piece)
        produced += 1
        if progress is not None:
            tail = "".join(pieces)[-PREVIEW_TAIL:]
            progress.update(produced, f"Generating · {produced}/{max_new_tokens} tokens\n{tail}")

    thread.join()
    if failure:
        raise failure[0]
    if _was_interrupted():
        import comfy.model_management as mm

        raise mm.InterruptProcessingException()

    if progress is not None:
        progress.finish(f"Done · {produced} tokens")
    return "".join(pieces).strip()


def generate(
    tokenizer,
    model,
    messages: list[dict[str, str]],
    seed: int,
    greedy: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    repetition_penalty: float,
    progress: NodeProgress | None = None,
) -> str:
    from transformers import set_seed

    set_seed(normalize_seed(seed))
    rendered = _render_prompt(tokenizer, messages)
    inputs = tokenizer(rendered, return_tensors="pt", add_special_tokens=False)
    return _run(
        model, _to_device(inputs, model), tokenizer,
        greedy, max_new_tokens, temperature, top_p, top_k, repetition_penalty, progress,
    )


def generate_with_images(
    processor,
    model,
    messages: list[dict],
    images: list,
    seed: int,
    greedy: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    repetition_penalty: float,
    progress: NodeProgress | None = None,
) -> str:
    """The same generation, with the reference frames spliced into the turn.

    The processor renders the chat template *and* consumes the pictures, which
    is why both go through it rather than through the tokenizer: the template
    puts an image placeholder exactly where ``build_messages`` put one, so the
    line naming a picture stays attached to the picture it names.
    """
    from transformers import set_seed

    set_seed(normalize_seed(seed))
    try:
        rendered = _apply_template(processor, messages)
    except ValueError:
        rendered = _apply_template(processor.tokenizer, messages)
    inputs = processor(text=[rendered], images=list(images), return_tensors="pt")
    return _run(
        model, _to_device(inputs, model), processor.tokenizer,
        greedy, max_new_tokens, temperature, top_p, top_k, repetition_penalty, progress,
    )


def rewrite(
    base_dir: str,
    adapter_dir: str | None,
    quantization: str,
    attn_implementation: str,
    keep_loaded: bool,
    device: str = devices.AUTO,
    progress: NodeProgress | None = None,
    trust_remote_code: bool = False,
    images: list | None = None,
    **generation,
) -> str:
    """Load (or reuse) the rewriter, generate once, and optionally release VRAM.

    The model reference never escapes this frame, so ``unload`` can actually
    drop the last reference and free the device memory.
    """
    tokenizer, model = load(
        base_dir, adapter_dir, quantization, attn_implementation, device, progress,
        trust_remote_code=trust_remote_code,
    )
    try:
        if images:
            reader = processor()
            if reader is None:
                raise RuntimeError(
                    f"'{base_dir}' has no processor, so it cannot be shown a picture. This is a "
                    f"text-only checkpoint -- pick the full multimodal build, or a task that "
                    f"needs no reference frame."
                )
            return generate_with_images(
                reader, model, images=list(images), progress=progress, **generation
            )
        return generate(tokenizer, model, progress=progress, **generation)
    finally:
        del tokenizer, model
        if not keep_loaded:
            unload()
