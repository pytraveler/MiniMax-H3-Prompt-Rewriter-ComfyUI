"""One node for a whole shot: the references, their order, and the rewrite.

Three nodes used to be needed for a full-reference prompt -- one to describe the
assets, one to write from the block they produce, and a different writer again
if the task was Ref2VA rather than a frame task. All three still work and none
of them is going away; but the split was never about the format. It was about
which node happened to hold which widget.

What the split cost is *order*. ``Picture 1`` and ``Picture 2`` are not
interchangeable in FL2VA: one opens the video and the other closes it, and the
only thing deciding which is which was the order the caption node happened to
write them in, which came in turn from which slot each was plugged into. Real,
load-bearing, and nowhere on screen.

Here it is a widget. The strip under the inputs draws one square per connected
reference, in the order the block will be written; drag them and the numbering
follows. That is also how one universal socket keeps all four Ref2VA labels: the
socket says what a reference *is* -- a picture, a clip, a sound -- and the badge
on its square says what it is *for*.

The strip, the task switch and the aspect-ratio picker are drawn by
``web/js/universal_widgets.js`` as HTML rather than on the canvas, which is the
one widget mechanism ComfyUI renders in both the classic canvas and the Nodes
2.0 renderer. Each one takes over an ordinary widget declared here and keeps its
name and its place, so a browser that never loads the script still shows a
dropdown for every one of them and the node still runs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from comfy_api.latest import io

from . import (
    aspect,
    clip_caption,
    guide_prompt,
    library,
    media,
    memory,
    mtmd_engine,
    snapshot,
)
from .constants import OUTPUT_FIELDS, REF_OUTPUT_FIELDS, RESOLUTIONS
from .fields import split_sections
from .nodes import (
    BYPASS_TOOLTIP,
    CAPTION_LENGTHS,
    CATEGORY,
    DEFAULT_OPTIONS,
    INSTRUCTIONS_TOOLTIP,
    OPTIONS_TYPE,
    SYSTEM_PROMPT_TOOLTIP,
    _ensure_pair,
    _guided_text,
    _fix_once,
    _report,
    _resolve_captioner_choice,
    caption_question,
    captioner_choices,
    next_index,
    slot_instructions,
    writer_choices,
)
from .multi_caption import _check_encoders
from .progress import NodeProgress, announce

log = logging.getLogger(__name__)

MAX_REFERENCES = 12

TASKS = guide_prompt.ALL_MODES
REF_TASK = guide_prompt.REF_MODE
TEXT_TASK = "T2VA"

PICTURES_FOR_TASK = {"I2VA": 1, "FL2VA": 2, "L2VA": 1}

ROLE_SUBJECT = "Subject"
ROLE_PICTURE = "Picture"
ROLE_VIDEO = "Video"
ROLE_AUDIO = "Audio"

IMAGE_ROLES = (ROLE_PICTURE, ROLE_SUBJECT, ROLE_VIDEO)

FIXED_ROLE = {"video": ROLE_VIDEO, "audio": ROLE_AUDIO}

ALL_FIELDS = (
    OUTPUT_FIELDS[0],
    *REF_OUTPUT_FIELDS[:4],
    *OUTPUT_FIELDS[1:],
)

DURATION_MIN = 0.1
DURATION_DEFAULT = 10.0

DURATION_CEILING = 600.0
DURATION_PROPERTY = "max_duration"
DURATION_PROPERTY_DEFAULT = 30.0

DESCRIPTION = (
    "Describes every connected reference and writes the finished MiniMax-H3 prompt, in one "
    "node, for all five tasks. One socket takes an image, a clip or a sound; the strip under "
    "the inputs shows what is connected and in what order, and dragging a square renumbers the "
    "block. A square's badge says what its image is for -- a frame, a subject, or a batch to "
    "read as a clip -- which is how one socket still produces the four labels the Ref2VA guide "
    "allows. No LoRA anywhere: both models are ordinary GGUFs."
)

LAYOUT_TOOLTIP = (
    "The strip's state as JSON -- which squares are switched off, what order they are in, and "
    "what each image is being used as. It is a widget so the arrangement travels with the "
    "workflow and through the API; the interface draws it as squares instead. A slot missing "
    "from it is on, in slot order, and used as a picture."
)

REFERENCE_TOOLTIP = (
    "One image, clip or sound per slot; more slots appear as you fill them. What a reference "
    "is used for is set on its square in the strip below, not by which slot it is in -- so "
    "there is no wrong socket to plug into here."
)

TASK_TOOLTIP = (
    "T2VA: text alone, references ignored. I2VA: one picture, the first frame. L2VA: one "
    "picture, the final frame. FL2VA: two pictures, first and last. Ref2VA: any number of "
    "references the target video reuses, written with the six-section full-reference guide. "
    "Everything but T2VA opens with the alignment line, duration already filled in."
)

DURATION_TOOLTIP = (
    "Target clip length in seconds; drives shot count and pacing. The slider's upper end is "
    "the node's own 'max_duration' property (right-click, Properties Panel), 30 seconds until "
    "you change it -- MiniMax's guide is written around clips of a few seconds, so a shorter "
    "range is a more useful slider than a longer one."
)


@dataclass(frozen=True)
class Asset:
    """One connected, switched-on reference, ready to be described."""

    slot: str
    role: str
    kind: str
    value: object


def kind_of(value) -> str:
    """Whether a value arriving on the universal socket is a picture, clip or sound.

    Nothing here needs torch or the video API imported: an IMAGE is a tensor and
    carries a shape, an AUDIO is a mapping holding a waveform -- possibly a lazy
    one, which is why membership is asked rather than the key read -- and a VIDEO
    is neither.
    """
    if hasattr(value, "shape"):
        return "image"
    if hasattr(value, "keys"):
        try:
            if "waveform" in value:
                return "audio"
        except TypeError:
            pass
    return "video"


def layout_of(raw: str) -> tuple[list[str], set[str], dict[str, str]]:
    """The strip's state: display order, switched-off slots, chosen image roles.

    Anything unreadable means the default arrangement, the same way the caption
    node treats a corrupted checkbox map: a broken widget should cost the layout,
    never the run.
    """
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    def names(key: str) -> list[str]:
        found = parsed.get(key)
        if not isinstance(found, list):
            return []
        return [name for name in found if isinstance(name, str)]

    roles = parsed.get("roles")
    if not isinstance(roles, dict):
        roles = {}
    return (
        names("order"),
        set(names("off")),
        {
            name: role
            for name, role in roles.items()
            if isinstance(name, str) and role in IMAGE_ROLES
        },
    )


def _slot_number(name: str) -> int:
    tail = name.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def arrange(supplied: dict | None, raw_layout: str) -> tuple[list[Asset], int]:
    """Every connected reference in strip order, plus how many are switched off.

    A slot the strip has never seen -- just plugged in, or connected through the
    API by someone who never opened the graph -- goes after the ones it knows, in
    slot order. So an untouched node still numbers its references the obvious
    way, and the widget only ever overrides that.
    """
    connected = {name: value for name, value in (supplied or {}).items() if value is not None}
    order, off, roles = layout_of(raw_layout)

    named = [name for name in order if name in connected]
    known = set(named)
    named += sorted((name for name in connected if name not in known), key=_slot_number)

    assets: list[Asset] = []
    skipped = 0
    for name in named:
        if name in off:
            skipped += 1
            continue
        kind = kind_of(connected[name])
        role = FIXED_ROLE.get(kind) or roles.get(name, ROLE_PICTURE)
        assets.append(Asset(name, role, kind, connected[name]))
    return assets, skipped


def refuse_mismatch(task: str, assets: list[Asset]) -> None:
    """Stop before any weights move if the task and the strip disagree.

    Both messages name the strip rather than the sockets, because the strip is
    where the fix is: a reference that is in the way is switched off or given a
    different badge, not unplugged.
    """
    if task == REF_TASK:
        if not assets:
            raise ValueError(
                "Ref2VA writes about reference assets the target video reuses, so it needs at "
                "least one. Connect an image, a clip or a sound and switch its square on in the "
                "strip -- or pick a task that writes from text alone."
            )
        return

    wanted = PICTURES_FOR_TASK[task]
    pictures = [asset for asset in assets if asset.role == ROLE_PICTURE]
    if len(pictures) == wanted:
        return

    if len(pictures) > wanted:
        raise ValueError(
            f"{task} is written from {wanted} picture(s), and the strip has {len(pictures)}. "
            f"Switch the extra squares off, or click a badge to call one a subject or a clip: "
            f"those are reference material rather than frames, and they are not counted here."
        )
    raise ValueError(
        f"{task} is written from {wanted} picture(s), and the strip has {len(pictures)}. "
        f"Connect the missing image, switch its square back on, or click a badge to turn a "
        f"subject back into a picture."
    )


def _clip_note(asset: Asset, max_frames: int) -> str:
    """The 'these frames are one clip' sentence, for a batch the badge calls a clip.

    ``mtmd_engine`` writes this itself for a real VIDEO, but an IMAGE batch reaches
    it as pictures and gets no note -- and without one the answer comes back about
    several unrelated photographs rather than about a clip.
    """
    if asset.role != ROLE_VIDEO or asset.kind != "image":
        return ""
    total = int(getattr(asset.value, "shape", [1])[0])
    count = min(total, int(max_frames))
    return mtmd_engine.clip_note(count, 0.0) if count > 1 else ""


class MiniMaxH3UniversalWriter(io.ComfyNode):
    """Caption every reference and write the prompt, for all five tasks."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3UniversalWriter",
            display_name="MiniMax-H3 Universal Writer",
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
                                "An image or batch of frames, a clip, or a sound. Its square "
                                "appears in the strip as soon as it is connected."
                            ),
                        ),
                        prefix="ref_",
                        min=0,
                        max=MAX_REFERENCES,
                    ),
                ),
                io.Clip.Input(
                    "clip",
                    optional=True,
                    tooltip=(
                        "A multimodal text encoder loaded by 'CLIPLoader' -- Qwen3-VL or "
                        "Gemma-4. Connect it and every reference here is described by that "
                        "model instead of by 'caption_model': it stays loaded between "
                        "references and between runs. Only Gemma-4 E2B, E4B and 12B can hear "
                        "audio. Leave it unconnected and nothing changes."
                    ),
                ),
                io.String.Input(
                    "previous",
                    optional=True,
                    force_input=True,
                    tooltip=(
                        "A reference block from an earlier caption node, if this one is in a "
                        "chain. Its labels are counted, so the references here carry on from "
                        "where it stopped."
                    ),
                ),
                io.String.Input(
                    "reference_layout",
                    default="{}",
                    tooltip=LAYOUT_TOOLTIP,
                ),
                io.Combo.Input(
                    "task",
                    options=list(TASKS),
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
                io.Float.Input(
                    "duration",
                    default=DURATION_DEFAULT,
                    min=DURATION_MIN,
                    max=DURATION_CEILING,
                    step=0.1,
                    round=0.1,
                    display_mode=io.NumberDisplay.slider,
                    tooltip=DURATION_TOOLTIP,
                ),
                io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip=(
                        "What the target video should show. For Ref2VA, also how it uses the "
                        "references -- the descriptions say what they are, this says what they "
                        "are for."
                    ),
                ),
                io.Combo.Input(
                    "caption_model",
                    options=captioner_choices(),
                    tooltip=(
                        "The multimodal GGUF that reads the references, with its projector. "
                        "Entries prefixed 'on disk:' are pairs already in your model folders. "
                        "One model reads every reference here, so it has to cover every kind "
                        "you connected. Unused while 'clip' is connected, or on T2VA."
                    ),
                ),
                io.Combo.Input(
                    "caption_length",
                    options=list(CAPTION_LENGTHS),
                    default="standard",
                    tooltip="How much the captioner is asked to write, for every reference here.",
                ),
                io.Combo.Input(
                    "writer_model",
                    options=writer_choices(),
                    tooltip=(
                        "The language model that writes the finished prompt from the guide. Any "
                        "instruction-following GGUF; the full-reference guide is the longer of "
                        "the two, so a 4B holds the format but a 9B keeps the labels consistent "
                        "across all six sections."
                    ),
                ),
                io.Boolean.Input(
                    "greedy",
                    default=True,
                    tooltip=(
                        "Deterministic decoding. Worth keeping on for small models, which drift "
                        "out of the format when they sample."
                    ),
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
                        "Keep the writer in VRAM after the rewrite. Leave off when the same GPU "
                        "has to run MiniMax-H3 video generation afterwards."
                    ),
                ),
                io.Int.Input(
                    "max_frames",
                    default=media.DEFAULT_MAX_FRAMES,
                    min=1,
                    max=64,
                    optional=True,
                    tooltip=(
                        "How many frames to take from a batch or a clip, spread evenly. All of "
                        "them would overflow the context and the wall clock."
                    ),
                ),
                io.Int.Input(
                    "context_size",
                    default=mtmd_engine.CONTEXT_FROM_MODEL,
                    min=0,
                    max=131072,
                    step=1024,
                    optional=True,
                    tooltip=(
                        "Context for the captioner. 0 sizes it from the references and the card, "
                        "rather than from a model header that can say 256k and cost tens of GB of "
                        "KV cache. Set a number to say it yourself. The writer sizes its own "
                        "context from the guide."
                    ),
                ),
                io.Boolean.Input(
                    "bypass",
                    default=False,
                    optional=True,
                    tooltip=BYPASS_TOOLTIP,
                ),
                io.String.Input(
                    "reference_instructions",
                    default="{}",
                    optional=True,
                    tooltip=INSTRUCTIONS_TOOLTIP,
                ),
                io.String.Input(
                    "system_prompt",
                    default="",
                    multiline=True,
                    optional=True,
                    tooltip=SYSTEM_PROMPT_TOOLTIP,
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
                io.String.Output(display_name="reference_assets"),
                io.String.Output(display_name="captions"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, library_pick="", repeat_last=False, **kwargs):
        """Whether the saved prompt this node is pointed at has changed since.

        A record edited in the library window changes none of this node's
        inputs, so without this the answer would come back out of ComfyUI's
        execution cache, still saying what it said before the edit.
        """
        return library.stamp(library_pick, repeat_last)

    @classmethod
    def execute(
        cls,
        task,
        resolution,
        duration,
        prompt,
        caption_model,
        caption_length,
        writer_model,
        greedy,
        seed,
        keep_model_loaded,
        references=None,
        clip=None,
        previous="",
        reference_layout="{}",
        reference_instructions="{}",
        system_prompt="",
        options=None,
        max_frames=media.DEFAULT_MAX_FRAMES,
        context_size=mtmd_engine.CONTEXT_FROM_MODEL,
        bypass=False,
        aspect_ratio=None,
        repeat_last=False,
        library_pick="",
    ) -> io.NodeOutput:
        given = dict(locals())
        progress = NodeProgress(cls.hidden.unique_id)
        block = (previous or "").strip()
        empty = ("",) * len(ALL_FIELDS)

        if bypass:
            progress.finish("bypassed")
            return io.NodeOutput((prompt or "").strip(), *empty, block, "")

        assets, skipped = arrange(references, reference_layout)

        chosen, saved = library.picked(
            library_pick, repeat_last, "MiniMaxH3UniversalWriter", 1 + len(ALL_FIELDS) + 2,
            cls.hidden.unique_id, having=[item.kind for item in assets],
        )
        if chosen is not None:
            return io.NodeOutput(*chosen)
        if saved:
            _head, sections = split_sections(
                saved, guide_prompt.FIELDS_FOR_MODE[task],
                fallback=guide_prompt.BODY_FIELD[task],
            )
            return io.NodeOutput(
                saved,
                *(sections.get(name, "") for name in ALL_FIELDS),
                block,
                "",
            )

        kept = memory.repeat(
            cls.hidden.unique_id, "MiniMaxH3UniversalWriter", repeat_last, given
        )
        if kept is not None:
            return io.NodeOutput(*kept)

        resolution = aspect.resolve(aspect_ratio, resolution)

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        if task == TEXT_TASK:
            if assets or block:
                note = (
                    f"T2VA writes from the prompt alone, so "
                    f"{len(assets)} connected reference(s) and any incoming block are ignored"
                )
                log.info("[minimax_h3_rewriter.universal] %s", note)
                progress.text(note, force=True)
                announce(cls.hidden.unique_id, [("warn", note)])
            assets = []
        else:
            refuse_mismatch(task, assets)

        captions: list[str] = []
        empty: list[str] = []
        if assets:
            kinds = {asset.kind for asset in assets}
            model_path = mmproj_path = None
            if clip is None:
                choice = _resolve_captioner_choice(caption_model)
                if choice.local:
                    model_path, mmproj_path = choice.reference, choice.mmproj
                else:
                    model_path, mmproj_path = _ensure_pair(
                        choice.reference, choice.file, choice.mmproj, "Captioner",
                        settings["auto_download"], progress,
                    )
                _check_encoders(mmproj_path, kinds)
            else:
                clip_caption.check(clip, kinds)

            asked_for = slot_instructions(reference_instructions)

            progress.set_total(len(assets))
            for done, asset in enumerate(assets):
                index = next_index(block, asset.role)
                progress.update(
                    done,
                    f"{asset.role} {index}: reading {asset.slot} ({done + 1} of {len(assets)})",
                )

                asked = caption_question(
                    asset.role, caption_length, asked_for.get(asset.slot)
                )
                note = _clip_note(asset, max_frames)
                if note:
                    asked = f"{note}\n\n{asked}"

                as_image = asset.value if asset.kind == "image" else None
                as_audio = asset.value if asset.kind == "audio" else None
                as_video = asset.value if asset.kind == "video" else None

                if clip is not None:
                    caption = clip_caption.describe(
                        clip,
                        instruction=asked,
                        image=as_image,
                        audio=as_audio,
                        video=as_video,
                        max_frames=int(max_frames),
                        seed=int(seed),
                        settings=settings,
                        progress=progress,
                    )
                else:
                    caption = mtmd_engine.describe(
                        model_path=model_path,
                        mmproj_path=mmproj_path,
                        instruction=asked,
                        image=as_image,
                        audio=as_audio,
                        video=as_video,
                        max_frames=int(max_frames),
                        gpu_layers=int(settings["gpu_layers"]),
                        n_ctx=int(context_size),
                        seed=int(seed),
                        greedy=True,
                        max_new_tokens=min(int(settings["max_new_tokens"]), 1024),
                        temperature=float(settings["temperature"]),
                        top_p=float(settings["top_p"]),
                        top_k=int(settings["top_k"]),
                        device=settings["device"],
                        backend=settings["llama_backend"],
                        auto_download=settings["auto_download"],
                        progress=progress,
                    )

                caption = " ".join(caption.split())
                if not caption:
                    empty.append(f"{asset.role} {index} ({asset.slot})")
                    log.warning(
                        "[minimax_h3_rewriter.universal] %s came back with an empty caption",
                        asset.slot,
                    )
                captions.append(caption)
                block = f"{block}\n{asset.role} {index}: {caption}".strip()

            described = f"{len(assets)} described"
            if skipped:
                described += f", {skipped} switched off"
            if empty:
                silence = (
                    f"nothing came back for {', '.join(empty)}. Those labels are in "
                    f"the block with nothing after them, so the writer has been told an asset "
                    f"exists and not what it is -- try another captioner."
                )
                described = f"WARNING: {silence}\n{described}"
                announce(cls.hidden.unique_id, [("warn", silence)])
            progress.update(len(assets), f"{described}\n{block}")

        material = "" if task == TEXT_TASK else block
        text = _guided_text(
            task, writer_model, prompt, resolution, duration, material,
            greedy, seed, keep_model_loaded, settings, progress, system_prompt,
        )

        names = guide_prompt.FIELDS_FOR_MODE[task]
        text = _fix_once(
            text, progress,
            lambda extra: _guided_text(
                task, writer_model, prompt + extra, resolution, duration, material,
                greedy, seed, keep_model_loaded, settings, progress, system_prompt,
            ),
            names, task=task, duration=duration,
            having=[item.kind for item in assets],
            fallback=guide_prompt.BODY_FIELD[task], settings=settings,
        )
        _head, sections = split_sections(text, names, fallback=guide_prompt.BODY_FIELD[task])
        _report(
            progress, text, sections, names,
            task=task, duration=duration,
            having=[item.kind for item in assets],
            settings=settings,
        )

        fields = tuple(sections.get(name, "") for name in ALL_FIELDS)
        outputs = (text,) + fields + (block, "\n".join(captions))
        memory.keep(
            cls.hidden.unique_id, "MiniMaxH3UniversalWriter", outputs, given,
            references=snapshot.take((item.slot, item.kind, item.value) for item in assets),
        )
        return io.NodeOutput(*outputs)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3UniversalWriter": MiniMaxH3UniversalWriter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3UniversalWriter": "MiniMax-H3 Universal Writer",
}
