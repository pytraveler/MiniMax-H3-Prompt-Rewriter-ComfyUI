"""Full-checkpoint generation through Transformers or an owned llama-server."""

from __future__ import annotations

import atexit
import base64
import io
import json
import threading
import time
import urllib.error
import urllib.request

from . import engine, llama_flags, llamacpp, resident, runner, server_engine
from .constants import normalize_seed
from .qwen_prompt import split_reasoning

_LOCK = threading.RLock()
_SERVER = None
_KEY = None


def holding():
    return str(_KEY[0]) if _KEY and _SERVER is not None else ""


def unload():
    global _SERVER, _KEY
    with _LOCK:
        server, _SERVER, _KEY = _SERVER, None, None
        if server is not None:
            server.close()


atexit.register(unload)


def check_support(choice, settings):
    """An older SFT loader must fail before downloading a full checkpoint."""
    if choice.fmt != "transformers":
        return
    import importlib.util
    from transformers.models.auto.configuration_auto import CONFIG_MAPPING_NAMES
    from .constants import install_command

    if "qwen3_5" not in CONFIG_MAPPING_NAMES:
        raise RuntimeError("This SFT checkpoint needs Transformers with qwen3_5 support (tested with 5.8.0). "
                           "Update the SFT loader, restart ComfyUI, and run again:\n" + install_command("transformers>=5.8.0"))
    if settings.quantization in ("nf4", "int8") and importlib.util.find_spec("bitsandbytes") is None:
        raise RuntimeError("SFT nf4/int8 requires bitsandbytes. Install it and restart ComfyUI:\n" + install_command("bitsandbytes>=0.43"))


def _native(directory, messages, images, settings, seed, greedy, keep_loaded, progress):
    from PIL import Image
    from transformers import set_seed

    with engine._LOCK:
        tokenizer, model = engine.load(directory, None, settings.quantization, settings.attn_implementation,
                                       settings.device, progress)
        try:
            processor = engine.processor()
            if processor is None:
                raise RuntimeError("The full Qwen checkpoint requires its original multimodal processor files.")
            rendered = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                                     enable_thinking=settings.thinking)
            pictures = []
            for one in images:
                with Image.open(io.BytesIO(one.jpeg)) as image:
                    pictures.append(image.convert("RGB"))
            inputs = processor(text=[rendered], images=pictures or None, return_tensors="pt")
            prompt_tokens = int(inputs["input_ids"].shape[1])
            if prompt_tokens + settings.max_new_tokens > settings.context_size:
                raise ValueError(f"Qwen input uses {prompt_tokens} tokens; with {settings.max_new_tokens} output tokens it exceeds context_size={settings.context_size}. Reduce frames/image size or increase context_size.")
            set_seed(normalize_seed(seed))
            raw = engine._run(model, engine._to_device(inputs, model), tokenizer, greedy,
                              settings.max_new_tokens, settings.temperature, settings.top_p, settings.top_k,
                              settings.repetition_penalty, progress)
            text, reasoning = split_reasoning(raw, thinking=settings.thinking)
            usage = {"prompt_tokens": prompt_tokens, "completion_tokens_reencoded": len(tokenizer.encode(raw, add_special_tokens=False))}
            usage["runtime"] = {"backend": "transformers", "model_class": type(model).__name__,
                                "attention": getattr(model.config, "_attn_implementation", None),
                                "devices": sorted({str(one.device) for one in model.parameters()})}
            return text, reasoning, usage
        except BaseException:
            keep_loaded = False
            raise
        finally:
            del tokenizer, model
            if not keep_loaded:
                engine.unload()


def server_messages(messages, images):
    """Replace each ordered image marker, without appending all pictures at the end."""
    iterator = iter(images)
    converted = []
    for message in messages:
        content = message["content"]
        if isinstance(content, list):
            content = [({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(next(iterator).jpeg).decode("ascii")}}
                if part["type"] == "image" else dict(part)) for part in content]
        converted.append({"role": message["role"], "content": content})
    if next(iterator, None) is not None:
        raise ValueError("Internal Qwen reference/image order mismatch.")
    return converted


