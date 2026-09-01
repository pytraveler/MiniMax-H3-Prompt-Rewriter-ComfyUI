"""Turning one socket carrying many references into one socket each.

The writer nodes take a reference per slot, which is what makes the strip below
them work: every asset has a square, a role and a switch. It is also what makes
them unreachable from a node that hands over its references *together* -- a
batch of images, a list from a directory loader, a bundle assembled by another
pack. Those arrive as one value holding many, and there is no slot shape that
accepts that.

This node is the join. It takes the collected form on one socket and gives back
the separated one, so the writers keep the inputs they have and the strip keeps
working exactly as it does today.

Two things follow from that and are worth stating plainly:

- **It has to be its own node.** Receiving a real ComfyUI list means declaring
  ``is_input_list``, and that flag is not per input: it rewrites the shape of
  *every* argument the node receives. Putting it on a writer would change how
  its prompt, its duration and its options arrive. So the flag lives here, on a
  node that has nothing else to lose.
- **An empty output is not an error.** Nine picture sockets are the most
  Ref2VA can hold, not a number anyone has to fill. Unused ones hand on
  ``None``, which is what ``universal.arrange`` already skips, so wiring a
  socket that turns out empty costs nothing and needs no branch.

The bundle format read here belongs to another pack and is not ours to
guarantee. It is read defensively for that reason: four keys, each optional,
anything unreadable ignored and counted rather than raised. If it changes
shape, this node describes fewer references; nothing else in the pack notices.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

from .nodes import CATEGORY
from .progress import NodeProgress, announce
from .references import (
    BUNDLE_TYPE,
    MAX_AUDIOS,
    MAX_PICTURES,
    MAX_VIDEOS,
    first,
    from_bundle,
    sort_out,
    summarise,
    unpack,
)

log = logging.getLogger(__name__)

DESCRIPTION = (
    "Takes references that arrive together -- an image batch, a list from another node, or "
    "a bundle from another pack -- and hands them back one to a socket, which is the shape "
    "the writer nodes take.\n\n"
    "Nothing is loaded, decoded or described here: the values are passed through as they "
    "arrived, only sorted by kind and separated. Outputs left over are empty, and an empty "
    "one can be wired or not without changing the run.\n\n"
    "Nine pictures, three clips and three sounds are what Ref2VA can hold, so that is what "
    "there is room for. Anything past it is reported on the summary output rather than "
    "silently dropped."
)

ITEMS_TOOLTIP = (
    "References arriving together. An image batch is split into its frames, a list is taken "
    "apart, and a single value is passed through as one reference.\n\n"
    "The socket takes any type because the nodes that produce collections mostly do not "
    "declare one. What each item is gets worked out from the value itself, not from the "
    "wire, so anything that is not an image, a clip or a sound is skipped and counted on "
    "the summary."
)

BUNDLE_TOOLTIP = (
    "A reference bundle from another pack, if you have one. Its pictures, clips and sounds "
    "are read out and placed on the sockets below, ahead of anything on 'items'.\n\n"
    "The audio tracks that come with clips are treated as sounds in their own right, since "
    "that is what they are to a writer."
)

SPLIT_TOOLTIP = (
    "What to do with an image batch: split it into one reference per frame, or keep it as a "
    "single reference made of several frames.\n\n"
    "The difference is real. Split, six frames are six things the video reuses, each "
    "described and numbered separately. Kept, they are one thing seen six times, described "
    "once -- which is what a clip is. Turn it off when the batch is frames of one shot."
)

SUMMARY_TOOLTIP = (
    "What arrived and where it went, including anything skipped or over capacity. Wire it "
    "to a preview when a reference is not turning up where you expected."
)


class MiniMaxH3ReferenceAdapter(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3ReferenceAdapter",
            display_name="MiniMax-H3 Reference Adapter",
            category=CATEGORY,
            description=DESCRIPTION,
            is_input_list=True,
            inputs=[
                io.AnyType.Input("items", optional=True, tooltip=ITEMS_TOOLTIP),
                io.Custom(BUNDLE_TYPE).Input("bundle", optional=True,
                                             tooltip=BUNDLE_TOOLTIP),
                io.Boolean.Input("split_batches", default=True, tooltip=SPLIT_TOOLTIP),
            ],
            outputs=[
                *(io.Image.Output(display_name=f"picture_{number}")
                  for number in range(1, MAX_PICTURES + 1)),
                *(io.Video.Output(display_name=f"video_{number}")
                  for number in range(1, MAX_VIDEOS + 1)),
                *(io.Audio.Output(display_name=f"audio_{number}")
                  for number in range(1, MAX_AUDIOS + 1)),
                io.String.Output(display_name="summary", tooltip=SUMMARY_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, items=None, bundle=None, split_batches=True):
        node_id = first(cls.hidden.unique_id)
        progress = NodeProgress(node_id)

        collected, unreadable = from_bundle(first(bundle))
        for value in (items if isinstance(items, list) else [items]):
            if value is not None:
                collected.extend(unpack(value))

        sorted_out, skipped, over = sort_out(collected, bool(first(split_batches)))
        summary, warning = summarise(sorted_out, skipped, over, unreadable)

        if warning:
            log.warning("[minimax_h3_rewriter.reference_adapter] %s", warning)
            announce(node_id, [("warn", warning)])
        progress.text(summary, force=True)

        pictures = sorted_out["image"] + [None] * MAX_PICTURES
        videos = sorted_out["video"] + [None] * MAX_VIDEOS
        audios = sorted_out["audio"] + [None] * MAX_AUDIOS
        return io.NodeOutput(
            *pictures[:MAX_PICTURES],
            *videos[:MAX_VIDEOS],
            *audios[:MAX_AUDIOS],
            summary,
        )


NODE_CLASS_MAPPINGS = {"MiniMaxH3ReferenceAdapter": MiniMaxH3ReferenceAdapter}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3ReferenceAdapter": "MiniMax-H3 Reference Adapter",
}
