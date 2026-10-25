"""The generator's end of a writer's references, RefMods included.

'Reference Slots' hands a writer's references to MiniMaxH3ReferenceToVideo one
to a socket, and that node encodes each. A RefMod cannot go that way: it is a
latent already, and what the DiT needs of it is that latent as a reference
block, while H3's text encoder needs its pictures. So this node does what those
two do together, and for a RefMod does the one thing neither can -- presents
the saved latent in its place.

Order is the whole contract. H3's DiT has no index for a reference: a block is
matched to its label by where it sits, the text encoder's ``<Picture 1>`` to
the first image block and so on. Everything here therefore keeps
MiniMaxH3ReferenceToVideo's order exactly -- every picture, then every clip with
its own sound just ahead of it, then every standalone sound -- and a RefMod
takes the place in it that the writer numbered.

An ordinary reference is prepared with that node's own helpers, sizing and
cutting included, so a workflow without a RefMod in it encodes to the same
conditioning it did through 'Reference Slots'.
"""

from __future__ import annotations

import logging
import math

from comfy_api.latest import io

from . import media, refmods
from .nodes import CATEGORY
from .progress import NodeProgress, announce, refuse
from .references import SLOTS_TYPE, fan_out

log = logging.getLogger(__name__)

MAX_RESOLUTION = 16384

DESCRIPTION = (
    "Encodes the prompt for MiniMax-H3 with a writer's references, RefMods included: wire the "
    "writer's 'references' and its prompt in, and 'positive' and 'latent' on to the sampler. "
    "It stands in for 'Reference Slots' and MiniMaxH3ReferenceToVideo together and takes the "
    "same settings.\n\n"
    "A picture, a clip or a sound is prepared exactly as MiniMaxH3ReferenceToVideo prepares it. "
    "A RefMod is given to the generator as the latent it saved, in the place the writer "
    "numbered, and H3's text encoder is shown its pictures."
)

VAE_TOOLTIP = (
    "The MiniMax-H3 video VAE. Encodes pictures and clips, and decodes a RefMod that keeps no "
    "pictures of itself when its RefMod node had none. Without it, pictures and clips reach "
    "the text encoder only -- MiniMaxH3ReferenceToVideo's own rule -- while a RefMod still "
    "reaches the generator."
)

AUDIO_VAE_TOOLTIP = (
    "The MiniMax-H3 audio VAE. Encodes sounds and clip soundtracks; without it they reach "
    "the text encoder only. A voice RefMod needs none."
)

LENGTH_TOOLTIP = (
    "Frame count at 24 fps, snapped up to the model's 17k+5 grid (124 = ~5 s). Reference "
    "clips are cut to it, as MiniMaxH3ReferenceToVideo cuts them."
)

SIZE_TOOLTIP = (
    "Reference picture sizing, as on MiniMaxH3ReferenceToVideo: 'match' scales each picture "
    "down to the generation's pixel area, 'max' keeps up to a 2048 px short edge for "
    "identity, at a cost in speed. A RefMod keeps the size it was saved at."
)

REFERENCES_TOOLTIP = (
    "The 'references' output of a Universal Writer, a Prompt Rewriter Omni or a Universal "
    "Rewriter: what that node numbered, in its order. Leave it empty to encode the prompt "
    "alone."
)

SOUNDTRACKS_TOOLTIP = (
    "Give every clip its own sound, whatever the writer did -- the same switch as on "
    "'Reference Slots'. Off, a clip's sound goes in exactly when the writer counted it. A "
    "clip the writer left silent and this gives a sound to moves the prompt's <Audio N> "
    "labels, and the summary says by how many."
)

SUMMARY_TOOLTIP = (
    "What went in, in the order the generator reads it, and what it costs. Wire it to a "
    "preview when a reference is not where the prompt says it is."
)


