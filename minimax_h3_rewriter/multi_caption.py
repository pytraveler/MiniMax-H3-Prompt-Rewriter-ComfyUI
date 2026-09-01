"""Describing every reference asset of a shot in one node.

A chain of single ``Reference Caption`` nodes is exact, but it grows: five
references mean five nodes, five wires and five chances to leave the wrong role
on one of them. This node folds the chain into one box, and it takes the role
out of the user's hands entirely -- the group an asset is plugged into *is* its
label.

That is not a convenience, it is the guide's own vocabulary made structural.
The Ref2VA guide defines exactly four reference labels, ``<Subject N>``,
``<Picture N>``, ``<Video N>`` and ``<Audio N>``, and forbids inventing more. So
four groups of inputs cover the format completely, and the mismatch the single
node can only warn about -- an image described as audio -- stops being
representable at all.

Two details follow from the format rather than from taste:

- **Order.** The block is written subjects, pictures, videos, audio, not in
  wiring order, because that is the order the guide presents them in and the
  order the writer reads them back.
- **Numbering.** Each label is counted within its own category, continuing from
  whatever came in on ``previous``, so this node can still sit in a chain with
  single caption nodes on either side.

The inputs grow themselves, which is ``io.Autogrow`` from the v3 node API and
therefore needs a recent ComfyUI. The import is guarded where the pack registers
its nodes, so an old install loses this node alone instead of all of them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from comfy_api.latest import io

from . import clip_caption, discovery, media, memory, mtmd_engine
from .nodes import (
    BYPASS_CAPTION_TOOLTIP,
    CAPTION_LENGTHS,
    CATEGORY,
    DEFAULT_OPTIONS,
    INSTRUCTIONS_TOOLTIP,
    OPTIONS_TYPE,
    _ensure_pair,
    _resolve_captioner_choice,
    caption_question,
    captioner_choices,
    next_index,
    slot_instructions,
)
from .progress import NodeProgress


@dataclass(frozen=True)
class Group:
    """One growing group of inputs, standing for one Ref2VA label."""

    id: str       
    prefix: str   
    role: str     
    maximum: int
    kind: str     


GROUPS = (
    Group("subjects", "subject_", "Subject", 8, "image"),
    Group("pictures", "picture_", "Picture", 8, "image"),
    Group("videos", "video_", "Video", 4, "video"),
    Group("audios", "audio_", "Audio", 4, "audio"),
)

GROUP_TOOLTIPS = {
    "subjects": (
        "Reusable visible content: a person, an animal, an object, a scene, a costume, a style. "
        "This is the right group for an image that defines what something looks like, which the "
        "guide keeps separate from an image used as an actual frame."
    ),
    "pictures": (
        "Images used as concrete frames: a first, last or key frame, a composition anchor, a "
        "storyboard panel. If the image only defines how a character or a place looks, it belongs "
        "in 'subjects' instead."
    ),
    "videos": (
        "Whole-video relationships: the source clip of an edit, the clip a continuation starts "
        "from, or a clip whose camera work, cuts and pacing are reused."
    ),
    "audios": (
        "Standalone audio: a voice timbre to match, music to reference, an ambience or an effect "
        "to copy."
    ),
}

DESCRIPTION = (
    "Describes every connected reference asset with a small multimodal model and writes them out "
    "as one finished 'reference_assets' block for the Ref2VA writer. The group an asset is "
    "plugged into decides its label, so the four labels the guide allows are the only four that "
    "can come out. Inputs grow as you fill them, and the strip below shows what is connected: "
    "click a square to silence that reference without unplugging it, and the band under a square "
    "asks that one reference something other than its role's usual question."
)

MASK_TOOLTIP = (
    "Which slots are switched off, as JSON, written by the squares on the strip. It is kept as a "
    "widget so the state travels with the workflow and through the API; the interface draws it as "
    "squares instead. A slot missing from the map is on."
)


@dataclass(frozen=True)
class Asset:
    """One connected, unskipped input, ready to be described."""

    slot: str
    role: str
    kind: str
    value: object


def _template_input(group: Group) -> io.Input:
    """The single input an Autogrow group clones for each of its slots."""
    singular = group.prefix.rstrip("_")
    if group.kind == "image":
        return io.Image.Input(singular, tooltip="A frame, or a batch of frames.")
    if group.kind == "video":
        return io.MultiType.Input(
            singular,
            types=[io.Video, io.Image],
            tooltip=(
                "A clip, either as VIDEO or as the batch of frames a video loader outputs. "
                "Frames are sampled evenly up to 'max_frames'."
            ),
        )
    return io.Audio.Input(singular, tooltip="A sound or voice reference.")


def _is_frames(value) -> bool:
    """True for an IMAGE batch, false for a VIDEO object.

    An IMAGE is a tensor and carries a shape; ComfyUI's VideoInput is an object
    with no such attribute, so this needs neither torch nor the video API to be
    imported here.
    """
    return hasattr(value, "shape")


def _disabled(mask: str) -> set[str]:
    """Slot names switched off on the node, from the checkbox map.

    Anything unparseable means nothing is switched off: a corrupted widget
    should cost a wasted caption at worst, never a silently truncated block.
    """
    try:
        parsed = json.loads(mask or "{}")
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, dict):
        return set()
    return {name for name, enabled in parsed.items() if enabled is False}


def _sorted_slots(supplied: dict | None) -> list[tuple[str, object]]:
    """The group's connected slots, in slot order rather than wiring order.

    Autogrow hands back a dict keyed by slot name, and dict order follows how
    the prompt was built. Sorting by the trailing index makes the written block
    depend on where an asset is plugged in and nothing else, so the same graph
    always numbers its labels the same way.
    """
    found = []
    for name, value in (supplied or {}).items():
        if value is None:
            continue
        tail = name.rsplit("_", 1)[-1]
        found.append((int(tail) if tail.isdigit() else 0, name, value))
    return [(name, value) for _, name, value in sorted(found, key=lambda item: item[0])]


def _collect(supplied: dict[str, dict | None], disabled: set[str]) -> tuple[list[Asset], int]:
    """Every asset to describe, in the guide's order, plus how many are switched off."""
    assets: list[Asset] = []
    skipped = 0
    for group in GROUPS:
        for name, value in _sorted_slots(supplied.get(group.id)):
            if name in disabled:
                skipped += 1
                continue
            assets.append(Asset(name, group.role, group.kind, value))
    return assets, skipped


