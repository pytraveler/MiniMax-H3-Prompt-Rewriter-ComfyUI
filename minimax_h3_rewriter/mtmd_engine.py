"""Describing a reference asset with ``llama-mtmd-cli``.

The writer nodes read text, not pixels. This is where the text comes from: an
image, an audio clip or a video goes to a multimodal model in a subprocess and
comes back as one or two sentences, which then become a line of
``reference_assets``.

It reuses the runtime the rewriter already fetches -- ``llama-mtmd-cli`` ships in
the same release archive as ``llama-completion``, so a machine that has run one
rewrite already has everything this needs.

Two differences from ``cli_engine`` are worth knowing:

- **The chat template is applied by the binary**, not here. ``-p`` is a plain
  instruction and mtmd wraps it, which is also what lets it splice the media
  tokens into the right place in the turn. Rendering the template ourselves, as
  the rewriter does to force ``enable_thinking=False``, would put the image
  markers in the wrong position.
- **The prompt goes on the command line**, not through ``--file``. There is no
  shell in the way -- the command is a list -- so quotes and newlines in an
  instruction need no escaping, and ``-f`` is not honoured alongside media.
- **The context is sized here**, rather than left to the binary. ``--ctx-size 0``
  asks for the context the model was trained on, and llama.cpp reserves the
  whole KV cache before it reads a pixel: on a 256k model that is tens of GB and
  a card that had room for the job. See ``fit_context``.

Which models actually work here is a much shorter list than the set of
multimodal GGUFs on the Hub: the projector format has to be one ``mtmd``
understands. See ``models.json`` for the ones this pack has been run against.

A run with several references to read does not want a process each. ``session``
holds one model open for the whole loop -- see ``server_engine`` -- and hands
``describe`` something to ask instead of something to start. Everything below
behaves identically either way; a machine where the server cannot start keeps
the one-process-per-asset behaviour and loses only the time.
"""

from __future__ import annotations

import contextlib
import logging
import os

from . import checks, devices, discovery, llamacpp, media, runner, server_engine
from .constants import answer_only, normalize_seed
from .progress import NodeProgress

log = logging.getLogger(__name__)

PREVIEW_TAIL = 280
CHARS_PER_TOKEN = 4.0

ALL_LAYERS = 999

DEFAULT_MAX_TOKENS = 256

FLAG_FOR_KIND = {"image": "--image", "audio": "--audio"}

CONTEXT_FROM_MODEL = 0

TOKENS_PER_ATTACHMENT = 4096

CONTEXT_FLOOR = 8192

VRAM_HEADROOM = 2 * 1024 ** 3

OUT_OF_MEMORY = (
    "failed to allocate buffer for kv cache",
    "cudamalloc failed",
    "out of memory",
    "alloc_tensor_range",
    "failed to allocate",
)

CONTEXT_EXCEEDED = (
    "too long",
    "exceed",
    "n_batch",
    "find a memory slot",
)

DEFAULT_SYSTEM = "You are a helpful assistant"


