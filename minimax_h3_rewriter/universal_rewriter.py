"""All three prompt-rewriter LoRAs in one node, with the tab remembering the rest.

There are three adapters and they are not three settings of one thing. The 27B
is text: Qwen3.6-27B, one task, and a reference frame reaches it only as a
sentence somebody wrote about it. The 8B is multimodal: Qwen3-VL-8B, four tasks,
and the picture itself. The Omni is multimodal and hears as well: Qwen2.5-Omni-7B,
the same four tasks here, and a fifth -- Ref2AV -- that this node does not offer,
for a reason set out below. Different base, different size, different download.

Which is exactly why choosing between them by hand is tedious. The prompt is the
same prompt, the aspect ratio is the same aspect ratio, and the duration is the
same duration -- so trying the other adapter means retyping all of it into a
second node, and then keeping the two in step. What actually differs is two
widgets: which base model, and how to quantize it.

**Ref2VA is here, and it is the reason for the clip and sound sockets.** The
Omni adapter's fifth task takes any mix of pictures, clips and sounds and answers
with six fields instead of three; the four extra outputs are appended after the
three every task fills, never inserted among them, because ComfyUI addresses an
output link by its slot index and inserting would move ``overall_soundscape`` in
every workflow already built on this node.

What it does not have is a strip. Order is the whole labelling rule -- the first
picture is ``<Picture 1>`` -- and with four sockets the order is simply the order
of the sockets. Past four, arranging them by hand is the thing you actually want,
which is what the Prompt Rewriter Omni node's draggable strip is for, and why it
takes twelve where this takes four.

**No captioner here, deliberately.** A description of a frame does reach the 27B
if you fold it into the prompt, and it is not wasted -- but the picture ends up
absorbed into the scene rather than pinned to 0.00 seconds, which is what the
adapter's own roadmap says: T2VA is finished and FL2VA is not. A widget on this
node would have looked like the frame task the 27B cannot do. Describe a frame
with the caption node and paste it into ``prompt`` when that is what you want,
and use the 8B tab when the picture has to *be* a frame.

So the tab carries those two and nothing else. Everything above and below it is
shared, and switching adapters mid-graph costs one click. The task switch is
shared too, in the sense that the 27B tab does not touch it: it shows T2VA lit
with the frame tasks greyed out, because that is the honest picture of a text
model, and the value the other two tabs had is still there when you switch back.

The tab strip, the task switch and the aspect-ratio picker are drawn by
``web/js/universal_rewriter_widgets.js`` on top of ``web/js/mmx_controls.js``,
the same HTML-widget mechanism the Universal Writer uses and for the same
reason: it is the one kind of custom widget both the classic canvas and the
Nodes 2.0 renderer draw. Each takes over an ordinary widget declared here and
keeps its name and its place, so a browser that never loads the script still
shows a dropdown for every one of them and the node still runs.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

from . import aspect, library, memory, snapshot, writer_8b, writer_omni
from .constants import (
    OUTPUT_FIELDS,
    QUANTIZATIONS,
    REF_OUTPUT_FIELDS,
    RESOLUTIONS,
    duration_options,
    duration_tooltip,
)
from .fields import split_fields
from .guide_prompt import ALL_MODES, BASE_MODES, REF_MODE
from .multi_caption import _disabled
from .nodes import (
    BYPASS_TOOLTIP,
    CATEGORY,
    DEFAULT_OPTIONS,
    OPTIONS_TYPE,
    _fix_once,
    _report,
    model_choices,
    rewrite_t2va,
)
from .progress import NodeProgress, announce

log = logging.getLogger(__name__)

LORA_27B = "27B LoRA"
LORA_8B = "8B LoRA"
LORA_OMNI = "Omni LoRA"
LORAS = (LORA_27B, LORA_8B, LORA_OMNI)

TEXT_TASK = "T2VA"

FRAME_SLOTS = ("first_frame", "last_frame")

REFERENCE_SLOTS = FRAME_SLOTS + ("reference_video", "reference_audio")

KIND_OF_SLOT = {
    "first_frame": "image",
    "last_frame": "image",
    "reference_video": "video",
    "reference_audio": "audio",
}

REF_ONLY_FIELDS = tuple(name for name in REF_OUTPUT_FIELDS if name not in OUTPUT_FIELDS)

UNIVERSAL_FIELDS = OUTPUT_FIELDS + REF_ONLY_FIELDS

FIELDS_FOR_TASK = {mode: OUTPUT_FIELDS for mode in BASE_MODES}
FIELDS_FOR_TASK[REF_MODE] = REF_OUTPUT_FIELDS

BODY_FIELD = {mode: OUTPUT_FIELDS[0] for mode in BASE_MODES}
BODY_FIELD[REF_MODE] = "detailed_description"

DESCRIPTION = (
    "Runs any of the three MiniMax-H3 prompt-rewriter LoRAs and returns a structured "
    "audio-video description. The tab at the top picks the adapter: 27B is text-only and "
    "writes T2VA; 8B and Omni are multimodal and look at the frames you connect, so they also "
    "write I2VA, FL2VA and L2VA. Only the base model and its quantization belong to a tab -- "
    "the prompt, the aspect ratio, the duration and the seed are shared, so trying another "
    "adapter is one click rather than a second node. Weights are fetched on first use."
)

FRAME_ON_27B = (
    " Unread on the 27B tab, which takes text alone: describe it with the caption node and "
    "put that in 'prompt' if you want it there."
)

FRAMES_FOR_TASK = writer_8b.FRAMES_FOR_TASK

LORA_TOOLTIP = (
    "Which prompt-rewriter LoRA runs. '27B LoRA' is lightx2v's original on Qwen3.6-27B: text "
    "in, T2VA out, about 16 GB of VRAM at nf4 and the strongest writer of the three. '8B "
    "LoRA' is the multimodal one on Qwen3-VL-8B: it reads the frames itself and writes the "
    "alignment line from what it sees, at about a third of the download. 'Omni LoRA' is the "
    "third, on Qwen2.5-Omni-7B: it reads the frames too, and it is the one that also hears -- "
    "though the sound, the clips and the six-field Ref2AV task are on the Prompt Rewriter "
    "Omni node, not here. Switching keeps everything but the base model and its quantization."
)

TASK_TOOLTIP = (
    "T2VA: text alone, no frames. I2VA: 'first_frame' is the first frame. FL2VA: both frames. "
    "L2VA: 'last_frame' is the final frame. Ref2VA: everything connected is a reference the "
    "target video reuses, written with the six-field full-reference prompt -- Omni tab only, "
    "being the one adapter trained on it. The 27B tab writes T2VA whatever this says, and "
    "leaves it alone, so it is still here when you switch back."
)

DURATION_TOOLTIP = duration_tooltip(
    "Target clip length in seconds; drives shot count and pacing. Both adapters were trained "
    "on clips of a few seconds, so a number far past that is a worse prompt rather than a "
    "longer video -- MiniMax-H3 gets the length from its own settings, not from this line."
)

SWITCH_TOOLTIP = (
    "Which frame rows are switched off, as JSON, written by the checkboxes on the input rows. "
    "It is kept as a widget so the state travels with the workflow and through the API; the "
    "interface hides it. A row missing from the map is on. A switched-off frame counts as "
    "unplugged, which is how you park a picture without dragging the wire off."
)

MODEL_27B_TOOLTIP = (
    "Base model for the 27B adapter. Entries prefixed 'on disk:' are already downloaded; the "
    "rest are fetched on first use. GGUF entries need no extra install: without "
    "llama-cpp-python the node fetches the official llama.cpp binaries. Belongs to this tab, "
    "so the 8B tab keeps its own."
)

MODEL_8B_TOOLTIP = (
    "Base model for the 8B adapter -- a Qwen3-VL-8B, either as a GGUF pair (the model and its "
    "projector, from one conversion) or as the official safetensors folder the adapter was "
    "trained on. Only the 8B fits this LoRA; another size is refused by name and number "
    "before anything is downloaded. Belongs to this tab."
)

QUANT_27B_TOOLTIP = (
    "How to load an unquantized 27B checkpoint: nf4 needs about 16 GB of VRAM, int8 about 28, "
    "bfloat16 about 54. Ignored for GGUF models and for checkpoints that are already "
    "quantized. Belongs to this tab."
)

QUANT_8B_TOOLTIP = (
    "How to load an unquantized 8B checkpoint: nf4 needs about 8 GB of VRAM, int8 about 13, "
    "bfloat16 about 20. Ignored for GGUF models and for checkpoints that are already "
    "quantized. Belongs to this tab."
)

REFERENCE_VIDEO_TOOLTIP = (
    "A clip the target video reuses, for Ref2VA on the Omni tab. Sampled into frames and shown "
    "to the model as <Video 1>. Read by nothing else: the 27B and 8B adapters were never "
    "trained on a clip, and the four frame tasks take pictures alone."
)

REFERENCE_AUDIO_TOOLTIP = (
    "A sound the target video reuses, for Ref2VA on the Omni tab, shown to the model as "
    "<Audio 1>. This is the one input in the pack that reaches a rewriter as sound rather than "
    "as a sentence about it, and only the Omni adapter can hear it."
)

QUANT_SPEED_NOTE = (
    "bitsandbytes buys VRAM here, not speed: measured on this adapter, int8 generates at "
    "about a third of bfloat16's rate and nf4 at four fifths, because both dequantize on "
    "every matmul. Pick the largest your card holds."
)

MODEL_OMNI_TOOLTIP = (
    "Base model for the Omni adapter -- a Qwen2.5-Omni-7B, either as a GGUF pair (the model "
    "and its projector) or as the official safetensors folder. Its projector carries an audio "
    "encoder as well as a vision one; a Qwen2.5-VL of the same size looks identical by the "
    "numbers and is marked 'vision only' in the list, because the adapter would attach to it "
    "and then write about sound it never heard. Belongs to this tab."
)

QUANT_OMNI_TOOLTIP = (
    "How to load an unquantized Qwen2.5-Omni-7B: nf4 needs about 9 GB of VRAM, int8 about 12, "
    "bfloat16 about 20. " + QUANT_SPEED_NOTE + " Ignored for GGUF models and for "
    "checkpoints that are already quantized. Belongs to this tab."
)

KEEP_TOOLTIP = (
    "Keep the model in VRAM after the rewrite. Honoured on every route except one: a GGUF 8B "
    "or Omni base running a task with frames goes through llama-mtmd-cli, and the model "
    "leaves with the subprocess. Leave it off when the same GPU has to run MiniMax-H3 video "
    "generation afterwards."
)


def connected_references(supplied: dict, switches: str) -> dict[str, object]:
    """The reference slots that are both plugged in and switched on, in slot order.

    A switched-off row is reported as unplugged rather than as an error, so the
    message the task check writes afterwards is the same one an empty socket
    gets -- which is the message that names the fix.
    """
    off = _disabled(switches)
    return {
        name: supplied.get(name)
        for name in REFERENCE_SLOTS
        if supplied.get(name) is not None and name not in off
    }



def first_base(chosen: str, offered: list[str]) -> str:
    """The chosen base, or the first on offer when a workflow predates the widget.

    A workflow saved before this tab existed has no value for its model widget,
    and the API hands the node an empty string rather than the default. Falling
    through to the first entry is what the widget itself would have shown.
    """
    if chosen:
        return chosen
    if not offered:
        raise RuntimeError(
            "No Qwen2.5-Omni base is on offer, which means models.json could not be read. "
            "Pick a base on the Prompt Rewriter Omni node to see the same list."
        )
    return offered[0]


def omni_references(task: str, connected: dict[str, object]) -> list:
    """The connected sockets as the reference list the Omni adapter takes.

    Two rules, because the tasks mean two different things by a reference.

    On the four frame tasks a picture is a *position* -- first frame or final
    frame -- so the slots are picked by name through the 8B's own check, which
    refuses the wrong one rather than handing over whichever socket happened to
    be plugged in. A clip or a sound is not a position and is refused with them.

    On Ref2VA nothing is a position: every connected socket is a reference the
    target video reuses, and the only question is what order they go in. Here
    that is the order of the sockets themselves -- pictures, then the clip, then
    the sound -- which is why this node stops at four and the Prompt Rewriter
    Omni node, which has a strip to drag, takes twelve.
    """
    wanted = writer_omni.normalize_task(task)

    if wanted == writer_omni.REF_TASK:
        return [
            writer_omni.Reference(name, KIND_OF_SLOT[name], value)
            for name, value in connected.items()
        ]

    heard = [name for name in connected if KIND_OF_SLOT[name] != "image"]
    if heard:
        raise ValueError(
            f"{task} is written from pictures alone, and {', '.join(sorted(heard))} "
            f"{'is' if len(heard) == 1 else 'are'} connected. Switch those rows off, or pick "
            f"Ref2VA, which is the task that takes a clip and a sound."
        )
    return [
        writer_omni.Reference(name, "image", value)
        for name, value in writer_8b.frames_for(
            wanted, connected.get("first_frame"), connected.get("last_frame")
        )
    ]


def _unread(progress: NodeProgress, slots: list[str], why: str) -> None:
    """Say on the node that something connected is going nowhere."""
    note = (
        f"{', '.join(sorted(slots))} {'are' if len(slots) > 1 else 'is'} connected and not "
        f"read: {why}. Switch to the Omni tab and pick Ref2VA, which is the task that takes "
        f"them."
    )
    log.info("[minimax_h3_rewriter.universal_rewriter] %s", note)
    progress.text(note, force=True)
    announce(progress.node_id, [("warn", note)])


class MiniMaxH3UniversalRewriter(io.ComfyNode):
    """Run either prompt-rewriter LoRA, with the tab holding what differs."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3UniversalRewriter",
            display_name="MiniMax-H3 Universal Rewriter",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                io.Custom(OPTIONS_TYPE).Input("options", optional=True),
                io.Image.Input(
                    "first_frame",
                    optional=True,
                    tooltip=writer_8b.FRAME_TOOLTIPS["first_frame"] + FRAME_ON_27B,
                ),
                io.Image.Input(
                    "last_frame",
                    optional=True,
                    tooltip=writer_8b.FRAME_TOOLTIPS["last_frame"] + FRAME_ON_27B,
                ),
                io.String.Input(
                    "frame_switches",
                    default="{}",
                    optional=True,
                    tooltip=SWITCH_TOOLTIP,
                ),
                io.Combo.Input(
                    "lora",
                    options=list(LORAS),
                    default=LORA_27B,
                    tooltip=LORA_TOOLTIP,
                ),
                io.Combo.Input(
                    "task",
                    options=list(ALL_MODES),
                    default=TEXT_TASK,
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
                    io.Float.Input(
                        "duration",
                        display_mode=io.NumberDisplay.slider,
                        **duration_options(DURATION_TOOLTIP),
                    ),
                    types=[io.Int],
                ),
                io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="The short prompt to expand into an H3 audio-video description.",
                ),
                io.Combo.Input(
                    "model_27b",
                    options=model_choices(),
                    tooltip=MODEL_27B_TOOLTIP,
                ),
                io.Combo.Input(
                    "model_8b",
                    options=writer_8b.model_choices(),
                    tooltip=MODEL_8B_TOOLTIP,
                ),
                io.Combo.Input(
                    "quantization_27b",
                    options=list(QUANTIZATIONS),
                    default="nf4",
                    tooltip=QUANT_27B_TOOLTIP,
                ),
                io.Combo.Input(
                    "quantization_8b",
                    options=list(QUANTIZATIONS),
                    default="nf4",
                    tooltip=QUANT_8B_TOOLTIP,
                ),
                io.Boolean.Input(
                    "greedy",
                    default=True,
                    tooltip="Deterministic decoding. Turn off to sample; see the options node.",
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
                    tooltip=KEEP_TOOLTIP,
                ),
                io.Boolean.Input(
                    "bypass",
                    default=False,
                    optional=True,
                    tooltip=BYPASS_TOOLTIP,
                ),
                io.Combo.Input(
                    "model_omni",
                    options=writer_omni.model_choices(),
                    optional=True,
                    tooltip=MODEL_OMNI_TOOLTIP,
                ),
                io.Combo.Input(
                    "quantization_omni",
                    options=list(QUANTIZATIONS),
                    default="nf4",
                    optional=True,
                    tooltip=QUANT_OMNI_TOOLTIP,
                ),
                io.Video.Input(
                    "reference_video",
                    optional=True,
                    tooltip=REFERENCE_VIDEO_TOOLTIP,
                ),
                io.Audio.Input(
                    "reference_audio",
                    optional=True,
                    tooltip=REFERENCE_AUDIO_TOOLTIP,
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
                *(io.String.Output(display_name=name) for name in UNIVERSAL_FIELDS),
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
        lora,
        task,
        resolution,
        duration,
        prompt,
        model_27b,
        model_8b,
        quantization_27b,
        quantization_8b,
        greedy,
        seed,
        keep_model_loaded,
        first_frame=None,
        last_frame=None,
        frame_switches="{}",
        options=None,
        bypass=False,
        model_omni="",
        quantization_omni="nf4",
        reference_video=None,
        reference_audio=None,
        aspect_ratio=None,
        repeat_last=False,
        library_pick="",
    ) -> io.NodeOutput:
        given = dict(locals())
        progress = NodeProgress(cls.hidden.unique_id)
        empty = ("",) * len(UNIVERSAL_FIELDS)

        if bypass:
            progress.finish("bypassed")
            return io.NodeOutput((prompt or "").strip(), *empty)

        connected = connected_references(
            {
                "first_frame": first_frame,
                "last_frame": last_frame,
                "reference_video": reference_video,
                "reference_audio": reference_audio,
            },
            frame_switches,
        )

        chosen, saved = library.picked(
            library_pick, repeat_last, "MiniMaxH3UniversalRewriter", 1 + len(UNIVERSAL_FIELDS),
            cls.hidden.unique_id,
            having=[KIND_OF_SLOT.get(name) for name in connected],
        )
        if chosen is not None:
            return io.NodeOutput(*chosen)

        kept = memory.repeat(
            cls.hidden.unique_id, "MiniMaxH3UniversalRewriter", repeat_last and not saved, given
        )
        if kept is not None:
            return io.NodeOutput(*kept)

        if saved:
            fields = split_fields(saved, FIELDS_FOR_TASK[task], BODY_FIELD[task])
            return io.NodeOutput(
                saved, *(fields.get(name, "") for name in UNIVERSAL_FIELDS)
            )

        if not (prompt or "").strip():
            raise ValueError("prompt must not be empty")

        resolution = aspect.resolve(aspect_ratio, resolution)

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        frames = {
            name: value
            for name, value in connected.items()
            if KIND_OF_SLOT[name] == "image"
        }
        heard = [name for name in connected if KIND_OF_SLOT[name] != "image"]

        if task == REF_MODE and lora != LORA_OMNI:
            raise ValueError(
                f"{REF_MODE} is the full-reference task, and only the Omni LoRA was trained on "
                f"it. Switch to the Omni tab, or pick one of {', '.join(BASE_MODES)}."
            )

        def write(extra: str = "") -> str:
            if lora == LORA_OMNI:
                return writer_omni.rewrite_omni(
                    model=first_base(model_omni, writer_omni.model_choices()),
                    prompt=prompt + extra,
                    task=task,
                    resolution=resolution,
                    duration=float(duration),
                    quantization=quantization_omni,
                    greedy=greedy,
                    seed=seed,
                    keep_loaded=keep_model_loaded,
                    settings=settings,
                    progress=progress,
                    references=omni_references(task, connected),
                )
            if lora == LORA_8B:
                return writer_8b.rewrite_8b(
                    model_8b, prompt + extra, task, resolution, duration, quantization_8b,
                    greedy, seed, keep_model_loaded, settings, progress,
                    frames.get("first_frame"), frames.get("last_frame"),
                )
            return rewrite_t2va(
                model_27b, prompt + extra, resolution, duration, quantization_27b,
                greedy, seed, keep_model_loaded, settings, progress,
            )

        if lora == LORA_8B and heard:
            _unread(progress, heard, "the 8B LoRA has neither ear nor a clip task")
        elif lora == LORA_27B:
            unread = sorted(connected)
            if task != TEXT_TASK or unread:
                note = (
                    f"the 27B LoRA was trained on {TEXT_TASK} alone, so it writes that"
                    + (f" rather than {task}" if task != TEXT_TASK else "")
                    + (
                        f", and {', '.join(unread)} {'are' if len(unread) > 1 else 'is'} "
                        f"not read"
                        if unread
                        else ""
                    )
                    + ". Switch to the 8B or Omni tab, which are shown the frames -- or "
                    "describe them with the caption node and put that in 'prompt'."
                )
                log.info("[minimax_h3_rewriter.universal_rewriter] %s", note)
                progress.text(note, force=True)
                announce(progress.node_id, [("warn", note)])

        text = write()
        text = _fix_once(
            text, progress, write, FIELDS_FOR_TASK[task],
            task=task, duration=duration,
            having=[KIND_OF_SLOT.get(name) for name in connected],
            fallback=BODY_FIELD[task], settings=settings,
        )

        fields = split_fields(text, FIELDS_FOR_TASK[task], BODY_FIELD[task])
        _report(
            progress, text, fields, FIELDS_FOR_TASK[task],
            task=task, duration=duration,
            having=[KIND_OF_SLOT.get(name) for name in connected],
            settings=settings,
        )
        outputs = (text,) + tuple(fields.get(name, "") for name in UNIVERSAL_FIELDS)
        memory.keep(
            cls.hidden.unique_id, "MiniMaxH3UniversalRewriter", outputs, given,
            references=snapshot.take(
                (name, KIND_OF_SLOT.get(name), value) for name, value in connected.items()
            ),
            fields=UNIVERSAL_FIELDS,
        )
        return io.NodeOutput(*outputs)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3UniversalRewriter": MiniMaxH3UniversalRewriter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3UniversalRewriter": "MiniMax-H3 Universal Rewriter",
}
