"""The 8B rewriter: the one that looks at the reference frames itself.

lightx2v's second prompt-rewriter LoRA is trained on Qwen3-VL-8B-Instruct, and
the vision tower is the whole point of it. Where the 27B has to be *told* what a
reference frame contains -- in a caption somebody wrote, or one the captioner
node produced -- this model is shown the frame and writes the alignment line
from what it sees. It is also a third of the download and half the VRAM.

A node of its own rather than another mode on the existing rewriter. The inputs
are different (IMAGE rather than a block of text), the architecture is
different, and the adapter is different; merging them would produce a node where
most of the widgets are inert for most settings of the first one.

**Three engines, and two questions pick between them.** First the shape of the
base model. A folder of safetensors runs in this process through Transformers
and PEFT, which is what the adapter was published for, and every task stays
resident there because nothing exits between runs.

A GGUF base then asks the second question, which is the task. T2VA has no
pictures in it and takes the ordinary text path -- no projector loaded, no
subprocess, and ``keep_model_loaded`` works. The other three carry frames and go
through ``llama-mtmd-cli``, a fresh process per run that therefore cannot keep
anything resident, and says so rather than ignoring the switch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import aspect, catalog, discovery, engine, media, mtmd_engine
from .catalog import FORMAT_GGUF, FORMAT_TRANSFORMERS
from .constants import (
    MERGE_AUTO,
    DURATION_MAX,
    DURATION_MIN,
    OUTPUT_FIELDS,
    QUANTIZATIONS,
    RESOLUTIONS,
)
from . import library, memory, snapshot
from .fields import split_fields
from .guide_prompt import BASE_MODES
from .nodes import (
    BASE_SPEC,
    BYPASS_TOOLTIP,
    CATEGORY,
    DEFAULT_OPTIONS,
    LOCAL_PREFIX,
    OPTIONS_TYPE,
    _announce,
    _bypassed,
    _ensure_pair,
    _ensure_present,
    _gguf_text,
    _refuse_problem,
    _report,
    _resolve_adapter,
    _verify_base_model,
)
from .progress import NodeProgress, announce
from .prompt_template_8b import build_messages, expected_image_count, normalize_task

log = logging.getLogger(__name__)

BASE_REPO_8B = "Qwen/Qwen3-VL-8B-Instruct"

BASE_SPEC_8B = dict(BASE_SPEC, default_repo=BASE_REPO_8B, label="Base model")

PATCH_8B = 32

IMAGE_MAX_PIXELS_8B = 1024 * 1024


@dataclass
class BaseChoice:
    """A base model for the 8B adapter, in whichever shape it was found in.

    GGUF is two files that have to come from the same conversion; safetensors is
    a folder. Both are in one list because which of them you have is a fact
    about your disk, not about the task -- and the adapter is published in both.
    """

    reference: str
    fmt: str = FORMAT_GGUF
    file: str = ""
    mmproj: str = ""
    local: bool = False


MEDIA_MARKER = "<__media__>"

FRAMES_FOR_TASK = {
    "t2av": (),
    "i2av": ("first_frame",),
    "l2av": ("last_frame",),
    "fl2av": ("first_frame", "last_frame"),
}

FRAME_TOOLTIPS = {
    "first_frame": (
        "The exact first frame, for I2VA and FL2VA. The model looks at it and anchors the "
        "opening shot to what is actually in the picture."
    ),
    "last_frame": (
        "The exact final frame, for L2VA and FL2VA. Connect this one rather than 'first_frame' "
        "for L2VA -- which end of the clip a picture belongs to is what the model is told."
    ),
}


def render(messages: list[dict]) -> tuple[str, str]:
    """Flatten the trained messages into ``(system prompt, user turn)``.

    ``build_messages`` interleaves ``{"type": "image"}`` between pieces of text,
    which is the shape a Transformers processor takes. llama.cpp wants one
    string with its own marker at each of those points, and splices the encoded
    frame in exactly there -- which is what keeps
    ``Picture 1 - exact first frame at 0.00 seconds:`` attached to the picture it
    is naming, instead of the frames being appended at one end.
    """
    system = ""
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "system":
            system = content if isinstance(content, str) else ""
            continue
        if isinstance(content, str):
            parts.append(content)
            continue
        for piece in content or []:
            if piece.get("type") == "image":
                parts.append(MEDIA_MARKER)
            else:
                parts.append(str(piece.get("text") or ""))
    return system, "".join(parts)


def frames_for(task: str, first_frame, last_frame, progress=None) -> list[tuple[str, object]]:
    """The frames this task needs, in order, or a message saying which is missing.

    Checked before anything loads, because the alternative is a five-gigabyte
    download followed by llama.cpp counting media markers and refusing. Given a
    ``progress``, a connected frame the task does not read is also announced as
    a toast rather than only logged.
    """
    connected = {"first_frame": first_frame, "last_frame": last_frame}
    wanted = FRAMES_FOR_TASK[task]

    missing = [name for name in wanted if connected[name] is None]
    if missing:
        raise ValueError(
            f"{task.upper()} needs {len(wanted)} reference frame(s): "
            f"{', '.join(wanted)}. Not connected: {', '.join(missing)}."
        )

    spare = [name for name, value in connected.items() if value is not None and name not in wanted]
    if spare:
        unread = (
            f"task {task.upper()} does not use {' or '.join(spare)}, so it is ignored; "
            f"pick the task that matches the frames you connected"
        )
        log.warning("[minimax_h3_rewriter.writer_8b] %s", unread)
        if progress is not None:
            announce(progress.node_id, [("warn", unread)])

    frames = [(name, connected[name]) for name in wanted]
    expected = expected_image_count(task)
    if len(frames) != expected:
        raise RuntimeError(
            f"internal: {task} wants {expected} image(s), this node prepared {len(frames)}"
        )
    return frames


_MODEL_MAP: dict[str, BaseChoice] = {}


def _build_model_map() -> dict[str, BaseChoice]:
    """The 8B base models, in both shapes the adapter is published for.

    A GGUF base here is two files, exactly as a captioner is and for the same
    reason -- the projector comes out of the same conversion as the model. A
    safetensors base is a folder, and the shape the adapter was actually trained
    in. Either way the architecture filter is the point: any multimodal pair
    will caption, but only a Qwen3-VL of this size can carry this LoRA.
    """
    mapping: dict[str, BaseChoice] = {}
    try:
        for entry in catalog.models_8b():
            if not entry.is_gguf:
                mapping[entry.label] = BaseChoice(
                    reference=entry.repo, fmt=FORMAT_TRANSFORMERS
                )
                continue
            if not entry.mmproj:
                log.warning(
                    "[minimax_h3_rewriter.writer_8b] '%s' has no 'mmproj', skipping", entry.name
                )
                continue
            mapping[entry.label] = BaseChoice(
                reference=entry.repo, file=entry.file, mmproj=entry.mmproj
            )
    except Exception:
        log.warning("[minimax_h3_rewriter.writer_8b] catalog unreadable", exc_info=True)
    try:
        for label, model_path, mmproj_path in discovery.scan_captioner_gguf(
            arch=discovery.GGUF_ARCH_8B
        ):
            mapping[f"{LOCAL_PREFIX}{label}"] = BaseChoice(
                reference=model_path, mmproj=mmproj_path, local=True
            )
    except Exception:
        log.warning("[minimax_h3_rewriter.writer_8b] gguf scan failed", exc_info=True)
    try:
        for label, directory in discovery.scan_local(discovery.SHAPE_8B):
            mapping[f"{LOCAL_PREFIX}{label}"] = BaseChoice(
                reference=directory, fmt=FORMAT_TRANSFORMERS, local=True
            )
    except Exception:
        log.warning("[minimax_h3_rewriter.writer_8b] local scan failed", exc_info=True)

    _MODEL_MAP.clear()
    _MODEL_MAP.update(mapping)
    return mapping


def model_choices() -> list[str]:
    choices = list(_build_model_map())
    return _announce(choices or ["(no Qwen3-VL model found - see the model list)"])


def _resolve_model_choice(choice: str) -> BaseChoice:
    _refuse_problem(choice)
    found = _MODEL_MAP.get(choice)
    if found is None:
        found = _build_model_map().get(choice)
    if found is not None:
        return found
    raise RuntimeError(
        f"'{choice}' is not in the 8B model list any more. Pick another entry, put a "
        f"Qwen3-VL '.gguf' and its 'mmproj' together in one folder under ComfyUI's "
        f"models/LLM, or add it under \"models_8b\" in {catalog.user_file()}."
    )


def _with_transformers(
    choice: BaseChoice,
    frames: list[tuple[str, object]],
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

    This is the shape the adapter was published in, and the messages
    ``build_messages`` returns are already the shape a Transformers processor
    takes -- the GGUF route is the one that has to flatten them. It is also
    the only route where ``keep_model_loaded`` means anything for a task with
    frames: there is no subprocess to take the weights with it when it exits.
    """
    _verify_base_model(choice.reference, progress, discovery.SHAPE_8B, BASE_REPO_8B)
    base_dir = _ensure_present(
        choice.reference, BASE_SPEC_8B, settings["auto_download"], progress
    )
    log.info("[minimax_h3_rewriter.writer_8b] base model: %s", base_dir)

    adapter_dir = None
    if settings["use_lora"]:
        adapter_dir = _resolve_adapter(
            FORMAT_TRANSFORMERS, settings["adapter"], settings["auto_download"], progress,
            catalog.ADAPTERS_8B,
        )

    images = []
    for name, value in frames:
        picture = media.pil_frames(value, 1, IMAGE_MAX_PIXELS_8B, PATCH_8B)
        if not picture:
            raise ValueError(f"'{name}' is an empty IMAGE batch, so there is no frame to read.")
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
        messages=build_messages(prompt, task, resolution, duration),
        seed=int(seed),
        greedy=greedy,
        max_new_tokens=int(settings["max_new_tokens"]),
        temperature=float(settings["temperature"]),
        top_p=float(settings["top_p"]),
        top_k=int(settings["top_k"]),
        repetition_penalty=float(settings["repetition_penalty"]),
    )

