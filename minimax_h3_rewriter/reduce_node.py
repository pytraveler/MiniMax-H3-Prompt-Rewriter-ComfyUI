"""The two nodes that run the reduction, and the widgets they are steered by.

Same split as the guided writers: one node runs a model from the writer list and
hands back the short prompt, the other builds the two messages and hands back
strings for whatever LLM node is already in the graph -- an API model, an Ollama
node, ComfyUI's own text generator. Both stand on :mod:`reduce`, so the parsing
and the instruction are identical either way and only the last step differs.

The widgets are four axes rather than one "abstraction level" dial, because
they are genuinely independent. How long the answer is and how specifically it
names people are different questions -- a one-line prompt can still say "a woman
in a red coat", and a three-sentence one can still say "a subject" -- and the
camera, the sound and the film look are each kept or dropped on their own. A
single dial would have to bundle them into an order nobody agrees on.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

from . import reduce
from .nodes import (
    BYPASS_TOOLTIP,
    CATEGORY,
    DEFAULT_OPTIONS,
    GUIDE_PROMPT_FORMATS,
    OPTIONS_TYPE,
    run_messages,
    single_prompt,
    writer_choices,
)
from .progress import NodeProgress

log = logging.getLogger(__name__)

WHAT_IT_IS = (
    "Turns a finished MiniMax-H3 prompt back into the short line it could have been written "
    "from: 'A black cat walks along a fence' out of four hundred words of blocking, light and "
    "sound.\n\n"
    "The format is taken apart without a model first -- field names, the alignment sentence, "
    "the [Shot n] markers, the <Picture n> tags and the sound sections all come off by rule -- "
    "so what the model is asked is only 'shorten this paragraph', which a 4B can do. Reference "
    "bindings go with the scaffolding: the result describes a video, not a set of pictures the "
    "next run will not have."
)

WHY = (
    "Three things it is for: changing one word of a prompt you liked without rewriting the "
    "other four hundred, feeding a prompt written for H3 to a generator that wants a short one, "
    "and putting a readable line on a saved prompt."
)

PROMPT_TOOLTIP = (
    "The finished prompt to shorten. Any of the five tasks, and it does not have to be said "
    "which: the text is split against every field name either family uses, and one with no "
    "field names at all is read whole as the description.\n\n"
    "A writer node's output, a loaded text file, or something pasted in."
)

DETAIL_TOOLTIP = (
    "How much comes back.\n\n"
    "'idea' is the bare line -- one short sentence, ten words at most. 'sentence' allows the "
    "place and the time of day. 'paragraph' keeps one sentence per thing that actually happens, "
    "which is what a prompt with several shots needs if the order is to survive.\n\n"
    "The example the model is shown is picked to match, which does more for the length than the "
    "instruction does."
)

SUBJECTS_TOOLTIP = (
    "How specifically the subjects are named. Separate from the length: a one-line prompt can "
    "still say 'a woman in a red coat'.\n\n"
    "'as written' keeps appearance and clothing. 'age and gender' cuts every person down to "
    "'a young woman', 'an elderly man'. 'impersonal' drops even that: a person becomes "
    "'a subject' and anything else its bare kind.\n\n"
    "'impersonal' is for templates you fill in afterwards. Fed to a generator as it stands, it "
    "produces exactly the anonymous nothing it asks for."
)

CAMERA_TOOLTIP = (
    "Keep the shot size, the angle and the camera move. Off by default: the camera is usually "
    "the writer's invention rather than yours, and leaving it out lets the next rewrite choose "
    "again."
)

AUDIO_TOOLTIP = (
    "Fold the soundscape and the music into one clause at the end. Off by default, and off "
    "means the sound sections never reach the model at all -- they are dropped by the parser, "
    "not by the instruction."
)

STYLE_TOOLTIP = (
    "Keep the medium and the look the prompt opens with -- live-action, animation, cinematic, "
    "documentary. Worth turning on when the look is the point and not a default."
)

LANGUAGE_TOOLTIP = (
    "Which language the short prompt comes back in. Empty means the language of the input, "
    "which for an H3 prompt is English. Write a language name: English, Russian, Chinese, "
    "Deutsch -- whatever the model is likely to recognise.\n\n"
    "The Reducer does this as a second pass: it shortens first and translates the finished "
    "line afterwards, in its own request. Asking for both at once does not work -- the worked "
    "example in the instruction is in English, and a model copying the demonstration copies "
    "its language with it. Translating afterwards has one objective and no example to copy, "
    "and small models obey it. It costs one short generation on a model already loaded.\n\n"
    "'Reduce Prompt (any LLM)' can only build one request, so there the language is a rule "
    "inside it and is obeyed or not depending on the model. If a short prompt comes back from "
    "that node in the wrong language, this is why, and the Reducer is the reliable path."
)

SYSTEM_TOOLTIP = (
    "Replace the whole assembled instruction with your own. 'detail', 'subjects' and the three "
    "keeps then stop applying -- they exist only to build the text this overrides.\n\n"
    "'language' still applies on the Reducer, because there it is not part of this text at all: "
    "it is a second request made after yours has answered.\n\n"
    "The parsing still happens either way. Stripping shot markers and reference tags is right "
    "whatever the instruction over them says, so what your system prompt is handed is the "
    "cleaned scene, not the raw text."
)

FORMAT_TOOLTIP = (
    "How the third output joins the two. 'plain' puts a blank line between them and lets the "
    "LLM node apply the model's own chat template, which lands the instruction in the user "
    "turn. 'chatml' writes the turns out instead, so a Qwen text encoder takes it as a real "
    "system message and skips its thinking block; on a model that is not ChatML, leave this on "
    "'plain'."
)

SCENE_TOOLTIP = (
    "The description with the scaffolding taken off and nothing else done to it -- no model has "
    "touched this. Wire it when the deterministic half is all you wanted: the prose of a prompt, "
    "with the field names, shot markers and reference tags gone."
)


def _axes() -> list:
    """The widgets both nodes are steered by, in one place.

    ``language`` is declared required despite having an empty default, because
    an optional input is sorted below every required one: left optional it
    lands under the seed, three widgets away from the axes it belongs with.
    Empty is a real answer here -- it asks for the language of the input -- so
    nothing is lost by requiring it.
    """
    return [
        io.Combo.Input(
            "detail",
            options=list(reduce.DETAIL_ORDER),
            default="sentence",
            tooltip=DETAIL_TOOLTIP,
        ),
        io.Combo.Input(
            "subjects",
            options=list(reduce.SUBJECT_ORDER),
            default="as written",
            tooltip=SUBJECTS_TOOLTIP,
        ),
        io.Boolean.Input("keep_camera", default=False, tooltip=CAMERA_TOOLTIP),
        io.Boolean.Input("keep_audio", default=False, tooltip=AUDIO_TOOLTIP),
        io.Boolean.Input("keep_style", default=False, tooltip=STYLE_TOOLTIP),
        io.String.Input("language", default="", tooltip=LANGUAGE_TOOLTIP),
    ]


class MiniMaxH3PromptReducer(io.ComfyNode):
    """Shorten a finished H3 prompt back to its idea, with a local model."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3PromptReducer",
            display_name="MiniMax-H3 Prompt Reducer",
            category=CATEGORY,
            description=WHAT_IT_IS + "\n\n" + WHY,
            inputs=[
                io.Custom(OPTIONS_TYPE).Input("options", optional=True),
                io.String.Input("prompt", multiline=True, tooltip=PROMPT_TOOLTIP),
                io.Combo.Input(
                    "model",
                    options=writer_choices(),
                    tooltip=(
                        "Any instruction-following GGUF, from the same list the guided writers "
                        "use. This asks much less of a model than writing does -- the format is "
                        "gone before the model sees anything -- so the smallest entry in the "
                        "list is a reasonable choice here even if it is not one there."
                    ),
                ),
                *_axes(),
                io.Boolean.Input(
                    "greedy",
                    default=True,
                    tooltip=(
                        "Deterministic decoding. Worth keeping on: sampling is what turns "
                        "'a black cat' into 'a sleek obsidian feline'."
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
                        "Keep the model in VRAM afterwards. Leave off when the same GPU has to "
                        "run MiniMax-H3 video generation next."
                    ),
                ),
                io.Boolean.Input(
                    "bypass", default=False, optional=True, tooltip=BYPASS_TOOLTIP
                ),
                io.String.Input(
                    "system_prompt",
                    default="",
                    multiline=True,
                    optional=True,
                    tooltip=SYSTEM_TOOLTIP,
                ),
            ],
            outputs=[
                io.String.Output(display_name="short_prompt"),
                io.String.Output(display_name="scene", tooltip=SCENE_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        prompt,
        model,
        detail,
        subjects,
        keep_camera,
        keep_audio,
        keep_style,
        language="",
        greedy=True,
        seed=42,
        keep_model_loaded=False,
        options=None,
        bypass=False,
        system_prompt="",
    ):
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)

        if bypass:
            progress.finish("bypassed")
            return io.NodeOutput(str(prompt or "").strip(), "")

        settings = dict(DEFAULT_OPTIONS)
        if options:
            settings.update(options)

        wanted = (language or "").strip()
        messages, stripped = reduce.build_messages(
            prompt,
            detail=detail,
            subjects=subjects,
            keep_camera=keep_camera,
            keep_audio=keep_audio,
            keep_style=keep_style,
            system=system_prompt,
        )
        progress.text(
            f"{len(stripped.body.split())} words of scene after stripping "
            f"{len(stripped.had_fields)} fields",
            force=True,
        )

        answer = run_messages(
            model,
            messages,
            greedy,
            seed,
            keep_model_loaded or bool(wanted),
            settings,
            progress,
            label="reduce",
        )
        short = reduce.tidy(answer, detail)

        if wanted and short:
            progress.text(f"translating into {wanted}", force=True)
            short = reduce.tidy(
                run_messages(
                    model,
                    reduce.translate_messages(short, wanted),
                    greedy,
                    seed,
                    keep_model_loaded,
                    settings,
                    progress,
                    label="translate",
                ),
                detail,
            )

        note = reduce.report(stripped, short)
        if wanted:
            note += f" - translated into {wanted}"
        log.info("[minimax_h3_rewriter.reduce] %s", note)
        progress.finish(note + "\n\n" + (short or "(the model returned nothing)"))
        return io.NodeOutput(short, stripped.body)


