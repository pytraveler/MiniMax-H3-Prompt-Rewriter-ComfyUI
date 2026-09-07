"""The node that puts effect embeddings into a finished prompt.

It writes about forty characters into a text and hands the rest of it on
untouched. Everything that makes it worth a node rather than a string
concatenation is in what it can see and a text box cannot: which of the ten
files are actually on this disk, how many positions of the prompt each one will
take up once the tokenizer expands it, whether the placement asked for exists
in this particular prompt, and whether this ComfyUI is new enough to read the
token at all.

The silent failure is the thing to design against. A token whose file is
missing, or whose name got a capital letter on the way through, is dropped by
the tokenizer with one line in the console and no sign anywhere else: the video
generates, it simply generates without the effect. So this node says all of it
out loud -- on the node caption, in a toast, and in a ``findings`` output that
can be routed somewhere it will be read.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

from . import embeddings, paths
from .nodes import CATEGORY
from .progress import NodeProgress, announce

log = logging.getLogger(__name__)

MINIMUM_COMFY = (0, 33, 0)

WHAT_IT_IS = (
    "Adds MiniMax-H3's ten effect embeddings to a prompt: bullet time, dark magic, fire "
    "breath, the Truman Show pull-back, and six more.\n\n"
    "They are not keywords. Each file is a piece of prompt that has already been through the "
    "text encoder -- fifty to a hundred and forty positions of it -- and 'embedding:name' in "
    "the text is where it gets dropped back in. So an effect costs prompt length rather than "
    "adjectives, and it arrives at full strength or not at all: there is no way to ask for "
    "half of one."
)

WHY = (
    "The node exists because every way this can fail is a silent one. A missing file, a "
    "capital letter, a token glued to the word in front of it -- each is ignored by the "
    "tokenizer with a single line in the console, and the video comes out looking like the "
    "prompt without the effect rather than like an error. This says which of the ten are on "
    "disk, what each will cost, and what it did with your text.\n\n"
    "Needs ComfyUI 0.33.0 or newer, which is where 'embedding:' started working for H3."
)

PROMPT_TOOLTIP = (
    "The prompt to put the tokens into. Anything: a writer node's output, a loaded file, "
    "something typed here.\n\n"
    "It is passed through character for character apart from the tokens themselves -- nothing "
    "is re-wrapped, re-labelled or rebuilt from parsed fields. Running it twice does not stack "
    "them: the node takes its own tokens back out before it puts them in, so unticking an "
    "effect removes it."
)

EFFECTS_TOOLTIP = (
    "Which effects to add. The grid is drawn by this pack's own interface: a tick, the name, "
    "and what it costs in prompt positions.\n\n"
    "Any combination is allowed and they are added in the order listed. Whether two of them "
    "combine into anything sensible is not something this pack can promise -- they are "
    "separate pieces of encoded prompt, and asking for a spiral ascent through four seasons "
    "is asking the model to reconcile them.\n\n"
    "Files that are not on disk are marked, and the button fetches all ten in about ten "
    "megabytes. They go to ComfyUI's own models/embeddings folder, which is the only place "
    "'embedding:' looks."
)

PLACEMENT_TOOLTIP = (
    "Where in the prompt the tokens go. It matters more than it looks.\n\n"
    "'start of the description' opens the description field itself, after its label -- the "
    "field the writers put the scene in, and the field every downstream node reads. This is "
    "the position an effect was meant to modify.\n\n"
    "'top of the prompt' puts them above everything, before the alignment sentence and the "
    "field labels.\n\n"
    "'end of the prompt' puts them last. Worth knowing why that is not just a matter of "
    "taste: ComfyUI's tokenizer joins everything that follows a token onto one line, so a "
    "token at the top flattens every blank line between the fields below it into a single "
    "space before H3 ever sees the prompt. Nothing follows a token at the end, so nothing is "
    "flattened. The node says when this is happening.\n\n"
    "A prompt with no field labels has no description to open, so it falls back to the top "
    "and says so."
)

FINDINGS_TOOLTIP = (
    "What the node did and what it noticed, one per line -- files that are not on disk, a "
    "placement that could not be honoured, line breaks the tokenizer will flatten.\n\n"
    "Empty when there is nothing to say. Route it into a text preview to keep it in sight."
)

TOKENS_TOOLTIP = (
    "How many positions of the prompt the chosen effects take up once expanded.\n\n"
    "Read out of the safetensors header of each file that is present, so it is the real "
    "number rather than a table in this pack; effects that have not been downloaded yet "
    "contribute their published count. Worth watching next to the length rules: these "
    "positions are spent on the effect and not on your scene."
)


def _comfy_version() -> tuple[int, ...] | None:
    try:
        from comfyui_version import __version__ as raw
    except Exception:
        return None
    parts = []
    for piece in str(raw).split(".")[:3]:
        digits = "".join(char for char in piece if char.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or None


def _too_old() -> str:
    """The version warning, or "" when this ComfyUI can read the token.

    An unreadable version says nothing. Guessing "too old" from a version
    string nobody could parse would put a false warning on a working install,
    and the console line from the tokenizer is still there for the real case.
    """
    version = _comfy_version()
    if version is None or version >= MINIMUM_COMFY:
        return ""
    said = ".".join(str(part) for part in version)
    wanted = ".".join(str(part) for part in MINIMUM_COMFY)
    return (
        f"this ComfyUI is {said}, and 'embedding:' only reaches the MiniMax-H3 tokenizer "
        f"from {wanted} onwards. The tokens will be read as ordinary words -- not ignored, "
        f"which would be better, but tokenized as the text they are."
    )


class MiniMaxH3EffectEmbeddings(io.ComfyNode):
    """Add MiniMax-H3 effect embeddings to a prompt."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3EffectEmbeddings",
            display_name="MiniMax-H3 Effect Embeddings",
            category=CATEGORY,
            description=WHAT_IT_IS + "\n\n" + WHY,
            inputs=[
                io.String.Input("prompt", multiline=True, tooltip=PROMPT_TOOLTIP),
                io.Combo.Input(
                    "placement",
                    options=list(embeddings.PLACEMENTS),
                    default=embeddings.BODY,
                    tooltip=PLACEMENT_TOOLTIP,
                ),
                io.String.Input(
                    "effects",
                    default="{}",
                    optional=True,
                    tooltip=EFFECTS_TOOLTIP,
                ),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
                io.Int.Output(display_name="tokens", tooltip=TOKENS_TOOLTIP),
                io.String.Output(display_name="findings", tooltip=FINDINGS_TOOLTIP),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, prompt, placement=embeddings.BODY, effects="{}"):
        node_id = cls.hidden.unique_id
        progress = NodeProgress(node_id)

        names = embeddings.chosen(effects)
        text, note = embeddings.insert(prompt, names, placement)

        if not names:
            progress.finish("no effects selected")
            return io.NodeOutput(text, 0, "")

        try:
            directory = paths.embeddings_root()
        except Exception:
            log.warning("[minimax_h3_rewriter.embedding_node] no embeddings folder", exc_info=True)
            directory = ""

        total = 0
        absent = []
        for name in names:
            effect = embeddings.by_name(name)
            counted = None
            if directory:
                counted = embeddings.token_count(embeddings.path_for(directory, name))
                if not embeddings.present(directory, effect):
                    absent.append(effect)
            total += counted or effect.tokens

        findings = []
        old = _too_old()
        if old:
            findings.append(("warn", old))
        if absent:
            missing = ", ".join(effect.file for effect in absent)
            findings.append((
                "warn",
                f"not in {directory or 'the embeddings folder'}: {missing}. ComfyUI drops a "
                f"token whose file it cannot find, with one line in the console and no other "
                f"sign -- the video will generate without the effect. Use the download button "
                f"on the node."
            ))
        if note:
            findings.append(("info", note))

        flattened = text.count("\n", max(text.find(embeddings.IDENTIFIER), 0))
        if flattened:
            findings.append((
                "info",
                f"{flattened} line breaks fall after the tokens, and ComfyUI's tokenizer joins "
                f"everything after a token onto one line: H3 will read them as single spaces. "
                f"'{embeddings.END}' is the placement that leaves them alone."
            ))

        announce(node_id, [entry for entry in findings if entry[0] == "warn"])

        titles = ", ".join(embeddings.by_name(name).title for name in names)
        progress.finish(f"{titles}\n{total} tokens - {placement}")
        return io.NodeOutput(
            text, total, "\n".join(message for _level, message in findings)
        )


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3EffectEmbeddings": MiniMaxH3EffectEmbeddings,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3EffectEmbeddings": "MiniMax-H3 Effect Embeddings",
}