def _size_of(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def fit_context(
    model_path: str,
    mmproj_path: str = "",
    attachments: int = 1,
    device: str = devices.AUTO,
    requested: int = CONTEXT_FROM_MODEL,
) -> int:
    """The ``--ctx-size`` to ask for when the node was left to decide.

    ``--ctx-size 0`` means "the context this model was trained for", which reads
    as the safe answer and is not one. Qwen3-VL was trained for 262144 tokens,
    and its cache is 36 layers of 8 KV heads at 128 dimensions, K and V, in f16:
    144 KiB a token, so 36 GiB of KV cache reserved up front before a single
    pixel has been read. The run dies at ``failed to allocate buffer for kv
    cache`` on a card that would have captioned the picture in three seconds.

    Qwen2.5-Omni never hit it because it asks for 32k, which is the only reason
    the packaged captioners were fine -- and why the failure looked like the
    model's fault rather than this default's.

    So a size asked for by hand is honoured as asked, and 0 now means "what this
    run needs, and never more than the card can hold". A header too thin to size
    against falls back to the old behaviour rather than to a guess: the decision
    is llama.cpp's again, and it says so in its own words.
    """
    if requested > 0:
        return int(requested)

    header = discovery.gguf_header(model_path)
    trained = header.get("context") or 0
    per_token = header.get("kv_per_token") or 0
    if not trained:
        return CONTEXT_FROM_MODEL

    wanted = max(CONTEXT_FLOOR, TOKENS_PER_ATTACHMENT * max(int(attachments), 1))

    room = devices.vram_bytes(device)
    if room and per_token:
        spare = room - _size_of(model_path) - _size_of(mmproj_path) - VRAM_HEADROOM
        affordable = int(spare // per_token) // 1024 * 1024
        wanted = max(CONTEXT_FLOOR, min(wanted, affordable))

    wanted = min(wanted, trained)

    if wanted >= trained:
        return CONTEXT_FROM_MODEL

    cache = f", {wanted * per_token / 1024 ** 3:.1f} GiB of KV cache" if per_token else ""
    log.info(
        "[minimax_h3_rewriter.mtmd_engine.fit_context] %s: context %d rather than the "
        "model's %d%s",
        os.path.basename(model_path), wanted, trained, cache,
    )
    return wanted


def _failure_hint(message: str, n_ctx: int) -> str:
    """What to try next, told apart by what the child actually said.

    The projector note below used to be printed on every non-zero exit, which is
    how a plain out-of-memory came back dressed as a model llama.cpp cannot
    read. llama.cpp is specific about that one; so is this.
    """
    lowered = message.lower()
    if any(marker in lowered for marker in OUT_OF_MEMORY):
        size = f"{n_ctx} tokens" if n_ctx else "the model's own, which can be 256k"
        return (
            f"That is the card running out of room, not a model this cannot read: the "
            f"context asked for was {size}, and llama.cpp reserves the whole KV cache "
            f"before it looks at anything. Lower 'context_size' on the node -- 8192 holds a "
            f"picture or two -- or send the run to another card with 'device'."
        )
    if any(marker in lowered for marker in CONTEXT_EXCEEDED):
        size = f"{n_ctx} tokens" if n_ctx else "the model's own"
        return (
            f"That is the other direction: the pictures did not fit the context, which was "
            f"{size}. Raise 'context_size' -- or 'n_ctx' on the Options node -- or lower "
            f"'max_frames': mtmd charges a frame by its resolution, so a long clip at full "
            f"size is expensive."
        )
    return (
        "Not every multimodal GGUF works here: llama.cpp's mtmd has to understand the "
        "projector format, and several current models abort while loading it. Pick an entry "
        "from the captioner list, which names the ones this pack has been run against."
    )


def build_command(
    binary: str,
    model_path: str,
    mmproj_path: str,
    instruction: str,
    attachments: list[tuple[str, str]],
    gpu_layers: int,
    n_ctx: int = CONTEXT_FROM_MODEL,
    seed: int = 42,
    greedy: bool = True,
    max_new_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.7,
    top_p: float = 0.8,
    top_k: int = 20,
    device: str = devices.AUTO,
    adapter_path: str | None = None,
    system_prompt: str = "",
) -> list[str]:
    layers = devices.layers_for(device, gpu_layers)
    layers = ALL_LAYERS if layers < 0 else layers
    command = [
        binary,
        "--model", model_path,
        "--mmproj", mmproj_path,
        *devices.llama_arguments(device),
        "--n-gpu-layers", str(layers),
        "--ctx-size", str(int(n_ctx)),
        "--predict", str(int(max_new_tokens)),
        "--seed", str(normalize_seed(seed)),
    ]
    if adapter_path:
        command += ["--lora", adapter_path]
    for kind, path in attachments:
        flag = FLAG_FOR_KIND.get(kind)
        if flag is None:
            raise ValueError(f"unknown attachment kind '{kind}'")
        command += [flag, path]
    if greedy:
        command += ["--temp", "0"]
    else:
        command += [
            "--temp", f"{float(temperature):g}",
            "--top-p", f"{float(top_p):g}",
            "--top-k", str(int(top_k)),
        ]
    if system_prompt:
        command += ["--system-prompt", system_prompt]
    # Last, so the instruction is never mistaken for a value of another flag.
    command += ["--prompt", instruction]
    return command


def clip_note(count: int, seconds: float) -> str:
    """Tell the model that these separate images are one clip, in order.

    Without it the frames read as an unrelated set and the answer comes back as
    "several photographs of ..." rather than a description of what happens.
    """
    span = f" spanning {seconds:.1f} seconds" if seconds > 0 else ""
    return (
        f"The {count} images are frames sampled evenly from a single video clip{span}, "
        f"in chronological order. Describe the clip, not the individual frames."
    )


def attachments_from(
    workspace: media.Workspace,
    image=None,
    audio=None,
    video=None,
    max_frames: int = media.DEFAULT_MAX_FRAMES,
) -> tuple[list[tuple[str, str]], list[str], str]:
    """Write the connected inputs to disk. Returns ``(attachments, notes, note)``."""
    attachments: list[tuple[str, str]] = []
    notes: list[str] = []
    note = ""

    if video is not None:
        paths, total, seconds = media.video_frames(video, workspace, max_frames)
        attachments.extend(("image", path) for path in paths)
        notes.append(f"{len(paths)} of {total} video frames" if total > len(paths)
                     else f"video, {len(paths)} frames")
        if len(paths) > 1:
            note = clip_note(len(paths), seconds)

    if image is not None:
        paths = media.image_files(image, workspace, max_frames)
        attachments.extend(("image", path) for path in paths)
        total = int(getattr(image, "shape", [len(paths)])[0])
        notes.append(
            f"{len(paths)} of {total} frames" if total > len(paths) else f"{len(paths)} image(s)"
        )

    if audio is not None:
        attachments.append(("audio", media.audio_file(audio, workspace)))
        notes.append("audio")

    return attachments, notes, note


def busiest(kinds, max_frames: int = media.DEFAULT_MAX_FRAMES) -> int:
    """The most attachments any one description in this batch will carry.

    A server sizes its context once and then serves every request from it, so
    the number that matters is the largest single ask, not the total: eight
    frames of one clip have to fit at once, six separate photographs never do.
    """
    counts = [max(int(max_frames), 1) if kind == "video" else 1 for kind in kinds]
    return max(counts) if counts else 1


@contextlib.contextmanager
def session(
    model_path: str,
    mmproj_path: str,
    assets: int,
    attachments: int = 1,
    gpu_layers: int = -1,
    n_ctx: int = CONTEXT_FROM_MODEL,
    device: str = devices.AUTO,
    backend: str = "auto",
    auto_download: bool = True,
    adapter_path: str | None = None,
    progress: NodeProgress | None = None,
):
    """Hold one model open for ``assets`` descriptions, when that is worth it.

    Yields something to pass to :func:`describe` as ``server``, or ``None``.
    ``None`` is not an error and is not rare -- one asset, a build with no
    server in it, a machine where the port would not bind -- so callers do not
    branch on it: ``describe(..., server=None)`` is exactly what they did
    before this existed.

    The eviction of ComfyUI's own models moves in here for the same reason the
    loading does. Doing it per description would unload the diffusion model six
    times over, and the second through sixth would each find nothing to unload
    and cost a round trip to say so.
    """
    device = devices.validate(device)
    if not model_path or not mmproj_path or not server_engine.wanted(int(assets)):
        yield None
        return

    try:
        captioner = llamacpp.ensure_mtmd(backend, auto_download, progress)
    except Exception:
        log.info(
            "[minimax_h3_rewriter.mtmd_engine.session] no runtime to hold open",
            exc_info=True,
        )
        yield None
        return

    binary = llamacpp.server_beside(captioner)
    if not binary:
        yield None
        return

    if progress is not None:
        progress.text(
            f"Loading {os.path.basename(model_path)} once for {assets} references",
            force=True,
        )
    runner.free_comfy_vram(device)
    server = server_engine.open_server(
        binary,
        model_path,
        mmproj_path,
        gpu_layers,
        fit_context(model_path, mmproj_path, attachments, device, n_ctx),
        device,
        adapter_path,
    )
    try:
        yield server
    finally:
        if server is not None:
            server.close()


def describe(
    model_path: str,
    mmproj_path: str,
    instruction: str,
    image=None,
    audio=None,
    video=None,
    attachments: list[tuple[str, str]] | None = None,
    max_frames: int = media.DEFAULT_MAX_FRAMES,
    gpu_layers: int = -1,
    n_ctx: int = CONTEXT_FROM_MODEL,
    seed: int = 42,
    greedy: bool = True,
    max_new_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.7,
    top_p: float = 0.8,
    top_k: int = 20,
    device: str = devices.AUTO,
    backend: str = "auto",
    auto_download: bool = True,
    adapter_path: str | None = None,
    system_prompt: str | None = None,
    progress: NodeProgress | None = None,
    server=None,
) -> str:
    """Fetch the runtime if needed, describe the attachments once, and return the text.

    ``adapter_path`` is here for the 8B rewriter rather than for captioning.
    That model reads the reference frames itself, so its LoRA has to be attached
    to the same process that holds the images -- there is no separate text pass
    to hang it on. A captioner passes nothing here and nothing changes for it.

    ``attachments`` is for the same caller, and for the same reason. Two
    reference frames are two ordered pictures with a role each, not a batch: the
    model is told which is the first frame and which is the last, and the two
    can be different sizes. So that caller writes its own files and states the
    order, rather than handing over one IMAGE for this to take apart.

    ``server`` is a model :func:`session` already has open. Given one, this
    asks it rather than starting ``llama-mtmd-cli``; given ``None``, which is
    the default and what every caller did before sessions existed, nothing
    about the run changes. The two paths are held to the same sampling, the
    same seed, the same instruction and the same system turn -- see
    ``DEFAULT_SYSTEM``, which is the one of those four that had to be stated
    rather than assumed -- so which one ran is not visible in the answer.
    """
    device = devices.validate(device)
    if system_prompt is None:
        system_prompt = DEFAULT_SYSTEM
    if attachments is None and image is None and audio is None and video is None:
        raise ValueError(
            "nothing to describe: connect an image, an audio clip or a video, or type the "
            "description in by hand."
        )
    if attachments is not None and not attachments:
        raise ValueError("nothing to describe: 'attachments' is empty.")

    binary = server.binary if server is not None else llamacpp.ensure_mtmd(
        backend, auto_download, progress
    )

    with media.Workspace() as workspace:
        note = ""
        if attachments is None:
            attachments, notes, note = attachments_from(
                workspace, image, audio, video, max_frames
            )
        else:
            kinds = dict.fromkeys(kind for kind, _path in attachments)
            notes = [
                f"{sum(kind == wanted for kind, _path in attachments)} {wanted}(s)"
                for wanted in kinds
            ]
        if note:
            instruction = f"{note}\n\n{instruction}"

        command = None
        if server is None:
            n_ctx = fit_context(model_path, mmproj_path, len(attachments), device, n_ctx)
            command = build_command(
                binary, model_path, mmproj_path, instruction, attachments,
                gpu_layers, n_ctx, seed, greedy, max_new_tokens, temperature, top_p,
                top_k, device, adapter_path, system_prompt,
            )
            runner.free_comfy_vram(device)

        if progress is not None:
            where = "" if device == devices.AUTO else f" on {device}"
            lora = f" + {os.path.basename(adapter_path)}" if adapter_path else ""
            progress.set_total(max(int(max_new_tokens), 1))
            progress.text(
                f"Describing {' + '.join(notes)}\n{os.path.basename(model_path)}{lora}{where}",
                force=True,
            )

        def report(whole: str) -> bool:
            """Drive the bar, and say whether this caption has started cycling.

            The same callback goes to both ``server.ask`` and ``runner.run``,
            and both honour the verdict, so a repeating caption is stopped
            whichever of the two is answering.
            """
            if progress is not None:
                progress.update(
                    min(len(whole) / CHARS_PER_TOKEN, float(max_new_tokens)),
                    f"Describing · {len(whole)} chars\n{whole[-PREVIEW_TAIL:]}",
                )
            return bool(checks.looping(whole))

        try:
            if server is not None:
                n_ctx = server.n_ctx
                text, stderr_text = server.ask(
                    instruction, attachments, seed, greedy, max_new_tokens,
                    temperature, top_p, top_k, system_prompt, report,
                ), ""
            else:
                text, stderr_text = runner.run(command, binary, report)
        except runner.ChildFailed as error:
            raise RuntimeError(f"{error}\n\n{_failure_hint(str(error), n_ctx)}") from error

    text = answer_only(text.replace("\r\n", "\n"))

    if progress is not None:
        progress.finish(f"Done · {len(text)} chars{runner.speed(stderr_text)}")
    return text
