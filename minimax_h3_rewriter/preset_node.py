"""A thousand finished prompts, pickable from the graph.

Every other node here writes a prompt; this one only remembers which of the
bundled ones was chosen and hands it on. No model is loaded, nothing is
generated, and nothing is fetched: the collection ships inside the pack, and a
run costs about as long as reading a dictionary.

What it is for is the two things a person does with somebody else's prompt.
Wire ``prompt`` at the generator and it is used as it stands; wire it into a
writer's ``prompt`` and it is the starting point for a rewrite. That choice is
a wire rather than a widget, which is why this is a node of its own rather than
a second button inside the library window.

The picker, the tags and the clip preview live in ``web/js/prompt_presets.js``;
everything this file knows about the collection it asks ``presets.py`` for.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

from . import presets
from .nodes import CATEGORY
from .progress import refuse

log = logging.getLogger(__name__)

DESCRIPTION = (
    "Hands on one of the thousand MiniMax-H3 prompts that ship with the pack. Pick one in "
    "the browser -- filtered by shooting style, subject, shape or words, with the frame of "
    "the clip it was written for -- and its text comes out ready to use.\n\n"
    "They are finished T2VA prompts in the format the writers here produce, so 'prompt' can "
    "go straight to the generator, or into a writer's own prompt input to be rewritten from. "
    "No model is loaded and nothing is downloaded during a run.\n\n" + presets.NOTICE
)

PRESET_TOOLTIP = (
    "Which bundled prompt this node hands on, as its number in the collection. The 'Pick a "
    "preset' button writes it; it is stored in the workflow, so a graph shared with somebody "
    "else resolves to the same prompt on their machine.\n\n"
    "Editing it by hand works too, if you know the number you want."
)

PROMPT_TOOLTIP = (
    "The whole prompt, the three fields with their labels, exactly as a writer in this pack "
    "would have produced it. This is the output to use unless you want the parts separately."
)

SECONDS_TOOLTIP = (
    "How long the clip this prompt was written for runs. Every one of them is about five "
    "seconds, and the shot times inside the text are written against that -- so a video "
    "generated much longer than this will have nothing described for its last half."
)

SOURCE_TOOLTIP = (
    "Where this prompt came from: its number, both addresses the clip can be watched at, "
    "and who is owed the credit. Wire it into a text preview or a save node when a workflow "
    "is going somewhere else."
)


class MiniMaxH3PromptPresets(io.ComfyNode):
    """One of the bundled prompts, chosen in the browser and handed on."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromptPresets",
            display_name="MiniMax-H3 Prompt Presets",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                io.String.Input("preset", default="", tooltip=PRESET_TOOLTIP),
            ],
            outputs=[
                io.String.Output(display_name="prompt", tooltip=PROMPT_TOOLTIP),
                *(io.String.Output(display_name=name) for name in presets.FIELDS),
                io.Float.Output(display_name="seconds", tooltip=SECONDS_TOOLTIP),
                io.Int.Output(display_name="width"),
                io.Int.Output(display_name="height"),
                io.String.Output(display_name="source", tooltip=SOURCE_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, preset="", **kwargs):
        """Whether the prompt this node would hand on has changed.

        The number alone would not say: a rebuilt collection keeps its numbers
        and can change every word behind them, and nothing else on this node
        moves. So the collection's own build time is in the fingerprint too.
        """
        return presets.stamp(preset)

    @classmethod
    def execute(cls, preset):
        node_id = cls.hidden.unique_id
        wanted = str(preset or "").strip()
        if not wanted:
            refuse(
                node_id,
                "No preset is chosen. Press 'Pick a preset' on this node and choose one.",
            )

        record = presets.find(wanted)
        if record is None:
            refuse(
                node_id,
                f"There is no preset '{wanted}' in this pack."
                + (
                    ""
                    if presets.catalog()
                    else " The bundled prompts are not installed: this copy of the pack has no"
                    " 'presets' folder."
                ),
            )

        made = presets.outputs(record)
        log.info(
            "[minimax_h3_rewriter.preset_node] handing on %s, %d characters",
            presets.label(record), len(made[0]),
        )
        return io.NodeOutput(*made)


NODE_CLASS_MAPPINGS = {"MiniMaxH3PromptPresets": MiniMaxH3PromptPresets}

NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3PromptPresets": "MiniMax-H3 Prompt Presets"}
