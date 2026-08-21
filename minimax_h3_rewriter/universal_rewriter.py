"""Both prompt-rewriter LoRAs in one node, with the tab remembering the rest.

There are two adapters and they are not two settings of one thing. The 27B is
text: Qwen3.6-27B, one task, and a reference frame reaches it only as a sentence
somebody wrote about it. The 8B is multimodal: Qwen3-VL-8B, four tasks, and the
picture itself. Different base, different size, different download.

Which is exactly why choosing between them by hand is tedious. The prompt is the
same prompt, the aspect ratio is the same aspect ratio, and the duration is the
same duration -- so trying the other adapter means retyping all of it into a
second node, and then keeping the two in step. What actually differs is two
widgets: which base model, and how to quantize it.

**No captioner here, deliberately.** A description of a frame does reach the 27B
if you fold it into the prompt, and it is not wasted -- but the picture ends up
absorbed into the scene rather than pinned to 0.00 seconds, which is what the
adapter's own roadmap says: T2VA is finished and FL2VA is not. A widget on this
node would have looked like the frame task the 27B cannot do. Describe a frame
with the caption node and paste it into ``prompt`` when that is what you want,
and use the 8B tab when the picture has to *be* a frame.

So the tab carries those two and nothing else. Everything above and below it is
shared, and switching adapters mid-graph costs one click. The 8B's task switch
is shared too, in the sense that the 27B tab does not touch it: it shows T2VA lit
with the frame tasks greyed out, because that is the honest picture of a text
model, and the value the 8B tab had is still there when you switch back.

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

from . import writer_8b
from .constants import (
    DURATION_MAX,
    DURATION_MIN,
    OUTPUT_FIELDS,
    QUANTIZATIONS,
    RESOLUTIONS,
)
from .fields import split_fields
from .guide_prompt import BASE_MODES
from .multi_caption import _disabled
from .nodes import (
    BYPASS_TOOLTIP,
    CATEGORY,
    DEFAULT_OPTIONS,
    OPTIONS_TYPE,
    model_choices,
    rewrite_t2va,
)
from .progress import NodeProgress

log = logging.getLogger(__name__)

LORA_27B = "27B LoRA"
LORA_8B = "8B LoRA"
LORAS = (LORA_27B, LORA_8B)

TEXT_TASK = "T2VA"

FRAME_SLOTS = ("first_frame", "last_frame")

DESCRIPTION = (
    "Runs either MiniMax-H3 prompt-rewriter LoRA and returns a structured audio-video "
    "description. The tab at the top picks the adapter: 27B is text-only and writes T2VA, 8B "
    "is multimodal and looks at the frames you connect, so it also writes I2VA, FL2VA and "
    "L2VA. Only the base model and its quantization belong to a tab -- the prompt, the aspect "
    "ratio, the duration and the seed are shared, so trying the other adapter is one click "
    "rather than a second node. Weights are fetched on first use."
)

FRAME_ON_27B = (
    " Unread on the 27B tab, which takes text alone: describe it with the caption node and "
    "put that in 'prompt' if you want it there."
)

LORA_TOOLTIP = (
    "Which prompt-rewriter LoRA runs. '27B LoRA' is lightx2v's original on Qwen3.6-27B: text "
    "in, T2VA out, about 16 GB of VRAM at nf4 and the better writer of the two. '8B LoRA' is "
    "the multimodal one on Qwen3-VL-8B: it reads the frames itself and writes the alignment "
    "line from what it sees, at about a third of the download. Switching keeps everything but "
    "the base model and its quantization."
)

TASK_TOOLTIP = (
    "T2VA: text alone, no frames. I2VA: 'first_frame' is the first frame. FL2VA: both frames. "
    "L2VA: 'last_frame' is the final frame. Only the 8B LoRA was trained on the frame tasks, "
    "so the 27B tab writes T2VA whatever this says -- and leaves it alone, so it is still here "
    "when you switch back."
)

DURATION_TOOLTIP = (
    "Target clip length in seconds; drives shot count and pacing. The range is what both "
    "adapters were trained on, and a number outside it is a worse prompt rather than a longer "
    "video -- MiniMax-H3 gets the length from its own settings, not from this line."
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

KEEP_TOOLTIP = (
    "Keep the model in VRAM after the rewrite. Honoured on every route except one: a GGUF 8B "
    "base running a task with frames goes through llama-mtmd-cli, and the model leaves with "
    "the subprocess. Leave it off when the same GPU has to run MiniMax-H3 video generation "
    "afterwards."
)


def connected_frames(first_frame, last_frame, switches: str) -> dict[str, object]:
    """The frame slots that are both plugged in and switched on.

    A switched-off row is reported as unplugged rather than as an error, so the
    message the task check writes afterwards is the same one an empty socket
    gets -- which is the message that names the fix.
    """
    off = _disabled(switches)
    supplied = {"first_frame": first_frame, "last_frame": last_frame}
    return {
        name: value
        for name, value in supplied.items()
        if value is not None and name not in off
    }


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
                    options=list(BASE_MODES),
                    default=TEXT_TASK,
                    tooltip=TASK_TOOLTIP,
                ),
                io.Combo.Input(
                    "resolution",
                    options=list(RESOLUTIONS),
                    default="16:9",
                    tooltip="Target aspect ratio the rewrite is composed for.",
                ),
                io.Int.Input(
                    "duration",
                    default=10,
                    min=DURATION_MIN,
                    max=DURATION_MAX,
                    step=1,
                    display_mode=io.NumberDisplay.slider,
                    tooltip=DURATION_TOOLTIP,
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
            ],
            outputs=[
                io.String.Output(display_name="rewritten_prompt"),
                *(io.String.Output(display_name=name) for name in OUTPUT_FIELDS),
            ],
            hidden=[io.Hidden.unique_id],
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
    ) -> io.NodeOutput:
        progress = NodeProgress(cls.hidden.unique_id)
        empty = ("",) * len(OUTPUT_FIELDS)

        if bypass:
            progress.finish("bypassed")
            return io.NodeOutput((prompt or "").strip(), *empty)

        if not (prompt or "").strip():
            raise ValueError("prompt must not be empty")

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        frames = connected_frames(first_frame, last_frame, frame_switches)

        if lora == LORA_8B:
            text = writer_8b.rewrite_8b(
                model_8b, prompt, task, resolution, duration, quantization_8b,
                greedy, seed, keep_model_loaded, settings, progress,
                frames.get("first_frame"), frames.get("last_frame"),
            )
        else:
            if task != TEXT_TASK or frames:
                unread = ", ".join(sorted(frames))
                note = (
                    f"the 27B LoRA was trained on {TEXT_TASK} alone, so it writes that"
                    + (f" rather than {task}" if task != TEXT_TASK else "")
                    + (
                        f", and {unread} {'are' if len(frames) > 1 else 'is'} not read"
                        if frames
                        else ""
                    )
                    + ". Switch to the 8B tab, which is shown the frames -- or describe them "
                    "with the caption node and put that in 'prompt'."
                )
                log.info("[minimax_h3_rewriter.universal_rewriter] %s", note)
                progress.text(note, force=True)

            text = rewrite_t2va(
                model_27b, prompt, resolution, duration, quantization_27b,
                greedy, seed, keep_model_loaded, settings, progress,
            )

        fields = split_fields(text)
        progress.text(text[-2000:] if text else "(empty rewrite)", force=True)
        return io.NodeOutput(text, *(fields[name] for name in OUTPUT_FIELDS))


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3UniversalRewriter": MiniMaxH3UniversalRewriter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3UniversalRewriter": "MiniMax-H3 Universal Rewriter",
}
