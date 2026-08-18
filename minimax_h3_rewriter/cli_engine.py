"""Running the rewriter through the ``llama-cli`` binary, in a subprocess.

The backend of last resort, and a surprisingly good one. It needs nothing
installed into ComfyUI's Python: see ``llamacpp.py`` for why the wheel is worth
avoiding when it is not already there.

A subprocess reloads the model on every run, which sounds expensive and is not.
The node's own default is ``keep_model_loaded = False``, because the card is
needed for video generation the moment the rewrite finishes -- and in that mode
the in-process backend already unloads after every run. Reloading a 15.7 GB
Q4_K_M from the page cache takes about 8 seconds, which is what the in-process
backend spends too. What this backend genuinely cannot do is honour
``keep_model_loaded = True``; that is reported rather than silently ignored.

Two things come free with the process boundary: VRAM is returned by the
operating system rather than by hoping a deallocator ran, and a llama.cpp crash
takes down a child process instead of ComfyUI and its queue.

The prompt travels through ``--file`` rather than the command line, so a
multi-line template full of quotes needs no shell escaping on any platform.
"""

from __future__ import annotations

import logging
import os
import tempfile

from . import chat_template, devices, llamacpp, runner
from .constants import normalize_seed
from .progress import NodeProgress

log = logging.getLogger(__name__)

PREVIEW_TAIL = 280

ALL_LAYERS = 999

CHARS_PER_TOKEN = 4.0

_METADATA_CACHE: dict[tuple[str, int, int], dict] = {}


def available() -> bool:
    return llamacpp.available()


TEMPLATE_KEYS = (chat_template.TEMPLATE_KEY, "chat_template")


def gguf_metadata(model_path: str) -> dict:
    """The chat template out of a GGUF header, read straight from the file.

    Only the header is touched, and the answer is cached per file identity, so
    this costs a stat on every run after the first even for a 15.7 GB model.
    """
    try:
        stat = os.stat(model_path)
    except OSError as error:
        raise RuntimeError(f"Cannot read '{model_path}': {error}") from error

    key = (os.path.normcase(model_path), stat.st_size, int(stat.st_mtime))
    cached = _METADATA_CACHE.get(key)
    if cached is not None:
        return cached

    from . import gguf_meta

    metadata = gguf_meta.keys(model_path, TEMPLATE_KEYS)
    _METADATA_CACHE[key] = metadata
    return metadata


def render_prompt(model_path: str, messages: list[dict[str, str]]) -> str:
    return chat_template.from_metadata(gguf_metadata(model_path), messages, enable_thinking=False)


def build_command(
    binary: str,
    model_path: str,
    adapter_path: str | None,
    prompt_file: str,
    gpu_layers: int,
    n_ctx: int,
    seed: int,
    greedy: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    repetition_penalty: float,
    device: str = devices.AUTO,
) -> list[str]:
    layers = devices.layers_for(device, gpu_layers)
    layers = ALL_LAYERS if layers < 0 else layers
    command = [
        binary,
        "--model", model_path,
        "--file", prompt_file,
        *devices.llama_arguments(device),
        "--n-gpu-layers", str(layers),
        "--ctx-size", str(int(n_ctx)),
        "--predict", str(int(max_new_tokens)),
        "--seed", str(normalize_seed(seed)),
        "--repeat-penalty", f"{float(repetition_penalty):g}",
        "-no-cnv",
        "-st",
        "--no-display-prompt",
        "--no-warmup",
        "--simple-io",
    ]
    if adapter_path:
        command += ["--lora", adapter_path]
    if greedy:
        command += ["--temp", "0"]
    else:
        command += [
            "--temp", f"{float(temperature):g}",
            "--top-p", f"{float(top_p):g}",
            "--top-k", str(int(top_k)),
        ]
    return command


def generate(
    binary: str,
    model_path: str,
    adapter_path: str | None,
    messages: list[dict[str, str]],
    gpu_layers: int,
    n_ctx: int,
    seed: int,
    greedy: bool,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    repetition_penalty: float,
    device: str = devices.AUTO,
    progress: NodeProgress | None = None,
) -> str:
    device = devices.validate(device)
    rendered = render_prompt(model_path, messages)

    handle, prompt_file = tempfile.mkstemp(prefix="minimax_h3_", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
        file.write(rendered)

    command = build_command(
        binary, model_path, adapter_path, prompt_file, gpu_layers, n_ctx, seed,
        greedy, max_new_tokens, temperature, top_p, top_k, repetition_penalty, device,
    )

    runner.free_comfy_vram(device)
    if progress is not None:
        name = os.path.basename(model_path)
        note = f" + {os.path.basename(adapter_path)}" if adapter_path else " (no adapter)"
        where = "" if device == devices.AUTO else f" on {device}"
        progress.set_total(max(int(max_new_tokens), 1))
        progress.text(
            f"Loading {name}{note}\nllama.cpp binary{where}, {gpu_layers} GPU layers", force=True
        )

    def report(whole: str) -> None:
        if progress is None:
            return
        progress.update(
            min(len(whole) / CHARS_PER_TOKEN, float(max_new_tokens)),
            f"Generating · {len(whole)} chars\n{whole[-PREVIEW_TAIL:]}",
        )

    try:
        text, stderr_text = runner.run(command, binary, report)
    except runner.ChildFailed as error:
        raise RuntimeError(str(error)) from error
    finally:
        try:
            os.unlink(prompt_file)
        except OSError:
            log.debug("[minimax_h3_rewriter.cli.generate] could not remove %s", prompt_file)

    if progress is not None:
        progress.finish(f"Done · {len(text)} chars{runner.speed(stderr_text)}")
    return text


def unload() -> None:
    """Nothing to unload: the model left with the process that held it."""


def is_loaded() -> bool:
    return False


def rewrite(
    model_path: str,
    adapter_path: str | None,
    gpu_layers: int,
    n_ctx: int,
    keep_loaded: bool,
    backend: str = "auto",
    auto_download: bool = True,
    progress: NodeProgress | None = None,
    **generation,
) -> str:
    """Fetch the runtime if needed, generate once, and let the process go."""
    binary = llamacpp.ensure(backend, auto_download, progress)
    if keep_loaded:
        log.info(
            "[minimax_h3_rewriter.cli.rewrite] keep_model_loaded has no effect on the "
            "llama.cpp binary backend: the model leaves with the subprocess"
        )
    return generate(
        binary=binary,
        model_path=model_path,
        adapter_path=adapter_path,
        progress=progress,
        gpu_layers=gpu_layers,
        n_ctx=n_ctx,
        **generation,
    )
