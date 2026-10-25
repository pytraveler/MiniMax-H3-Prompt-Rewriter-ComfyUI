"""Picking a saved reference out of ``models/refmods``, for a writer's strip.

One entry is a look, a voice, or both -- a bundle holds both in one file, a
pair in two files beside each other -- so the node has an output for each, and
each goes into a square of its own. An entry without a voice leaves that output
empty, which a writer skips like any unconnected socket.

What comes out is the RefMod itself, latent and all, because that is what
'MiniMax-H3 Reference Encode' gives the generator. A writer reads it as what it
decodes to, and the VAEs here are only for that: a file that keeps no pictures
of itself is decoded with ``vae`` once, and a voice with ``audio_vae``, so the
captioner can see it and hear it. Neither is needed to generate -- 'Reference
Encode' decodes a look itself when it has to, and never needs to hear a voice.
"""

from __future__ import annotations

import logging
from fractions import Fraction

from comfy_api.latest import io

from . import refmods
from .nodes import CATEGORY
from .progress import NodeProgress, refuse

log = logging.getLogger(__name__)

DESCRIPTION = (
    "A saved reference -- a RefMod -- from models/refmods, for a writer's strip: a picture, "
    "a clip or a stack of photos on 'visual', a voice on 'audio'. Plug each into a square "
    "of the Universal Writer or the Prompt Rewriter Omni, and their 'references' into "
    "'MiniMax-H3 Reference Encode', which gives the generator the saved latent rather than "
    "encoding anything again.\n\n"
    "Most files carry no pictures of themselves: connect the H3 video VAE to 'vae' and the "
    "look is decoded once, so the writer can read it. 'audio_vae' does the same for a voice."
)

REFMOD_TOOLTIP = (
    "The RefMod to use, by its path under models/refmods. A look and a voice saved together "
    "-- one bundle file, or a pair named <name>_visual and <name>_audio -- are one entry with "
    "both outputs filled. Press R in the graph to list files added since."
)

STRENGTH_TOOLTIP = (
    "How strongly the generator follows this RefMod. 1 is as saved. Lower mixes the latent "
    "toward a heavily blurred copy of itself -- colour, layout and the broad shape stay, the "
    "detail goes -- and the pictures H3's text encoder is shown are weakened the same way. "
    "The writer still reads it at full strength."
)

VAE_TOOLTIP = (
    "The MiniMax-H3 video VAE. Decodes a look whose file keeps no pictures of itself, once, "
    "so a writer can describe it -- which is most files on Hugging Face. Not needed when the "
    "writer is bypassed: 'Reference Encode' decodes it then."
)

AUDIO_VAE_TOOLTIP = (
    "The MiniMax-H3 audio VAE. Decodes a voice so a writer can hear it. Without it the "
    "Universal Writer uses the description saved with the voice, if there is one."
)

VISUAL_TOOLTIP = (
    "The entry's picture, clip or stack of photos, for a square of a writer's strip. Empty "
    "when the entry is a voice alone."
)

AUDIO_TOOLTIP = (
    "The entry's voice, for a square of its own. Empty when the entry has no voice."
)

VIEW_RATE = 2


def view_of(mod: refmods.RefMod):
    """The ordinary ComfyUI value a writer reads for this RefMod, or None.

    A picture is an IMAGE and a voice the AUDIO it decodes to. A clip or a stack
    is a VIDEO, so both writers number it ``<Video N>`` -- which is what the
    generator calls it -- and read it as a clip, of the pictures the encoder is
    shown, at the two a second they were taken at.
    """
    if mod.kind == refmods.AUDIO_KIND:
        return mod.sound
    if mod.pictures is None:
        return None
    if mod.kind == "image":
        return mod.pictures[:1]
    from comfy_api.latest import InputImpl, Types

    return InputImpl.VideoFromComponents(
        Types.VideoComponents(images=mod.pictures, audio=None, frame_rate=Fraction(VIEW_RATE))
    )


class MiniMaxH3RewriterRefMod(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3RewriterRefMod",
            display_name="MiniMax-H3 RefMod",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                io.Combo.Input("refmod", options=refmods.choices(), tooltip=REFMOD_TOOLTIP),
                io.Float.Input(
                    "strength", default=1.0, min=0.0, max=1.0, step=0.05, tooltip=STRENGTH_TOOLTIP
                ),
                io.Vae.Input("vae", optional=True, tooltip=VAE_TOOLTIP),
                io.Vae.Input("audio_vae", optional=True, tooltip=AUDIO_VAE_TOOLTIP),
            ],
            outputs=[
                io.Custom(refmods.TYPE).Output(display_name="visual", tooltip=VISUAL_TOOLTIP),
                io.Custom(refmods.TYPE).Output(display_name="audio", tooltip=AUDIO_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, refmod="", **kwargs):
        """A file rewritten in place is a different RefMod, under the same name."""
        return refmods.stamp(refmod)

    @classmethod
    def validate_inputs(cls, refmod=None):
        if refmod == refmods.NONE_FOUND:
            return (
                "There are no RefMods in models/refmods. Put the .safetensors files there "
                "(subfolders are fine) and press R in the graph."
            )
        if not refmod:
            return (
                "No RefMod is picked. Choose one in 'refmod'; if the list is empty, put the "
                ".safetensors files in models/refmods and press R in the graph."
            )
        if refmods.find(refmod) is None:
            return (
                f"RefMod '{refmod}' is not in models/refmods any more. Pick another entry, or "
                f"press R in the graph if the file was just added."
            )
        return True

    @classmethod
    def execute(cls, refmod, strength=1.0, vae=None, audio_vae=None) -> io.NodeOutput:
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)

        item = refmods.find(refmod)
        if item is None:
            refuse(node_id, f"RefMod '{refmod}' is not in models/refmods any more.")
        try:
            loaded = refmods.load(item, float(strength))
        except (OSError, ValueError, RuntimeError) as error:
            refuse(node_id, f"RefMod '{refmod}' could not be read: {error}")

        for mod in loaded.values():
            try:
                if mod.kind in refmods.VISUAL_KINDS and vae is not None:
                    progress.text(f"decoding the look of '{refmod}'", force=True)
                    refmods.decode_pictures(mod, vae)
                elif mod.kind == refmods.AUDIO_KIND and audio_vae is not None:
                    progress.text(f"decoding the voice of '{refmod}'", force=True)
                    refmods.decode_sound(mod, audio_vae)
            except ValueError as error:
                refuse(node_id, str(error))
            mod.view = view_of(mod)

        lines = [refmods.summary(mod) for mod in loaded.values()]
        log.info("[minimax_h3_rewriter.refmod_node] %s: %s", refmod, "; ".join(lines))
        progress.text("\n".join(lines), force=True)
        return io.NodeOutput(loaded.get("visual"), loaded.get("audio"))


NODE_CLASS_MAPPINGS = {"MiniMaxH3RewriterRefMod": MiniMaxH3RewriterRefMod}

NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3RewriterRefMod": "MiniMax-H3 RefMod"}
