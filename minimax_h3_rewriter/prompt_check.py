"""Reading a finished prompt that was written somewhere else.

Every other node here writes a prompt and then checks what it wrote. This one
only checks, and takes the text on a socket -- so a prompt that arrived from a
different node, a text file, a clipboard or your own typing is read by exactly
the rules a rewrite from this pack is read by, and split into the same fields.

That is the whole of it. No model is loaded, no weights move, nothing is
generated: the run costs a few milliseconds, and what comes out the first
output is what went in.

The findings output always carries everything the rules found. ``self_check``
on the Options node governs what the nodes announce during a run, which is a
question about noise; wiring this output is asking, and an answer that had been
quietly trimmed would be worse than none.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

from . import checks, guide_prompt, memory, snapshot
from .constants import duration_options, duration_tooltip
from .fields import split_sections
from .nodes import CATEGORY, DEFAULT_OPTIONS, OPTIONS_TYPE
from .progress import NodeProgress, announce
from .universal import ALL_FIELDS, MAX_REFERENCES, TASKS, kind_of

log = logging.getLogger(__name__)

DESCRIPTION = (
    "Reads a finished MiniMax-H3 prompt against the rules the guides are written by, and "
    "splits it into its fields. The text arrives on a socket, so a prompt written outside "
    "this pack -- by another node, or by hand -- gets the same reading as one written "
    "inside it: shot numbering, cut times against the duration, dialogue markup, reference "
    "tags against what the task can take and what is actually connected.\n\n"
    "No model is loaded and nothing is generated. What goes in comes out of the first "
    "output unchanged; what the rules found comes out of the last one, and is announced on "
    "screen the way a rewrite's own findings are."
)

PROMPT_TOOLTIP = (
    "The prompt to read. Anything that produces MiniMax-H3 prose can feed this: another "
    "node's output, a loaded text file, a rewrite from this pack, or something typed here.\n\n"
    "It is passed through untouched. This node never edits what it is given -- it says what "
    "it found and hands the text on, so it can sit in the middle of a graph without changing "
    "what reaches the generator."
)

TASK_TOOLTIP = (
    "Which task the prompt was written for. It decides which fields the answer is supposed "
    "to have, how many references of each kind it may cite, and whether an alignment line "
    "is expected at the top.\n\n"
    "Getting this wrong makes the reading wrong rather than absent: a Ref2VA prompt read as "
    "T2VA is reported as missing three fields it never needed."
)

DURATION_TOOLTIP = duration_tooltip(
    "How long the target video is, in seconds. Cut times are read against it: a shot that "
    "starts after the end is the one mistake in a hand-written shot list that nothing else "
    "catches.\n\n"
    "Set it to what the prompt was written for, which is not necessarily what this pack's "
    "own writers were asked for -- prompts collected from elsewhere are often longer."
)

REFERENCES_TOOLTIP = (
    "The references this prompt is meant to describe, if you have them to hand. Only their "
    "kind and number are read -- nothing is decoded and no captioner runs.\n\n"
    "With them connected, the reading also covers what the text cites against what is "
    "actually here: a picture that is connected but never mentioned, or a clip mentioned "
    "that never arrived. With nothing connected those two rules are skipped and the rest "
    "still apply."
)

FINDINGS_TOOLTIP = (
    "Everything the rules found, as the same block the node writes under itself: a heading "
    "with the counts, then one line each, '!' for a warning and '-' for a note. Empty when "
    "there is nothing to say."
)


class MiniMaxH3PromptCheck(io.ComfyNode):
    """Read a prompt written anywhere, by the rules H3 actually reads by."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromptCheck",
            display_name="MiniMax-H3 Prompt Check",
            category=CATEGORY,
            description=DESCRIPTION,
            inputs=[
                io.Custom(OPTIONS_TYPE).Input("options", optional=True),
                io.String.Input("prompt", multiline=True, tooltip=PROMPT_TOOLTIP),
                io.Combo.Input(
                    "task", options=list(TASKS), default="T2VA", tooltip=TASK_TOOLTIP
                ),
                io.MultiType.Input(
                    io.Float.Input("duration", **duration_options(DURATION_TOOLTIP)),
                    types=[io.Int],
                ),
                io.Autogrow.Input(
                    "references",
                    optional=True,
                    tooltip=REFERENCES_TOOLTIP,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.MultiType.Input(
                            "ref",
                            types=[io.Image, io.Video, io.Audio],
                            tooltip=(
                                "An image or batch of frames, a clip, or a sound. Only its "
                                "kind is read."
                            ),
                        ),
                        prefix="ref_",
                        min=0,
                        max=MAX_REFERENCES,
                    ),
                ),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
                *(io.String.Output(display_name=name) for name in ALL_FIELDS),
                io.String.Output(display_name="findings", tooltip=FINDINGS_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, prompt, task, duration, options=None, references=None):
        given = dict(locals())
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        text = str(prompt or "")
        names = guide_prompt.FIELDS_FOR_MODE[task]
        _head, sections = split_sections(
            text, names, fallback=guide_prompt.BODY_FIELD[task]
        )

        connected = [value for value in (references or {}).values() if value is not None]
        having = [kind_of(value) for value in connected] if connected else None

        issues = checks.review(
            text, sections, names, task=task, duration=duration, having=having
        )
        note = checks.describe(issues)
        told = checks.reportable(issues, settings.get("self_check", checks.REPORT_ALL))
        if told:
            announce(node_id, told, kind="check")
        if note:
            log.info("[minimax_h3_rewriter.prompt_check] %s", note.replace("\n", " | "))

        progress.text(
            (note or "self-check: nothing to report")
            + "\n\n"
            + (text[-2000:] or "(nothing on the prompt input)"),
            force=True,
        )

        outputs = (
            (text,)
            + tuple(sections.get(name, "") for name in ALL_FIELDS)
            + (note,)
        )

        memory.keep(
            node_id,
            "MiniMaxH3PromptCheck",
            outputs[:-1],
            given,
            references=snapshot.take(
                (name, kind_of(value), value)
                for name, value in (references or {}).items()
                if value is not None
            ),
            task=task,
            fields=ALL_FIELDS,
        )
        return io.NodeOutput(*outputs)


NODE_CLASS_MAPPINGS = {"MiniMaxH3PromptCheck": MiniMaxH3PromptCheck}

NODE_DISPLAY_NAME_MAPPINGS = {"MiniMaxH3PromptCheck": "MiniMax-H3 Prompt Check"}
