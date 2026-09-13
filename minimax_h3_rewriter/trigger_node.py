"""The node that puts LoRA trigger words into a finished prompt.

It is a list and an insertion, and the whole of the work is that the list
survives: it is kept with the workflow, so a graph reopened next week still
knows which words its adapters answer to. Everything this pack does upstream is
a model writing prose, and a trigger word is the one thing in the prompt that
is not prose -- it is a key, and the model has no reason to keep it.

What it will not do is take anything out. ``triggers.py`` carries the reasoning
for that, which is the one thing here worth reading twice.
"""

from __future__ import annotations

from comfy_api.latest import io

from .nodes import CATEGORY
from .progress import NodeProgress, announce
from .triggers import SEPARATOR, entries, insert

WHAT_IT_IS = (
    "Adds LoRA trigger words to a finished prompt, from a list kept on the node: a tick, "
    "a name, the words themselves, and which part of the prompt each one goes into.\n\n"
    "Some LoRA adapters only do anything when their trigger word is in the text -- it is "
    "the word their training captions were prefixed with. Nothing upstream knows that. The "
    "writers write prose, the reducer shortens it, the self-check judges it, and a word "
    "that exists to wake an adapter three nodes later is exactly the sort of thing all "
    "three drop."
)

WHY = (
    "Without this the word gets typed in by hand after every run, and lost on the next "
    "one. It is the only place in the pipeline where finished text has to be edited by "
    "hand, and the edit is the one part of the prompt that cannot be regenerated.\n\n"
    "Put it at the very end of the graph: after the reducer and after 'MiniMax-H3 Prompt "
    "Check'. Triggers add words, and the check counts words against a ceiling -- a node "
    "that lengthens the text after it has been measured is fine, one that lengthens it "
    "before is arguing with the measurement. Anything that rewrites the prompt downstream "
    "of this will drop the words again."
)

PROMPT_TOOLTIP = (
    "The prompt to put the words into. Anything: a writer node's output, a reducer's, a "
    "loaded file, something typed here.\n\n"
    "It is passed through character for character apart from the inserted words -- nothing "
    "is re-wrapped, re-labelled or rebuilt from parsed fields."
)

TRIGGERS_TOOLTIP = (
    "The list, drawn by this pack's own interface. One row is one adapter: a grip, a tick, "
    "a name for your own use, the words that go into the prompt verbatim, where they go, "
    "and a cross to remove the row.\n\n"
    "A row can hold several words separated by commas, which is what an adapter with more "
    "than one trigger needs. They are checked and added one at a time, so a row is never "
    "half-duplicated. Triggers that want different parts of the prompt want different "
    "rows.\n\n"
    "Drag a row by its grip to move it. The order is not decoration: rows sharing a "
    "placement are written into the prompt in list order.\n\n"
    "The list is saved with the workflow. Nothing is read out of the LoRA files: the word "
    "is almost never in one, and where it is there is room for a single word.\n\n"
    "Driving this from the API, a plain JSON array of strings is read as a list of trigger "
    "words, and anything that is not JSON at all is read as one row's worth of words."
)

PROMPT_OUT_TOOLTIP = (
    "The prompt with the words in it.\n\n"
    "Running it twice does not stack them, and it does not need to take anything out to "
    "manage that: a word already in the text is simply not added again."
)

ADDED_TOOLTIP = (
    "The words this run actually put in, comma separated, in list order.\n\n"
    "Empty when every switched-on trigger was already in the prompt, which is the normal "
    "state on a second run of the same text. Route it into a preview to see at a glance "
    "whether the adapter is going to hear anything."
)

FINDINGS_TOOLTIP = (
    "What the node did and what it noticed, one per line -- words that were already there, "
    "a placement this particular prompt has no field for.\n\n"
    "Empty when there is nothing to say. Route it into a text preview to keep it in sight."
)

BYPASS_TOOLTIP = (
    "Hand 'prompt' straight to the output and add nothing at all. The text goes out exactly "
    "as it came in, character for character; 'added' and 'findings' come back empty.\n\n"
    "ComfyUI's own bypass (Ctrl+B) is not the same thing here, and it is worth knowing why "
    "before reaching for it. It stands an input in for each output by matching types, and "
    "this node has three outputs and one socket to fill them from. Measured on this node: "
    "'prompt' arrives, 'findings' is handed the whole prompt as though it were a finding, "
    "and 'added' loses its link altogether -- which leaves whatever it fed with no input at "
    "all. This switch is the one that does what it says."
)

PLACEMENT_NOTE = (
    "Where a row's words go. 'start of the description' opens the description field "
    "itself, after its label, which is the field the writers put the scene in and the one "
    "every downstream node reads -- and the position a caption-prefix trigger was trained "
    "in. 'start of the soundscape' and 'start of the music' open the other two fields the "
    "same way. 'top of the prompt' and 'end of the prompt' are positions rather than "
    "fields, so they are the two that still work on a text carrying no labels at all.\n\n"
    "A prompt that has not got the field a row asks for falls back to the top and says so. "
    "A field that is there but says 'N/A' gets a note of its own: the words go in, nothing "
    "is taken out, and the node points out that the field now names something and then "
    "says there is none of it.\n\n"
    "Unlike the effect embeddings, ordinary words do not flatten the line breaks below "
    "them -- ComfyUI's tokenizer only does that after an 'embedding:' token -- so the top "
    "of the prompt is a safe place here."
)


class MiniMaxH3LoraTriggers(io.ComfyNode):
    """Add LoRA trigger words to a prompt."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LoraTriggers",
            display_name="MiniMax-H3 LoRA Triggers",
            category=CATEGORY,
            description=WHAT_IT_IS + "\n\n" + WHY + "\n\n" + PLACEMENT_NOTE,
            inputs=[
                io.String.Input("prompt", multiline=True, tooltip=PROMPT_TOOLTIP),
                io.String.Input(
                    "triggers",
                    default="[]",
                    optional=True,
                    tooltip=TRIGGERS_TOOLTIP,
                ),
                io.Boolean.Input(
                    "bypass", default=False, optional=True, tooltip=BYPASS_TOOLTIP
                ),
            ],
            outputs=[
                io.String.Output(display_name="prompt", tooltip=PROMPT_OUT_TOOLTIP),
                io.String.Output(display_name="added", tooltip=ADDED_TOOLTIP),
                io.String.Output(display_name="findings", tooltip=FINDINGS_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, prompt, triggers="[]", bypass=False):
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)

        if bypass:
            progress.finish("bypassed")
            return io.NodeOutput(str(prompt or ""), "", "")

        rows = entries(triggers)
        text, added, findings = insert(prompt, rows)

        live = [row for row in rows if row.on]
        if not live:
            progress.finish("no triggers switched on")
        elif added:
            progress.finish(f"{len(added)} added\n{SEPARATOR.join(added)}")
        else:
            progress.finish(f"{len(live)} on, all of them already in the prompt")

        announce(node_id, [entry for entry in findings if entry[0] == "warn"])

        return io.NodeOutput(
            text,
            SEPARATOR.join(added),
            "\n".join(message for _level, message in findings),
        )


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3LoraTriggers": MiniMaxH3LoraTriggers,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3LoraTriggers": "MiniMax-H3 LoRA Triggers",
}