class MiniMaxH3ReducePrompt(io.ComfyNode):
    """The reduction's two messages, for any other LLM node to run."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3ReducePrompt",
            display_name="MiniMax-H3 Reduce Prompt (any LLM)",
            category=CATEGORY,
            description=(
                WHAT_IT_IS
                + "\n\nThis one runs nothing. It hands back the system and user messages as "
                "strings for whatever LLM node you already have -- local, API, or remote -- "
                "and costs no VRAM and no time. The parsing still happens here, so the scene "
                "your model receives is already clean. The third output is both messages in "
                "one string, for a node that takes a single prompt."
            ),
            inputs=[
                io.String.Input("prompt", multiline=True, tooltip=PROMPT_TOOLTIP),
                *_axes(),
                io.Combo.Input(
                    "format",
                    options=list(GUIDE_PROMPT_FORMATS),
                    default="plain",
                    optional=True,
                    tooltip=FORMAT_TOOLTIP,
                ),
                io.String.Input(
                    "system_prompt",
                    default="",
                    multiline=True,
                    optional=True,
                    tooltip=SYSTEM_TOOLTIP,
                ),
            ],
            outputs=[
                io.String.Output(display_name="system_prompt"),
                io.String.Output(display_name="user_prompt"),
                io.String.Output(display_name="prompt"),
                io.String.Output(display_name="scene", tooltip=SCENE_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        prompt,
        detail,
        subjects,
        keep_camera,
        keep_audio,
        keep_style,
        language="",
        format="plain",
        system_prompt="",
    ):
        progress = NodeProgress(cls.hidden.unique_id)
        messages, stripped = reduce.build_messages(
            prompt,
            detail=detail,
            subjects=subjects,
            keep_camera=keep_camera,
            keep_audio=keep_audio,
            keep_style=keep_style,
            language=language,
            system=system_prompt,
        )
        system, user = messages[0]["content"], messages[1]["content"]
        joined = single_prompt(system, user, format)
        progress.finish(
            f"{detail} - {subjects} - system {len(system)} chars - user {len(user)} chars "
            f"- {format}\n" + reduce.report(stripped)
        )
        return io.NodeOutput(system, user, joined, stripped.body)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3PromptReducer": MiniMaxH3PromptReducer,
    "MiniMaxH3ReducePrompt": MiniMaxH3ReducePrompt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3PromptReducer": "MiniMax-H3 Prompt Reducer",
    "MiniMaxH3ReducePrompt": "MiniMax-H3 Reduce Prompt (any LLM)",
}