def _check_encoders(mmproj_path: str, kinds: set[str]) -> None:
    """Refuse before any weights move if the projector cannot read what is connected."""
    header = discovery.gguf_header(mmproj_path)
    name = os.path.basename(mmproj_path)
    if "audio" in kinds and not header["audio"]:
        raise RuntimeError(
            f"'{name}' was built without an audio encoder, so this model cannot hear the clips "
            f"you connected. Pick a captioner whose label lists 'audio', or disconnect the audio."
        )
    if kinds & {"image", "video"} and not header["vision"]:
        raise RuntimeError(
            f"'{name}' was built without a vision encoder, so this model cannot see. Pick a "
            f"captioner whose label lists 'vision'."
        )


class MiniMaxH3MultiReferenceCaption(io.ComfyNode):
    """Caption a whole set of reference assets and emit one Ref2VA block."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3MultiReferenceCaption",
            display_name="MiniMax-H3 Multi Reference Caption",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                *(
                    io.Autogrow.Input(
                        group.id,
                        optional=True,
                        tooltip=GROUP_TOOLTIPS[group.id],
                        template=io.Autogrow.TemplatePrefix(
                            input=_template_input(group),
                            prefix=group.prefix,
                            min=0,
                            max=group.maximum,
                        ),
                    )
                    for group in GROUPS
                ),
                io.String.Input(
                    "previous",
                    optional=True,
                    force_input=True,
                    tooltip="reference_assets from an earlier caption node, if this one is in a chain.",
                ),
                io.Clip.Input(
                    "clip",
                    optional=True,
                    tooltip=(
                        "A multimodal text encoder loaded by 'CLIPLoader' -- Qwen3-VL or "
                        "Gemma-4. Connect it and every asset here is described by that model "
                        "instead of by 'model': it stays loaded between assets and between "
                        "runs, so a shot full of references costs one load rather than one per "
                        "asset. Only Gemma-4 E2B, E4B and 12B can hear audio. Leave it "
                        "unconnected and nothing changes."
                    ),
                ),
                io.Custom(OPTIONS_TYPE).Input("options", optional=True),
                io.String.Input(
                    "enabled_mask",
                    default="{}",
                    optional=True,
                    tooltip=MASK_TOOLTIP,
                ),
                io.Combo.Input(
                    "model",
                    options=captioner_choices(),
                    tooltip=(
                        "A multimodal GGUF and its projector. Entries prefixed 'on disk:' are "
                        "pairs already in your ComfyUI model folders. One model reads every asset "
                        "here, so it has to cover every kind you connected -- a vision-only "
                        "captioner cannot take the audio group. Ignored entirely while 'clip' is "
                        "connected."
                    ),
                ),
                io.Combo.Input(
                    "length",
                    options=list(CAPTION_LENGTHS),
                    default="standard",
                    tooltip="How much the model is asked to write, for every asset in this node.",
                ),
                io.Int.Input(
                    "seed",
                    default=42,
                    min=0,
                    max=0xFFFFFFFF,
                    control_after_generate=True,
                ),
                io.Int.Input(
                    "max_frames",
                    default=media.DEFAULT_MAX_FRAMES,
                    min=1,
                    max=64,
                    optional=True,
                    tooltip=(
                        "How many frames to take from an IMAGE batch or a video, spread evenly. "
                        "All of them would overflow the context and the wall clock."
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
                        "0 sizes the context from the references and the card: llama.cpp reserves "
                        "the whole KV cache up front, and a model trained for 256k would ask for "
                        "tens of GB of it before looking at anything. Set a number to say it "
                        "yourself; too small a value fails the run outright rather than truncating."
                    ),
                ),
                io.Boolean.Input(
                    "bypass",
                    default=False,
                    optional=True,
                    tooltip=BYPASS_CAPTION_TOOLTIP,
                ),
                io.String.Input(
                    "reference_instructions",
                    default="{}",
                    optional=True,
                    tooltip=INSTRUCTIONS_TOOLTIP,
                ),
                io.Boolean.Input(
                    "repeat_last",
                    default=False,
                    optional=True,
                    tooltip=memory.REPEAT_CAPTION_TOOLTIP,
                ),
            ],
            outputs=[
                io.String.Output(display_name="reference_assets"),
                io.String.Output(display_name="captions"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        model,
        length,
        seed,
        subjects=None,
        pictures=None,
        videos=None,
        audios=None,
        previous="",
        clip=None,
        options=None,
        enabled_mask="{}",
        reference_instructions="{}",
        max_frames=media.DEFAULT_MAX_FRAMES,
        context_size=mtmd_engine.CONTEXT_FROM_MODEL,
        bypass=False,
        repeat_last=False,
    ) -> io.NodeOutput:
        given = dict(locals())
        progress = NodeProgress(cls.hidden.unique_id)
        block = (previous or "").strip()

        if bypass:
            progress.finish("bypassed")
            return io.NodeOutput(block, "")

        kept = memory.repeat(
            cls.hidden.unique_id, "MiniMaxH3MultiReferenceCaption", repeat_last, given,
            label="captions",
        )
        reused = list(kept or ())

        supplied = {
            "subjects": subjects,
            "pictures": pictures,
            "videos": videos,
            "audios": audios,
        }
        assets, skipped = _collect(supplied, _disabled(enabled_mask))

        if not assets:
            if skipped:
                progress.finish(f"every connected asset is switched off ({skipped}), block unchanged")
                return io.NodeOutput(block, "")
            raise ValueError(
                "Nothing to describe. Connect a reference to one of the four groups: an image to "
                "'subjects' if it defines what something looks like, to 'pictures' if it is an "
                "actual frame of the target video, a clip to 'videos', a sound to 'audios'."
            )

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        if reused and len(reused) != len(assets):
            log.info(
                "[minimax_h3_rewriter.multi_caption] %d caption(s) kept against %d asset(s) "
                "now connected, so the ones without a kept caption are described for real",
                len(reused), len(assets),
            )
        described = len(assets) - len(reused)

        kinds = {asset.kind for asset in assets}
        model_path = mmproj_path = None
        if described > 0 and clip is None:
            choice = _resolve_captioner_choice(model)
            if choice.local:
                model_path, mmproj_path = choice.reference, choice.mmproj
            else:
                model_path, mmproj_path = _ensure_pair(
                    choice.reference, choice.file, choice.mmproj, "Captioner",
                    settings["auto_download"], progress,
                )
            _check_encoders(mmproj_path, kinds)
        elif described > 0:
            clip_caption.check(clip, kinds)

        asked_for = slot_instructions(reference_instructions)

        with mtmd_engine.session(
            model_path or "", mmproj_path or "",
            assets=described,
            attachments=mtmd_engine.busiest(
                (asset.kind for asset in assets), int(max_frames)
            ),
            gpu_layers=int(settings["gpu_layers"]),
            n_ctx=int(context_size),
            device=settings["device"],
            backend=settings["llama_backend"],
            auto_download=settings["auto_download"],
            progress=progress,
        ) as batch:
            progress.set_total(len(assets))
            captions = []
            for done, asset in enumerate(assets):
                index = next_index(block, asset.role)
                caption = reused[done] if done < len(reused) else ""
                frames = asset.kind == "video" and _is_frames(asset.value)
                verb = "reusing the kept caption for" if caption else "reading"
                progress.update(
                    done,
                    f"{asset.role} {index}: {verb} {asset.slot} ({done + 1} of {len(assets)})",
                )

                if not caption:
                    asked = caption_question(asset.role, length, asked_for.get(asset.slot))
                    as_image = asset.value if asset.kind == "image" or frames else None
                    as_audio = asset.value if asset.kind == "audio" else None
                    as_video = None if frames else (asset.value if asset.kind == "video" else None)

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
                            server=batch,
                        )

                caption = " ".join(caption.split())
                captions.append(caption)
                block = f"{block}\n{asset.role} {index}: {caption}".strip()

        if described > 0 or len(reused) != len(assets):
            memory.keep(
                cls.hidden.unique_id, "MiniMaxH3MultiReferenceCaption", tuple(captions),
                given, task="caption",
            )

        fresh = max(described, 0)
        note = f"{fresh} described"
        if fresh < len(assets):
            note += f", {len(assets) - fresh} reused"
        if skipped:
            note += f", {skipped} switched off"
        progress.update(len(assets), f"{note}\n{block}")
        return io.NodeOutput(block, "\n".join(captions))


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3MultiReferenceCaption": MiniMaxH3MultiReferenceCaption,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3MultiReferenceCaption": "MiniMax-H3 Multi Reference Caption",
}
