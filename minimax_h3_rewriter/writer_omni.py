"""The Omni rewriter: the one that hears as well as sees.

lightx2v's third prompt-rewriter LoRA is trained on Qwen2.5-Omni-7B, and two
things follow from that base which neither of the others can do.

**It takes the asset itself, sound included.** The 27B has to be told what a
reference contains, in a caption somebody wrote; the 8B is shown pictures. This
one is shown pictures, clips *and* sounds, so a voice timbre or a piece of
ambience reaches the rewrite as the recording rather than as a sentence about
the recording.

**It covers Ref2AV.** The full-reference mode is six output fields instead of
three, with a retention analysis that judges how faithfully each reference is
reused. Until now that mode existed only in the guided writers, where the format
is carried by a prompt rather than by training.

So the node takes an ordered set of references rather than two named frame
slots. Order is not decoration: labels are numbered within their own kind and in
supply order, so the second picture connected is ``<Picture 2>`` and the model is
told to align it with the end of the video on FL2AV.

**Three engines, and two questions pick between them,** exactly as for the 8B.
A folder of safetensors runs in this process through Transformers and PEFT --
though only with pictures, because ``engine`` has no audio path. A GGUF base
then asks the task: T2AV carries no references and goes the ordinary text way,
and everything else goes through ``llama-mtmd-cli`` in a subprocess, which is
also what can hear.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from comfy_api.latest import io

from . import (
    aspect,
    catalog,
    checks,
    discovery,
    engine,
    library,
    media,
    memory,
    mtmd_engine,
    snapshot,
)
from .catalog import FORMAT_GGUF, FORMAT_TRANSFORMERS
from .constants import (
    MERGE_AUTO,
    OUTPUT_FIELDS,
    QUANTIZATIONS,
    REF_OUTPUT_FIELDS,
    RESOLUTIONS,
    duration_options,
    duration_tooltip,
)
from .fields import split_sections
from .nodes import (
    BASE_SPEC,
    BYPASS_TOOLTIP,
    CATEGORY,
    DEFAULT_OPTIONS,
    LOCAL_PREFIX,
    OPTIONS_TYPE,
    _announce,
    _ensure_pair,
    _ensure_present,
    _gguf_text,
    _refuse_problem,
    _fix_once,
    _report,
    _resolve_adapter,
    _verify_base_model,
)
from .progress import NodeProgress, refuse
from .prompt_template_omni import (
    REF_TASK,
    TASKS,
    build_messages,
    expected_pictures,
    normalize_task,
)
from .universal import ALL_FIELDS, kind_of, layout_of

log = logging.getLogger(__name__)

BASE_REPO_OMNI = "Qwen/Qwen2.5-Omni-7B"

BASE_SPEC_OMNI = dict(BASE_SPEC, default_repo=BASE_REPO_OMNI, label="Base model")

MAX_REFERENCES = 12

MEDIA_MARKER = "<__media__>"

IMAGE_MAX_PIXELS = 301056

VIDEO_MAX_PIXELS = 100352

CONTEXT_SLACK = 1024

KIND_NAMES = {"image": "picture", "video": "clip", "audio": "sound"}

FIELDS_FOR_TASK = {name: OUTPUT_FIELDS for name in TASKS}
FIELDS_FOR_TASK[REF_TASK] = REF_OUTPUT_FIELDS

BODY_FIELD = {name: OUTPUT_FIELDS[0] for name in TASKS}
BODY_FIELD[REF_TASK] = "detailed_description"


@dataclass
class BaseChoice:
    """A base model for the Omni adapter, in whichever shape it was found in."""

    reference: str
    fmt: str = FORMAT_TRANSFORMERS
    file: str = ""
    mmproj: str = ""
    local: bool = False


@dataclass(frozen=True)
class Reference:
    """One connected, switched-on reference, in the order it will be labelled."""

    slot: str
    kind: str
    value: object


def render(messages: list[dict], counts: list[int] | None = None) -> tuple[str, str]:
    """Flatten the trained messages into ``(system prompt, user turn)``.

    ``build_messages`` interleaves one typed placeholder per reference, which is
    the shape a Transformers processor takes. llama.cpp wants one string with a
    marker at each of those points and splices the encoded asset in exactly
    there -- which is what keeps ``<Picture 2> - exact final frame at 10.13s:``
    attached to the picture it names.

    ``counts`` is how many markers each placeholder is worth, because a clip is
    not one attachment: it is sampled into frames and each frame is its own
    ``--image``. llama.cpp refuses a turn whose marker count disagrees with the
    number of media arguments, so the two are counted in one place.
    """
    system = ""
    parts: list[str] = []
    seen = 0
    for message in messages:
        content = message.get("content")
        if message.get("role") == "system":
            system = content if isinstance(content, str) else ""
            continue
        if isinstance(content, str):
            parts.append(content)
            continue
        for piece in content or []:
            if piece.get("type") == "text":
                parts.append(str(piece.get("text") or ""))
                continue
            howmany = counts[seen] if counts and seen < len(counts) else 1
            seen += 1
            parts.append("\n".join([MEDIA_MARKER] * max(1, howmany)) + "\n")
    return system, "".join(parts)


def arrange(supplied: dict | None, raw_layout: str) -> tuple[list[Reference], int]:
    """Every connected reference in strip order, plus how many are switched off.

    The same rule the Universal Writer follows, and for the same reason: a slot
    the strip has never seen goes after the ones it knows, in slot order, so an
    untouched node still numbers its labels the obvious way.
    """
    connected = {name: value for name, value in (supplied or {}).items() if value is not None}
    order, off, _roles = layout_of(raw_layout)

    def slot_number(name: str) -> int:
        tail = name.rsplit("_", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    named = [name for name in order if name in connected]
    known = set(named)
    named += sorted((n for n in connected if n not in known), key=slot_number)

    found = [
        Reference(name, kind_of(connected[name]), connected[name])
        for name in named
        if name not in off
    ]
    return found, len(connected) - len(found)


def check_task(task: str, references: list[Reference], node_id=None) -> None:
    """Refuse a task the connected references cannot serve, before anything loads.

    The alternative is a multi-gigabyte download followed by llama.cpp counting
    media markers and refusing, or -- worse on the frame tasks -- a rewrite that
    quietly aligns the wrong picture with the end of the video. Every refusal is
    said as a toast as well, since the console is not where anyone looks first.
    """
    task = normalize_task(task)
    kinds = [reference.kind for reference in references]

    if task == REF_TASK:
        if not references:
            refuse(
                node_id,
                "Ref2AV describes how a target video reuses reference assets, so it needs at "
                "least one. Connect a picture, a clip or a sound -- or pick T2AV, which is the "
                "task written from text alone.",
            )
        counts = {}
        for kind in kinds:
            word = checks.KIND_TAG.get(kind)
            if word:
                counts[word] = counts.get(word, 0) + 1
        too_many = checks.over_capacity(task, counts)
        if too_many:
            listed = "; and ".join(
                f"{count} {word.lower()}(s) where {task} takes {allowed}"
                for word, count, allowed in too_many
            )
            refuse(
                node_id,
                f"There are {listed} switched on. H3 has nowhere to put the extra "
                f"ones, so switch those squares off.",
            )
        return

    wanted = expected_pictures(task)
    pictures = kinds.count("image")
    heard = [KIND_NAMES[kind] for kind in kinds if kind != "image"]

    if heard:
        refuse(
            node_id,
            f"{task.upper()} is written from pictures alone, and {', '.join(heard)} "
            f"{'is' if len(heard) == 1 else 'are'} connected. Switch those squares off, or "
            f"pick Ref2AV, which is the task that takes clips and sound.",
        )
    if pictures != wanted:
        refuse(
            node_id,
            f"{task.upper()} is written from {wanted} picture(s), and {pictures} "
            f"{'is' if pictures == 1 else 'are'} switched on. "
            + (
                "Switch the extra squares off, or pick Ref2AV."
                if pictures > wanted
                else "Connect the missing picture, or switch its square back on."
            ),
        )


_MODEL_MAP: dict[str, BaseChoice] = {}


def _build_model_map() -> dict[str, BaseChoice]:
    """The Omni base models, in both shapes the adapter is published for."""
    mapping: dict[str, BaseChoice] = {}
    try:
        for entry in catalog.models_omni():
            if not entry.is_gguf:
                mapping[entry.label] = BaseChoice(entry.repo, FORMAT_TRANSFORMERS)
                continue
            if not entry.mmproj:
                log.warning(
                    "[minimax_h3_rewriter.writer_omni] '%s' has no 'mmproj', skipping", entry.name
                )
                continue
            mapping[entry.label] = BaseChoice(
                entry.repo, FORMAT_GGUF, entry.file, entry.mmproj
            )
    except Exception:
        log.warning("[minimax_h3_rewriter.writer_omni] catalog unreadable", exc_info=True)

    try:
        for label, model_path, mmproj_path in discovery.scan_captioner_gguf(
            discovery.GGUF_ARCH_OMNI
        ):
            if discovery.gguf_problem_omni(model_path):
                label += " (wrong size for the adapter)"
            elif not discovery.gguf_header(mmproj_path)["audio"]:
                label += " (vision only, not an Omni build)"
            mapping[f"{LOCAL_PREFIX}{label}"] = BaseChoice(
                model_path, FORMAT_GGUF, mmproj=mmproj_path, local=True
            )
    except Exception:
        log.warning("[minimax_h3_rewriter.writer_omni] gguf scan failed", exc_info=True)

    _MODEL_MAP.clear()
    _MODEL_MAP.update(mapping)
    return mapping


def model_choices() -> list[str]:
    choices = list(_build_model_map())
    return _announce(choices or ["(no Qwen2.5-Omni base found -- see the model list)"])


def _resolve_model_choice(choice: str) -> BaseChoice:
    _refuse_problem(choice)
    found = _MODEL_MAP.get(choice) or _build_model_map().get(choice)
    if found is not None:
        return found
    raise RuntimeError(
        f"'{choice}' is not in the base-model list any more. Pick another entry, or add one "
        f"under \"models_omni\" in {catalog.user_file()}."
    )


def _with_transformers(
    choice: BaseChoice,
    references: list[Reference],
    prompt: str,
    task: str,
    resolution: str,
    duration: float,
    quantization: str,
    settings: dict,
    seed: int,
    greedy: bool,
    keep_loaded: bool,
    progress: NodeProgress,
) -> str:
    """The safetensors route: one process, the pictures handed over in memory.

    Pictures only. ``engine`` shows a model images and nothing else, so a clip
    or a sound on this route is refused here rather than silently dropped from
    the turn -- which would leave the model writing about a reference it was
    never given.
    """
    unheard = [KIND_NAMES[r.kind] for r in references if r.kind != "image"]
    if unheard:
        raise RuntimeError(
            f"This base is a folder of safetensors, and that route can show the model "
            f"pictures only -- {', '.join(unheard)} cannot be passed to it here. Pick one of "
            f"the GGUF bases, which run through llama-mtmd-cli and do hear."
        )

    _verify_base_model(choice.reference, progress, discovery.SHAPE_OMNI, BASE_REPO_OMNI)
    base_dir = _ensure_present(
        choice.reference, BASE_SPEC_OMNI, settings["auto_download"], progress
    )
    log.info("[minimax_h3_rewriter.writer_omni] base model: %s", base_dir)

    adapter_dir = None
    if settings["use_lora"]:
        adapter_dir = _resolve_adapter(
            FORMAT_TRANSFORMERS, settings["adapter"], settings["auto_download"], progress,
            catalog.ADAPTERS_OMNI,
        )

    images = []
    for reference in references:
        picture = media.pil_frames(reference.value, 1, IMAGE_MAX_PIXELS)
        if not picture:
            raise ValueError(
                f"'{reference.slot}' is an empty IMAGE batch, so there is no frame to read."
            )
        images.append(picture[0])

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
        images=images or None,
        messages=build_messages(
            prompt, task, resolution, duration, tuple(r.kind for r in references)
        ),
        seed=int(seed),
        greedy=greedy,
        max_new_tokens=int(settings["max_new_tokens"]),
        temperature=float(settings["temperature"]),
        top_p=float(settings["top_p"]),
        top_k=int(settings["top_k"]),
        repetition_penalty=float(settings["repetition_penalty"]),
    )


def _attachments_for(
    references: list[Reference], workspace, max_frames: int
) -> tuple[list[tuple[str, str]], list[int], int]:
    """Write every reference to disk, and say how many markers each is worth.

    A clip is the reason this returns two things. It is sampled into frames and
    each frame goes in as its own ``--image``, so one ``<Video 1>`` label stands
    in front of several media markers and the turn has to say so.
    """
    attachments: list[tuple[str, str]] = []
    counts: list[int] = []
    for reference in references:
        if reference.kind == "audio":
            attachments.append(("audio", media.audio_file(reference.value, workspace)))
            counts.append(1)
        elif reference.kind == "video":
            paths, _total, _seconds = media.video_frames(
                reference.value, workspace, max_frames, VIDEO_MAX_PIXELS
            )
            attachments.extend(("image", path) for path in paths)
            counts.append(len(paths))
        else:
            paths = media.image_files(
                reference.value, workspace, max_frames=1, prefix=reference.slot,
                max_pixels=IMAGE_MAX_PIXELS,
            )
            attachments.append(("image", paths[0]))
            counts.append(1)
    return attachments, counts, _media_tokens(attachments)


def _media_tokens(attachments: list[tuple[str, str]]) -> int:
    """What the written assets will cost the context, read off the files themselves.

    Measured rather than assumed. The ceilings in ``media`` bound this, but a
    picture below them is charged what it is, and a sound is charged by its
    length -- so a turn is only ever given the room it needs.
    """
    from PIL import Image

    total = 0
    for kind, path in attachments:
        if kind == "audio":
            total += int(media.wav_seconds(path) * media.AUDIO_TOKENS_PER_SECOND)
            continue
        try:
            with Image.open(path) as picture:
                total += media.token_cost(*picture.size)
        except OSError:
            total += IMAGE_MAX_PIXELS // media.PATCH ** 2
    return total


def _room_for(n_ctx: int, budget: int, model_path: str, progress: NodeProgress) -> int:
    """Widen the context when the turn will not fit, and say so.

    ``n_ctx`` is a setting on the Options node, and 8192 -- its default -- holds
    two pictures and the four-task system prompt with nothing to spare. Ref2AV
    takes up to twelve references, so the honest thing is to ask for what this
    particular turn costs rather than to fail at ``failed to find a memory slot``
    with the pictures already encoded.
    """
    if budget <= n_ctx or n_ctx <= 0:
        return n_ctx

    trained = discovery.gguf_header(model_path).get("context") or 0
    wanted = min(budget, trained) if trained else budget
    if wanted <= n_ctx:
        raise RuntimeError(
            f"This turn needs about {budget} tokens of context and {os.path.basename(model_path)} "
            f"was trained for {trained}. Switch some squares off on the strip, lower "
            f"'max_frames', or use a shorter sound."
        )

    log.info(
        "[minimax_h3_rewriter.writer_omni] raising n_ctx from %d to %d for %d tokens of media "
        "and answer", n_ctx, wanted, budget,
    )
    progress.text(f"Context {wanted} for this turn", force=True)
    return wanted


def _with_media(
    references, model_path, mmproj_path, adapter_path, messages,
    settings, seed, greedy, keep_loaded, max_frames, progress,
) -> str:
    """Run the multimodal path: one asset per marker, in order."""
    if keep_loaded:
        log.info(
            "[minimax_h3_rewriter.writer_omni] keep_model_loaded has no effect on a task with "
            "references: those run through llama-mtmd-cli, and the model leaves with the "
            "subprocess"
        )
    with media.Workspace() as workspace:
        attachments, counts, media_tokens = _attachments_for(references, workspace, max_frames)
        system, user = render(messages, counts)
        budget = (
            media_tokens
            + (len(system) + len(user)) // 3
            + int(settings["max_new_tokens"])
            + CONTEXT_SLACK
        )
        return mtmd_engine.describe(
            model_path=model_path,
            mmproj_path=mmproj_path,
            instruction=user,
            system_prompt=system,
            attachments=attachments,
            adapter_path=adapter_path,
            gpu_layers=int(settings["gpu_layers"]),
            n_ctx=_room_for(int(settings["n_ctx"]), budget, model_path, progress),
            seed=int(seed),
            greedy=greedy,
            max_new_tokens=int(settings["max_new_tokens"]),
            temperature=float(settings["temperature"]),
            top_p=float(settings["top_p"]),
            top_k=int(settings["top_k"]),
            device=settings["device"],
            backend=settings["llama_backend"],
            auto_download=settings["auto_download"],
            progress=progress,
        )


def rewrite_omni(
    model: str,
    prompt: str,
    task: str,
    resolution: str,
    duration: float,
    quantization: str,
    greedy: bool,
    seed: int,
    keep_loaded: bool,
    settings: dict,
    progress: NodeProgress,
    references: list[Reference] | None = None,
    max_frames: int = media.DEFAULT_MAX_FRAMES,
) -> str:
    """Run the Omni adapter and return the rewrite, on whichever engine fits.

    A function rather than a method for the same reason as the other two: the
    universal rewriter's third tab runs exactly this, and the choice between the
    engines is not worth having two copies of.
    """
    wanted = normalize_task(task)
    references = list(references or [])
    check_task(wanted, references, progress.node_id)
    choice = _resolve_model_choice(model)

    if choice.fmt == FORMAT_TRANSFORMERS:
        return _with_transformers(
            choice, references, prompt, wanted, resolution, float(duration),
            quantization, settings, seed, greedy, keep_loaded, progress,
        )

    if choice.local:
        model_path, mmproj_path = choice.reference, choice.mmproj
    else:
        model_path, mmproj_path = _ensure_pair(
            choice.reference, choice.file, choice.mmproj, "Base model",
            settings["auto_download"], progress,
        )
    log.info("[minimax_h3_rewriter.writer_omni] base model: %s", model_path)

    adapter_path = None
    if settings["use_lora"]:
        problem = discovery.gguf_problem_omni(model_path)
        if problem:
            raise RuntimeError(
                "This GGUF cannot run the Omni prompt-rewriter LoRA.\n  - "
                + problem
                + "\nTurn 'use_lora' off to run it as a plain model anyway."
            )
        adapter_path = _resolve_adapter(
            FORMAT_GGUF, settings["adapter"], settings["auto_download"], progress,
            catalog.ADAPTERS_OMNI,
        )

    messages = build_messages(
        prompt, wanted, resolution, float(duration), tuple(r.kind for r in references)
    )

    if references:
        return _with_media(
            references, model_path, mmproj_path, adapter_path, messages,
            settings, seed, greedy, keep_loaded, max_frames, progress,
        )

    system, user = render(messages)
    return _gguf_text(
        settings,
        model_path=model_path,
        adapter_path=adapter_path,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        gpu_layers=int(settings["gpu_layers"]),
        n_ctx=int(settings["n_ctx"]),
        keep_loaded=keep_loaded,
        device=settings["device"],
        progress=progress,
        seed=int(seed),
        greedy=greedy,
        max_new_tokens=int(settings["max_new_tokens"]),
        temperature=float(settings["temperature"]),
        top_p=float(settings["top_p"]),
        top_k=int(settings["top_k"]),
        repetition_penalty=float(settings["repetition_penalty"]),
    )


DESCRIPTION = (
    "Rewrites a short prompt into a MiniMax-H3 audio-video description with lightx2v's third "
    "LoRA, on Qwen2.5-Omni-7B. It is the only one of the three that hears: a reference reaches "
    "it as the picture, the clip or the sound itself, and it covers all five tasks including "
    "Ref2AV, the full-reference mode with six output fields. One socket takes any of the three "
    "kinds; the strip below shows what is connected and in what order, and that order is what "
    "numbers the labels."
)

LAYOUT_TOOLTIP = (
    "The strip's state as JSON -- which squares are switched off and what order they are in. "
    "It is a widget so the arrangement travels with the workflow and through the API; the "
    "interface draws it as squares instead. A slot missing from it is on, in slot order."
)

REFERENCE_TOOLTIP = (
    "One picture, clip or sound per slot; more slots appear as you fill them. What a reference "
    "is called follows from what it is -- pictures are numbered among pictures, sounds among "
    "sounds -- so there is no wrong socket to plug into here."
)

TASK_TOOLTIP = (
    "T2AV: text alone, references ignored. I2AV: one picture, the first frame. L2AV: one "
    "picture, the final frame. FL2AV: two pictures, first and last. Ref2AV: any mix of "
    "pictures, clips and sounds the target video reuses, written with the six-field "
    "full-reference prompt. Only Ref2AV takes clips and sound."
)

DURATION_TOOLTIP = duration_tooltip(
    "Target clip length in seconds. MiniMax-H3 generates on a 17n+5 frame grid at 24 fps, so "
    "the model is told the next length that actually exists -- 10 seconds becomes 243 frames, "
    "10.13 s -- and it is that number the alignment line quotes back. The node does the "
    "snapping; this widget is what you meant."
)

QUANTIZATION_TOOLTIP = (
    "How to load a safetensors base -- nf4 about 9 GB of VRAM, int8 about 12, bfloat16 about "
    "20. Quantizing buys room, not speed: measured on this adapter, int8 generates at about a "
    "third of bfloat16's rate and nf4 at four fifths, because both dequantize on every matmul. "
    "So take the largest the card holds. Ignored by the GGUF route, where llama.cpp's own "
    "quantized kernels are fast."
)

MAX_FRAMES_TOOLTIP = (
    "How many frames to take from a clip, spread evenly. Each frame is its own picture to the "
    "model, so a long clip at full rate would overflow the context and the wall clock alike."
)


class MiniMaxH3PromptWriterOmni(io.ComfyNode):
    """Write the prompt from the assets themselves, for all five tasks."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromptWriterOmni",
            display_name="MiniMax-H3 Prompt Rewriter Omni (sees and hears)",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                io.Custom(OPTIONS_TYPE).Input("options", optional=True),
                io.Autogrow.Input(
                    "references",
                    optional=True,
                    tooltip=REFERENCE_TOOLTIP,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.MultiType.Input(
                            "ref",
                            types=[io.Image, io.Video, io.Audio],
                            tooltip=(
                                "A picture or batch of frames, a clip, or a sound. Its square "
                                "appears in the strip as soon as it is connected."
                            ),
                        ),
                        prefix="ref_",
                        min=0,
                        max=MAX_REFERENCES,
                    ),
                ),
                io.String.Input(
                    "reference_layout",
                    default="{}",
                    tooltip=LAYOUT_TOOLTIP,
                ),
                io.Combo.Input(
                    "task",
                    options=[name.upper() for name in TASKS],
                    default="T2AV",
                    tooltip=TASK_TOOLTIP,
                ),
                io.Combo.Input(
                    "resolution",
                    options=list(RESOLUTIONS),
                    default="16:9",
                    socketless=True,
                    tooltip=aspect.PICKER_TOOLTIP,
                ),
                io.MultiType.Input(
                    io.Float.Input("duration", **duration_options(DURATION_TOOLTIP)),
                    types=[io.Int],
                ),
                io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="The short prompt to rewrite.",
                ),
                io.Combo.Input(
                    "model",
                    options=model_choices(),
                    tooltip=(
                        "The Qwen2.5-Omni base this adapter attaches to. A GGUF entry is a "
                        "model and its projector; entries prefixed 'on disk:' are pairs "
                        "already in your ComfyUI model folders. The safetensors build runs in "
                        "this process and shows the model pictures only."
                    ),
                ),
                io.Combo.Input(
                    "quantization",
                    options=list(QUANTIZATIONS),
                    default="nf4",
                    tooltip=QUANTIZATION_TOOLTIP,
                ),
                io.Boolean.Input(
                    "greedy",
                    default=True,
                    tooltip="Greedy decoding, which is what the adapter was evaluated with.",
                ),
                io.Int.Input(
                    "seed",
                    default=42,
                    min=0,
                    max=0xFFFFFFFF,
                    control_after_generate=True,
                ),
                io.Boolean.Input(
                    "keep_model_loaded",
                    default=False,
                    tooltip=(
                        "Keep the weights resident between runs. It has no effect on a task "
                        "with references: those run in a subprocess that takes the model with "
                        "it when it exits."
                    ),
                ),
                io.Int.Input(
                    "max_frames",
                    default=media.DEFAULT_MAX_FRAMES,
                    min=1,
                    max=64,
                    optional=True,
                    tooltip=MAX_FRAMES_TOOLTIP,
                ),
                io.Boolean.Input(
                    "bypass",
                    default=False,
                    optional=True,
                    tooltip=BYPASS_TOOLTIP,
                ),
                io.MultiType.Input(
                    io.String.Input(
                        "aspect_ratio",
                        optional=True,
                        default="",
                        tooltip=aspect.TOOLTIP,
                    ),
                    types=[io.String, io.Combo],
                ),
                io.Boolean.Input(
                    "repeat_last",
                    default=False,
                    optional=True,
                    tooltip=memory.REPEAT_TOOLTIP,
                ),
                io.String.Input(
                    "library_pick",
                    default="",
                    optional=True,
                    tooltip=library.PICK_TOOLTIP,
                ),
            ],
            outputs=[
                io.String.Output(display_name="rewritten_prompt"),
                *(io.String.Output(display_name=name) for name in ALL_FIELDS),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, library_pick="", repeat_last=False, **kwargs):
        """Whether what this node would hand back without running has changed.

        Neither a record edited in the library window nor an answer edited in
        the node's own memory touches a single input, so without this the
        answer would come back out of ComfyUI's execution cache, still saying
        what it said before the edit.
        """
        return library.stamp(library_pick, repeat_last) + memory.stamp(
            getattr(getattr(cls, "hidden", None), "unique_id", None), repeat_last
        )

    @classmethod
    def execute(
        cls,
        task,
        resolution,
        duration,
        prompt,
        model,
        quantization,
        greedy,
        seed,
        keep_model_loaded,
        references=None,
        reference_layout="{}",
        options=None,
        max_frames=media.DEFAULT_MAX_FRAMES,
        bypass=False,
        aspect_ratio=None,
        repeat_last=False,
        library_pick="",
    ) -> io.NodeOutput:
        given = dict(locals())
        progress = NodeProgress(cls.hidden.unique_id)
        empty = ("",) * len(ALL_FIELDS)

        if bypass:
            progress.finish("bypassed")
            return io.NodeOutput((prompt or "").strip(), *empty)

        connected, switched_off = arrange(references, reference_layout)

        chosen, saved = library.picked(
            library_pick, repeat_last, "MiniMaxH3PromptWriterOmni", 1 + len(ALL_FIELDS),
            cls.hidden.unique_id, having=[item.kind for item in connected],
        )
        if chosen is not None:
            return io.NodeOutput(*chosen)

        kept = memory.repeat(
            cls.hidden.unique_id, "MiniMaxH3PromptWriterOmni", repeat_last and not saved, given
        )
        if kept is not None:
            return io.NodeOutput(*kept)

        resolution = aspect.resolve(aspect_ratio, resolution)

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        wanted = normalize_task(task)
        if switched_off:
            log.info(
                "[minimax_h3_rewriter.writer_omni] %d reference(s) switched off on the strip",
                switched_off,
            )

        text = saved or rewrite_omni(
            model=model,
            prompt=prompt,
            task=wanted,
            resolution=resolution,
            duration=float(duration),
            quantization=quantization,
            greedy=greedy,
            seed=int(seed),
            keep_loaded=keep_model_loaded,
            settings=settings,
            progress=progress,
            references=connected,
            max_frames=int(max_frames),
        )

        names = FIELDS_FOR_TASK[wanted]
        if not saved:
            text = _fix_once(
                text, progress,
                lambda extra: rewrite_omni(
                    model=model, prompt=prompt + extra, task=wanted, resolution=resolution,
                    duration=float(duration), quantization=quantization, greedy=greedy,
                    seed=int(seed), keep_loaded=keep_model_loaded, settings=settings,
                    progress=progress, references=connected, max_frames=int(max_frames),
                ),
                names, task=wanted, duration=duration,
                having=[item.kind for item in connected],
                fallback=BODY_FIELD[wanted], settings=settings,
            )
        _head, sections = split_sections(text, names, fallback=BODY_FIELD[wanted])
        if not saved:
            _report(
                progress, text, sections, names,
                task=wanted, duration=duration,
                having=[item.kind for item in connected],
                settings=settings,
            )
        fields = tuple(sections.get(name, "") for name in ALL_FIELDS)
        outputs = (text,) + fields
        if saved:
            return io.NodeOutput(*outputs)
        memory.keep(
            cls.hidden.unique_id, "MiniMaxH3PromptWriterOmni", outputs, given,
            references=snapshot.take(
                (item.slot, item.kind, item.value) for item in connected
            ),
            task=wanted,
            fields=ALL_FIELDS,
        )
        return io.NodeOutput(*outputs)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3PromptWriterOmni": MiniMaxH3PromptWriterOmni,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3PromptWriterOmni": "MiniMax-H3 Prompt Rewriter Omni (sees and hears)",
}