def _tokens(found: dict) -> int:
    """What one reference block costs the DiT, in tokens, at every step."""
    if found["kind"] == "audio":
        return 2 * int(found["ref_audio_t"])
    frames = int(found.get("latent_t", 1))
    cost = frames * ((found["latent_h"] + 1) // 2) * ((found["latent_w"] + 1) // 2)
    return cost + 2 * int(found.get("ref_audio_t") or 0)


def _h3_tokenizer(clip) -> bool:
    import inspect

    tokenizer = getattr(clip, "tokenizer", None)
    if type(tokenizer).__name__ == "MiniMaxH3Tokenizer":
        return True
    try:
        return "minimax_ref_items" in inspect.signature(tokenizer.tokenize_with_weights).parameters
    except (AttributeError, TypeError, ValueError):
        return False


class MiniMaxH3ReferenceEncode(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3ReferenceEncode",
            display_name="MiniMax-H3 Reference Encode",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                io.Clip.Input("clip"),
                io.Vae.Input("vae", optional=True, tooltip=VAE_TOOLTIP),
                io.Vae.Input("audio_vae", optional=True, tooltip=AUDIO_VAE_TOOLTIP),
                io.String.Input("prompt", multiline=True, dynamic_prompts=True),
                io.Int.Input("width", default=1344, min=32, max=MAX_RESOLUTION, step=32),
                io.Int.Input("height", default=768, min=32, max=MAX_RESOLUTION, step=32),
                io.Int.Input(
                    "length", default=124, min=5, max=3600, step=17, tooltip=LENGTH_TOOLTIP
                ),
                io.Combo.Input(
                    "ref_image_size", options=["match", "max"], default="match",
                    tooltip=SIZE_TOOLTIP,
                ),
                io.Custom(SLOTS_TYPE).Input("references", optional=True, tooltip=REFERENCES_TOOLTIP),
                io.Boolean.Input("soundtracks", default=False, tooltip=SOUNDTRACKS_TOOLTIP),
            ],
            outputs=[
                io.Conditioning.Output(display_name="positive"),
                io.Latent.Output(),
                io.String.Output(display_name="summary", tooltip=SUMMARY_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls, clip, prompt, width, height, length, ref_image_size="match", vae=None,
        audio_vae=None, references=None, soundtracks=False,
    ) -> io.NodeOutput:
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)
        try:
            import node_helpers
            from comfy_extras import nodes_minimax_h3 as h3
        except ImportError:
            refuse(
                node_id,
                "This ComfyUI has no MiniMax-H3 support (comfy_extras.nodes_minimax_h3). "
                "Update ComfyUI.",
            )
        if not _h3_tokenizer(clip):
            refuse(
                node_id,
                "The CLIP connected is not MiniMax-H3's text encoder, which is the only one that "
                "can be shown references. Load the H3 text encoder with a CLIP loader set to "
                "minimax_h3.",
            )

        latent, frame_count = h3._empty_av_latent(width, height, length)
        items: list[dict] = []
        blocks: list[dict] = []
        notes: list[str] = []
        unencoded = {"visual": 0, "audio": 0}

        def present(mod: refmods.RefMod) -> None:
            if mod.kind in refmods.VISUAL_KINDS and mod.pictures is None:
                if vae is None:
                    refuse(
                        node_id,
                        f"RefMod '{mod.name}' keeps no pictures of itself, and H3's text encoder "
                        f"has to be shown it as pictures. Connect the MiniMax-H3 video VAE to "
                        f"'vae' here, or to that RefMod node's 'vae'.",
                    )
                progress.text(f"decoding RefMod '{mod.name}'", force=True)
                refmods.decode_pictures(mod, vae)
            items.append(refmods.encoder_item(mod))
            blocks.append(refmods.block(mod))

        with media.Workspace(prefix="minimax_h3_encode_") as workspace:
            decoded = 0

            def clip_frames(video, with_sound):
                nonlocal decoded
                decoded += 1
                progress.text(f"decoding clip {decoded} at {media.REFERENCE_FPS} fps", force=True)
                frames, sound, seconds = media.reference_clip(video, workspace, soundtrack=with_sound)
                detail = f"{int(frames.shape[0])} frames at {media.REFERENCE_FPS} fps, {seconds:.2f}s"
                if with_sound and sound is None:
                    detail += ", no sound track"
                return frames, sound, detail

            slots, summary, warning = fan_out(references or {}, bool(soundtracks), clip_frames)

        for picture in slots["pictures"]:
            if picture is None:
                continue
            if refmods.is_refmod(picture):
                present(picture)
                continue
            h, w = picture.shape[1], picture.shape[2]
            if ref_image_size == "match":
                scale = min(1.0, math.sqrt((width * height) / (w * h)))
            else:
                scale = min(1.0, h3.REF_IMAGE_SHORT_EDGE / min(w, h))
            tw = max(h3.CANVAS_MULTIPLE, round(w * scale / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
            th = max(h3.CANVAS_MULTIPLE, round(h * scale / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
            resized = h3._resize(picture[:1], tw, th, "disabled")
            items.append({"type": "image", "data": resized})
            if vae is None:
                unencoded["visual"] += 1
                continue
            blocks.append({"kind": "image", "latent_h": th // 16, "latent_w": tw // 16,
                           "latent": vae.encode(resized)})

        for position, frames in enumerate(slots["videos"]):
            if frames is None:
                continue
            if refmods.is_refmod(frames):
                present(frames)
                continue
            soundtrack = slots["video_audios"][position]
            vh, vw = frames.shape[1], frames.shape[2]
            cw, ch = h3.adapt_canvas(vw, vh)
            if vw * vh < cw * ch:
                cw = max(h3.CANVAS_MULTIPLE, round(vw / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
                ch = max(h3.CANVAS_MULTIPLE, round(vh / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
            frames = h3._resize(frames, cw, ch, "disabled")
            if frames.shape[0] > frame_count:
                frames = frames[:frame_count]
            count = frames.shape[0]
            if count < 5:
                refuse(
                    node_id,
                    f"video_{position + 1} has {count} frame(s), and MiniMax-H3 needs at least 5 "
                    f"of a reference clip (about 0.2 s at 24 fps).",
                )
            while count % 17 != 5:
                count -= 1
            frames = frames[:count]
            if soundtrack is not None:
                items.append({"type": "audio"})
            sample = list(range(0, frames.shape[0], h3.FPS // 2))
            items.append({"type": "video", "data": frames[sample],
                          "timestamps": [index / 2.0 for index in range(len(sample))]})
            if vae is None:
                unencoded["visual"] += 1
                continue
            encoded = vae.encode(frames)
            sound, sound_t = None, 0
            if soundtrack is not None and audio_vae is not None:
                sound, sound_t = h3._encode_ref_audio(audio_vae, soundtrack)
            elif soundtrack is not None:
                unencoded["audio"] += 1
            blocks.append({"kind": "video_audio" if sound_t else "video",
                           "latent_t": encoded.shape[2], "latent_h": ch // 16, "latent_w": cw // 16,
                           "ref_audio_t": sound_t, "latent": encoded, "audio_latent": sound})

        for sound in slots["audios"]:
            if sound is None:
                continue
            if refmods.is_refmod(sound):
                present(sound)
                continue
            items.append({"type": "audio"})
            if audio_vae is None:
                unencoded["audio"] += 1
                continue
            encoded, encoded_t = h3._encode_ref_audio(audio_vae, sound)
            blocks.append({"kind": "audio", "ref_audio_t": encoded_t, "audio_latent": encoded})

        progress.text("encoding the prompt", force=True)
        tokens = clip.tokenize(prompt, minimax_ref_items=items)
        conditioning = clip.encode_from_tokens_scheduled(tokens)
        if blocks:
            conditioning = node_helpers.conditioning_set_values(conditioning, {"minimax_refs": blocks})

        if unencoded["visual"]:
            notes.append(
                f"{unencoded['visual']} picture(s) or clip(s) reach the text encoder only: no "
                f"'vae' is connected to encode them for the generator."
            )
        if unencoded["audio"]:
            notes.append(
                f"{unencoded['audio']} sound(s) reach the text encoder only: no 'audio_vae' is "
                f"connected to encode them for the generator."
            )
        held = sum(1 for group in ("pictures", "videos", "audios")
                   for value in slots[group] if refmods.is_refmod(value))
        cost = sum(_tokens(found) for found in blocks)
        head = (
            f"{len(blocks)} reference block(s) for the generator, {cost:,} tokens at every step"
            + (f", {held} of them RefMod(s)" if held else "")
        )
        if warning:
            notes.insert(0, warning)
        if notes:
            log.warning("[minimax_h3_rewriter.reference_encode] %s", " ".join(notes))
            announce(node_id, [("warn", note) for note in notes])
        text = "\n".join([head, *notes, summary])
        progress.text(text, force=True)
        return io.NodeOutput(conditioning, latent, text)


NODE_CLASS_MAPPINGS = {"MiniMaxH3ReferenceEncode": MiniMaxH3ReferenceEncode}

NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3ReferenceEncode": "MiniMax-H3 Reference Encode"}