def _with_frames(
    frames, model_path, mmproj_path, adapter_path, system, user,
    settings, seed, greedy, keep_loaded, progress,
) -> str:
    """Run the multimodal path: one picture per marker, in order."""
    if keep_loaded:
        log.info(
            "[minimax_h3_rewriter.writer_8b] keep_model_loaded has no effect on a task with "
            "reference frames: those run through llama-mtmd-cli, and the model leaves with "
            "the subprocess"
        )
    with media.Workspace() as workspace:
        attachments = [
            ("image", media.image_files(
                tensor, workspace, max_frames=1, prefix=name,
                max_pixels=IMAGE_MAX_PIXELS_8B, patch=PATCH_8B,
            )[0])
            for name, tensor in frames
        ]
        return mtmd_engine.describe(
            model_path=model_path,
            mmproj_path=mmproj_path,
            instruction=user,
            system_prompt=system,
            attachments=attachments,
            adapter_path=adapter_path,
            gpu_layers=int(settings["gpu_layers"]),
            n_ctx=int(settings["n_ctx"]),
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


def rewrite_8b(
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
    first_frame=None,
    last_frame=None,
) -> str:
    """Run the 8B adapter and return the rewrite, on whichever of the three engines fits.

    A function rather than a method for the same reason as ``rewrite_t2va``: the
    universal rewriter's 8B tab runs exactly this, and the choice between the
    three engines is not something worth having two copies of.
    """
    wanted = normalize_task(task)
    frames = frames_for(wanted, first_frame, last_frame, progress)
    choice = _resolve_model_choice(model)

    if choice.fmt == FORMAT_TRANSFORMERS:
        return _with_transformers(
            choice, frames, prompt, wanted, resolution, duration,
            quantization, settings, seed, greedy, keep_loaded, progress,
        )

    if choice.local:
        model_path, mmproj_path = choice.reference, choice.mmproj
    else:
        model_path, mmproj_path = _ensure_pair(
            choice.reference, choice.file, choice.mmproj, "Base model",
            settings["auto_download"], progress,
        )
    log.info("[minimax_h3_rewriter.writer_8b] base model: %s", model_path)

    adapter_path = None
    if settings["use_lora"]:
        problem = discovery.gguf_problem_8b(model_path)
        if problem:
            raise RuntimeError(
                "This GGUF cannot run the 8B prompt-rewriter LoRA.\n  - "
                + problem
                + "\nTurn 'use_lora' off to run it as a plain model anyway."
            )
        adapter_path = _resolve_adapter(
            FORMAT_GGUF, settings["adapter"], settings["auto_download"], progress,
            catalog.ADAPTERS_8B,
        )

    system, user = render(build_messages(prompt, wanted, resolution, duration))

    if frames:
        return _with_frames(
            frames, model_path, mmproj_path, adapter_path, system, user,
            settings, seed, greedy, keep_loaded, progress,
        )

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


class MiniMaxH3PromptWriter8B:
    """Rewrite a prompt with the 8B LoRA, which reads the reference frames."""

    DESCRIPTION = (
        "Runs the LightX2V MiniMax-H3 Prompt Rewriter LoRA for Qwen3-VL-8B and returns a "
        "structured audio-video description. Unlike the 27B rewriter this one is multimodal: "
        "connect the first and/or last frame and the model looks at them itself, so no caption "
        "of them is needed and it writes the alignment line from what is in the picture. About "
        "9 GB of VRAM at Q4_K_M. The base model comes in two shapes and the node takes either: "
        "GGUF needs nothing installed, because the official llama.cpp binaries are fetched too, "
        "and safetensors is the shape the adapter was published in and keeps the model resident "
        "for every task. Weights are fetched on first use."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "The short prompt to expand into an H3 audio-video description.",
                    },
                ),
                "model": (
                    model_choices(),
                    {
                        "tooltip": (
                            "A Qwen3-VL-8B base. A GGUF entry is two files from one conversion, "
                            "the model and its projector; a safetensors entry is the official "
                            "folder the adapter was trained on. Entries prefixed 'on disk:' are "
                            "already in your ComfyUI model folders; the rest are fetched on "
                            "first use. Only the 8B fits the adapter - another size is refused "
                            "by name and number before anything is downloaded."
                        ),
                    },
                ),
                "task": (
                    list(BASE_MODES),
                    {
                        "default": "T2VA",
                        "tooltip": (
                            "T2VA: text only, no frames. I2VA: 'first_frame' is the first frame. "
                            "FL2VA: both frames. L2VA: 'last_frame' is the final frame. The model "
                            "calls these T2AV, I2AV, FL2AV and L2AV; they are the same four."
                        ),
                    },
                ),
                "resolution": (
                    list(RESOLUTIONS),
                    {"default": "16:9", "socketless": True, "tooltip": aspect.PICKER_TOOLTIP},
                ),
                "duration": (
                    "INT",
                    {
                        "default": 10,
                        "min": DURATION_MIN,
                        "max": DURATION_MAX,
                        "step": 1,
                        "tooltip": "Target clip length in seconds; drives shot count and pacing.",
                    },
                ),
                "quantization": (
                    list(QUANTIZATIONS),
                    {
                        "default": "nf4",
                        "tooltip": (
                            "How to load the safetensors build: nf4 needs about 8 GB of VRAM, "
                            "int8 about 13 GB, bfloat16 about 20 GB. Ignored for GGUF models, "
                            "which carry their own quantization, and for checkpoints that are "
                            "already quantized."
                        ),
                    },
                ),
                "greedy": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Deterministic decoding. Turn off to sample; see the options node.",
                    },
                ),
                "seed": (
                    "INT",
                    {
                        "default": 42,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                    },
                ),
                "keep_model_loaded": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Keep the model in VRAM after the rewrite. A safetensors base "
                            "honours it on every task, being loaded in this process. A GGUF "
                            "base can only honour it on T2VA: the tasks with frames run in a "
                            "subprocess, which takes the model with it when it exits."
                        ),
                    },
                ),
            },
            "optional": {
                "aspect_ratio": (
                    "STRING,COMBO",
                    {"default": "", "widgetType": "STRING", "tooltip": aspect.TOOLTIP},
                ),
                "first_frame": ("IMAGE", {"tooltip": FRAME_TOOLTIPS["first_frame"]}),
                "last_frame": ("IMAGE", {"tooltip": FRAME_TOOLTIPS["last_frame"]}),
                "options": (OPTIONS_TYPE,),
                "bypass": ("BOOLEAN", {"default": False, "tooltip": BYPASS_TOOLTIP}),
                "repeat_last": ("BOOLEAN", {"default": False, "tooltip": memory.REPEAT_TOOLTIP}),
                "library_pick": ("STRING", {"default": "", "tooltip": library.PICK_TOOLTIP}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("rewritten_prompt",) + OUTPUT_FIELDS
    FUNCTION = "rewrite"
    CATEGORY = CATEGORY

    def rewrite(
        self,
        prompt,
        model,
        task,
        resolution,
        duration,
        quantization,
        greedy,
        seed,
        keep_model_loaded,
        first_frame=None,
        last_frame=None,
        aspect_ratio=None,
        options=None,
        bypass=False,
        repeat_last=False,
        library_pick="",
        unique_id=None,
    ):
        given = dict(locals())
        if bypass:
            return _bypassed(unique_id, prompt, OUTPUT_FIELDS)

        chosen, saved = library.picked(
            library_pick, repeat_last, "MiniMaxH3PromptWriter8B",
            1 + len(OUTPUT_FIELDS), unique_id,
            having=["image" for frame in (first_frame, last_frame) if frame is not None],
        )
        if chosen is not None:
            return chosen

        kept = memory.repeat(unique_id, "MiniMaxH3PromptWriter8B", repeat_last and not saved, given)
        if kept is not None:
            return kept

        if not saved and not (prompt or "").strip():
            raise ValueError("prompt must not be empty")

        resolution = aspect.resolve(aspect_ratio, resolution)

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        progress = NodeProgress(unique_id)
        text = saved or rewrite_8b(
            model, prompt, task, resolution, duration, quantization,
            greedy, seed, keep_model_loaded, settings, progress,
            first_frame, last_frame,
        )

        fields = split_fields(text)
        if not saved:
            _report(
                progress, text, fields, OUTPUT_FIELDS,
                task=task, duration=duration,
                having=["image" for frame in (first_frame, last_frame) if frame is not None],
            )
        outputs = (text,) + tuple(fields[name] for name in OUTPUT_FIELDS)
        if saved:
            return outputs
        memory.keep(
            unique_id, "MiniMaxH3PromptWriter8B", outputs, given,
            references=snapshot.take(
                (("first_frame", "image", first_frame), ("last_frame", "image", last_frame))
            ),
        )
        return outputs


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3PromptWriter8B": MiniMaxH3PromptWriter8B,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3PromptWriter8B": "MiniMax-H3 Prompt Rewriter 8B (sees frames)",
}
