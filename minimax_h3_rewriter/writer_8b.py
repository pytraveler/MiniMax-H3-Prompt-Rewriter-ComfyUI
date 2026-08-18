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

**Two engines, and the task picks which.** T2VA has no pictures in it, so it
takes the ordinary text path -- no projector loaded, no subprocess, and
``keep_model_loaded`` works. The other three carry frames and go through
``llama-mtmd-cli``, which is a fresh process per run and therefore cannot keep
anything resident.
"""

from __future__ import annotations

import logging

from . import catalog, discovery, media, mtmd_engine
from .catalog import FORMAT_GGUF
from .constants import (
    DURATION_MAX,
    DURATION_MIN,
    OUTPUT_FIELDS,
    RESOLUTIONS,
)
from .fields import split_fields
from .guide_prompt import BASE_MODES
from .nodes import (
    BYPASS_TOOLTIP,
    CATEGORY,
    DEFAULT_OPTIONS,
    LOCAL_PREFIX,
    OPTIONS_TYPE,
    CaptionerChoice,
    _announce,
    _bypassed,
    _ensure_pair,
    _gguf_text,
    _refuse_problem,
    _resolve_adapter,
)
from .progress import NodeProgress
from .prompt_template_8b import build_messages, expected_image_count, normalize_task

log = logging.getLogger(__name__)

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


def frames_for(task: str, first_frame, last_frame) -> list[tuple[str, object]]:
    """The frames this task needs, in order, or a message saying which is missing.

    Checked before anything loads, because the alternative is a five-gigabyte
    download followed by llama.cpp counting media markers and refusing.
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
        log.warning(
            "[minimax_h3_rewriter.writer_8b] task %s does not use %s, so it is ignored; "
            "pick the task that matches the frames you connected",
            task.upper(), " or ".join(spare),
        )

    frames = [(name, connected[name]) for name in wanted]
    expected = expected_image_count(task)
    if len(frames) != expected:
        raise RuntimeError(
            f"internal: {task} wants {expected} image(s), this node prepared {len(frames)}"
        )
    return frames


_MODEL_MAP: dict[str, CaptionerChoice] = {}


def _build_model_map() -> dict[str, CaptionerChoice]:
    """The 8B base models: a list of its own, and only ``qwen3vl`` from disk.

    A base here is two files, exactly as a captioner is, and for the same
    reason -- the projector comes out of the same conversion as the model. What
    differs is the architecture filter: any multimodal pair will caption, but
    only a Qwen3-VL one can carry this LoRA.
    """
    mapping: dict[str, CaptionerChoice] = {}
    try:
        for entry in catalog.models_8b():
            if not entry.mmproj:
                log.warning(
                    "[minimax_h3_rewriter.writer_8b] '%s' has no 'mmproj', skipping", entry.name
                )
                continue
            mapping[entry.label] = CaptionerChoice(
                reference=entry.repo, file=entry.file, mmproj=entry.mmproj
            )
    except Exception:
        log.warning("[minimax_h3_rewriter.writer_8b] catalog unreadable", exc_info=True)
    try:
        for label, model_path, mmproj_path in discovery.scan_captioner_gguf(
            arch=discovery.GGUF_ARCH_8B
        ):
            mapping[f"{LOCAL_PREFIX}{label}"] = CaptionerChoice(
                reference=model_path, mmproj=mmproj_path, local=True
            )
    except Exception:
        log.warning("[minimax_h3_rewriter.writer_8b] gguf scan failed", exc_info=True)

    _MODEL_MAP.clear()
    _MODEL_MAP.update(mapping)
    return mapping


def model_choices() -> list[str]:
    choices = list(_build_model_map())
    return _announce(choices or ["(no Qwen3-VL model found - see the model list)"])


def _resolve_model_choice(choice: str) -> CaptionerChoice:
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


class MiniMaxH3PromptWriter8B:
    """Rewrite a prompt with the 8B LoRA, which reads the reference frames."""

    DESCRIPTION = (
        "Runs the LightX2V MiniMax-H3 Prompt Rewriter LoRA for Qwen3-VL-8B and returns a "
        "structured audio-video description. Unlike the 27B rewriter this one is multimodal: "
        "connect the first and/or last frame and the model looks at them itself, so no caption "
        "of them is needed and it writes the alignment line from what is in the picture. About "
        "9 GB of VRAM at Q4_K_M. Weights are fetched on first use; nothing has to be installed, "
        "because the official llama.cpp binaries are fetched too."
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
                            "A Qwen3-VL base model and its projector. Entries prefixed 'on disk:' "
                            "are pairs already in your ComfyUI model folders; the rest are "
                            "fetched on first use. Only Qwen3-VL-8B fits the adapter - a "
                            "different size loads and then runs without the rewriter."
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
                    {"default": "16:9", "tooltip": "Target aspect ratio the rewrite is composed for."},
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
                            "Keep the model in VRAM after the rewrite. Only T2VA can honour it: "
                            "the tasks with frames run in a subprocess, which takes the model "
                            "with it when it exits."
                        ),
                    },
                ),
            },
            "optional": {
                "first_frame": ("IMAGE", {"tooltip": FRAME_TOOLTIPS["first_frame"]}),
                "last_frame": ("IMAGE", {"tooltip": FRAME_TOOLTIPS["last_frame"]}),
                "options": (OPTIONS_TYPE,),
                "bypass": ("BOOLEAN", {"default": False, "tooltip": BYPASS_TOOLTIP}),
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
        greedy,
        seed,
        keep_model_loaded,
        first_frame=None,
        last_frame=None,
        options=None,
        bypass=False,
        unique_id=None,
    ):
        if bypass:
            return _bypassed(unique_id, prompt, OUTPUT_FIELDS)

        if not (prompt or "").strip():
            raise ValueError("prompt must not be empty")

        wanted = normalize_task(task)
        frames = frames_for(wanted, first_frame, last_frame)

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        progress = NodeProgress(unique_id)
        choice = _resolve_model_choice(model)
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

        system, user = render(build_messages(prompt, wanted, resolution, int(duration)))

        if frames:
            text = self._with_frames(
                frames, model_path, mmproj_path, adapter_path, system, user,
                settings, seed, greedy, keep_model_loaded, progress,
            )
        else:
            text = _gguf_text(
                settings,
                model_path=model_path,
                adapter_path=adapter_path,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                gpu_layers=int(settings["gpu_layers"]),
                n_ctx=int(settings["n_ctx"]),
                keep_loaded=keep_model_loaded,
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

        fields = split_fields(text)
        progress.text(text[-2000:] if text else "(empty rewrite)", force=True)
        return (text,) + tuple(fields[name] for name in OUTPUT_FIELDS)

    @staticmethod
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
                ("image", media.image_files(tensor, workspace, max_frames=1, prefix=name)[0])
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


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3PromptWriter8B": MiniMaxH3PromptWriter8B,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3PromptWriter8B": "MiniMax-H3 Prompt Rewriter 8B (sees frames)",
}
