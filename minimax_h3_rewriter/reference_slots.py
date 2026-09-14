"""Handing a writer's references on in the order its prompt numbers them.

MiniMaxH3ReferenceToVideo numbers its references by the socket they arrive on:
the first picture socket is ``<Picture 1>``, the first sound socket
``<Audio 1>``. The writers number theirs by the strip, which can be dragged.
Wire the same assets to both by hand and the two orders agree only until
somebody drags a square -- after which the prompt describes one voice, the
video is given another, and nothing anywhere says so.

So the writers hand on what they numbered, as one value, and this node puts
each reference on the socket carrying its number. A square switched off in the
strip is not in the value at all, so its socket hands on nothing, and the far
node closes up its numbering around the gap exactly as the writer did.

Two things are worth stating plainly:

- **It is not the Reference Adapter.** That node feeds the writers: it sorts
  whatever arrives by kind and hands a clip on as a VIDEO, because a VIDEO is
  what a writer describes. This one feeds the generator, which takes a clip as
  frames and a clip's sound on a socket of its own. Same three kinds, opposite
  direction, a different type on every clip socket -- one node doing both
  would be two nodes behind a switch.
- **Clips are decoded here and nowhere earlier.** A writer runs whether or not
  anything is wired to its 'references' output, and fifteen seconds of 1080p
  as float frames is gigabytes. Carried as the VIDEO it arrived as, a clip
  costs nothing until this node runs.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

from . import media
from .nodes import CATEGORY
from .progress import NodeProgress, announce
from .references import MAX_AUDIOS, MAX_PICTURES, MAX_VIDEOS, SLOTS_TYPE, fan_out

log = logging.getLogger(__name__)

DESCRIPTION = (
    "Takes the references a writer numbered and puts each one on the socket carrying its "
    "number: picture_2 here is <Picture 2> in the prompt. Wire a writer's 'references' output "
    "in, and these outputs straight across to MiniMaxH3ReferenceToVideo in the same order -- "
    "picture_1 to its first ref_image, video_1 to its first ref_video, and so on.\n\n"
    "A reference switched off in the writer's strip is not handed on, and that node skips an "
    "empty socket, so its numbering closes up the same way the writer's did.\n\n"
    f"Clips are decoded here, at {media.REFERENCE_FPS} fps on that node's own canvas and up to "
    f"{media.REFERENCE_MAX_SECONDS:g} seconds. Pictures and sounds pass through as they came."
)

REFERENCES_TOOLTIP = (
    "The 'references' output of a Universal Writer, a Prompt Rewriter Omni or a Universal "
    "Rewriter: what that node numbered, in its order."
)

SOUNDTRACKS_TOOLTIP = (
    "Put each clip's own sound on video_audio_N, paired with video_N.\n\n"
    "Off by default, because it renumbers the sounds. MiniMaxH3ReferenceToVideo gives a clip's "
    "sound an <Audio N> of its own, numbered before every standalone sound, and the writers "
    "do not count clip sounds -- so with this on, the prompt's <Audio 1> is no longer the "
    "sound on audio_1. Turn it on when the prompt was written with that in mind; the summary "
    "says how far the labels move."
)

SUMMARY_TOOLTIP = (
    "What went on which output, and where each came from in the writer. Wire it to a preview "
    "when a reference is not where the prompt says it is."
)


class MiniMaxH3ReferenceSlots(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3ReferenceSlots",
            display_name="MiniMax-H3 Reference Slots",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                io.Custom(SLOTS_TYPE).Input("references", tooltip=REFERENCES_TOOLTIP),
                io.Boolean.Input("soundtracks", default=False, tooltip=SOUNDTRACKS_TOOLTIP),
            ],
            outputs=[
                *(io.Image.Output(display_name=f"picture_{number}")
                  for number in range(1, MAX_PICTURES + 1)),
                *(io.Image.Output(display_name=f"video_{number}")
                  for number in range(1, MAX_VIDEOS + 1)),
                *(io.Audio.Output(display_name=f"video_audio_{number}")
                  for number in range(1, MAX_VIDEOS + 1)),
                *(io.Audio.Output(display_name=f"audio_{number}")
                  for number in range(1, MAX_AUDIOS + 1)),
                io.String.Output(display_name="summary", tooltip=SUMMARY_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, references=None, soundtracks=False):
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)

        with media.Workspace(prefix="minimax_h3_slots_") as workspace:
            decoded = 0

            def clip(video, with_sound):
                nonlocal decoded
                decoded += 1
                progress.text(
                    f"decoding clip {decoded} at {media.REFERENCE_FPS} fps", force=True
                )
                frames, sound, seconds = media.reference_clip(
                    video, workspace, soundtrack=with_sound
                )
                detail = f"{int(frames.shape[0])} frames at {media.REFERENCE_FPS} fps, {seconds:.2f}s"
                if with_sound and sound is None:
                    detail += ", no sound track"
                return frames, sound, detail

            slots, summary, warning = fan_out(references, bool(soundtracks), clip)

        if warning:
            log.warning("[minimax_h3_rewriter.reference_slots] %s", warning)
            announce(node_id, [("warn", warning)])
        progress.text(summary, force=True)

        return io.NodeOutput(
            *slots["pictures"],
            *slots["videos"],
            *slots["video_audios"],
            *slots["audios"],
            summary,
        )


NODE_CLASS_MAPPINGS = {"MiniMaxH3ReferenceSlots": MiniMaxH3ReferenceSlots}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3ReferenceSlots": "MiniMax-H3 Reference Slots",
}