def _ask(server, messages, images, settings, seed, greedy, progress):
    body = {"messages": server_messages(messages, images), "stream": True,
            "stream_options": {"include_usage": True}, "max_tokens": settings.max_new_tokens,
            "seed": normalize_seed(seed), "temperature": 0.0 if greedy else settings.temperature,
            "top_p": settings.top_p, "top_k": settings.top_k, "repeat_penalty": settings.repetition_penalty,
            "chat_template_kwargs": {"enable_thinking": settings.thinking}}
    request = urllib.request.Request(server.base + "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
    pieces, thoughts, usage = [], [], {}
    finished = threading.Event()

    def watch():
        while not finished.wait(0.1):
            if runner.interrupted():
                server.close()
                return

    watcher = threading.Thread(target=watch, name="h3-qwen-cancel", daemon=True)
    watcher.start()
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            for raw in response:
                llama_flags.check_interrupt()
                if not raw.startswith(b"data:"):
                    continue
                raw = raw[5:].strip()
                if raw == b"[DONE]":
                    break
                event = json.loads(raw)
                usage.update(event.get("usage") or {})
                for choice in event.get("choices", []):
                    if choice.get("finish_reason"):
                        usage["finish_reason"] = choice["finish_reason"]
                    delta = choice.get("delta", {})
                    pieces.append(delta.get("content") or "")
                    thoughts.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
                    progress.text(("".join(pieces) or "".join(thoughts))[-1800:])
    except (OSError, urllib.error.URLError):
        llama_flags.check_interrupt()
        raise RuntimeError("The owned Qwen llama-server stopped answering.\n" + server.tail()) from None
    finally:
        finished.set()
        watcher.join(timeout=2)
    text, embedded = split_reasoning("".join(pieces))
    return text, "".join(thoughts) or embedded, usage


def _gguf(model_path, projector, messages, images, settings, seed, greedy, keep_loaded, progress):
    global _SERVER, _KEY
    binary = llamacpp.ensure_mtmd(settings.llama_backend, settings.auto_download, progress)
    binary = llamacpp.server_beside(binary)
    if not binary:
        raise RuntimeError("GGUF Qwen rewriting needs llama-server beside llama-mtmd-cli. Set MINIMAX_H3_LLAMA_BIN to a complete current llama.cpp build.")
    key = (model_path, projector, binary, settings.device, settings.gpu_layers, settings.context_size)
    with _LOCK:
        if key != _KEY or _SERVER is None or not _SERVER._healthy():
            unload()
            engine.unload()
            runner.free_comfy_vram(settings.device)
            port = server_engine.free_port()
            command = server_engine.build_command(binary, model_path, projector, port, settings.gpu_layers,
                                                  settings.context_size, settings.device)
            _SERVER = server_engine.Server(binary, command, port, settings.context_size)
            _KEY = key
            try:
                _SERVER.start(on_wait=lambda seconds: progress.text(f"Loading Qwen GGUF ({seconds:.0f}s)", force=True))
            except BaseException:
                unload()
                raise
        try:
            text, reasoning, usage = _ask(_SERVER, messages, images, settings, seed, greedy, progress)
            usage["runtime"] = {"backend": "llama-server", "binary": binary,
                                "context_size": settings.context_size, "gpu_layers_requested": settings.gpu_layers}
            return text, reasoning, usage
        except BaseException:
            keep_loaded = False
            raise
        finally:
            if not keep_loaded:
                unload()


def generate(choice, files, messages, images, settings, seed, greedy, keep_loaded, progress):
    check_support(choice, settings)
    with resident.in_use():
        if choice.fmt == "gguf":
            return _gguf(*files, messages, images, settings, seed, greedy, keep_loaded, progress)
        unload()
        return _native(files[0], messages, images, settings, seed, greedy, keep_loaded, progress)
