"""Building a system prompt out of a MiniMax-H3 writing guide.

The LoRA path and this path solve the same problem from opposite ends. The LoRA
*is* the format: a 27B model was trained until the H3 output shape came out of it
unprompted, and its system prompt is seven lines long. Here the format has to be
carried by the prompt instead, so the guide goes in whole and the model can be
anything that follows instructions -- which is what makes an 8 GB card enough.

Three things are added around the guide rather than left to the model:

- **The output contract.** The guide teaches an author how to write; it never
  says "return exactly these fields and nothing else". A general model given only
  the guide answers with commentary, headings, or a Markdown fence.
- **The alignment instruction, pre-filled.** I2VA, FL2VA and L2VA must open with
  a fixed sentence carrying the duration to two decimal places. The node knows
  the duration, so the literal line is handed over already formatted and only the
  final shot number is left to the model.
- **The reference material.** These nodes read text, not pixels. Whatever the
  user knows about the reference frames or assets travels in its own labelled
  block, so the model is never guessing what ``<Picture 1>`` shows.
"""

from __future__ import annotations

import math

from .constants import OUTPUT_FIELDS, REF_OUTPUT_FIELDS

#: Modes served by the base guide.
BASE_MODES = ("T2VA", "I2VA", "FL2VA", "L2VA")
REF_MODE = "Ref2VA"
ALL_MODES = BASE_MODES + (REF_MODE,)

GUIDE_FOR_MODE = {mode: "base" for mode in BASE_MODES}
GUIDE_FOR_MODE[REF_MODE] = "ref"

FIELDS_FOR_MODE = {mode: OUTPUT_FIELDS for mode in BASE_MODES}
FIELDS_FOR_MODE[REF_MODE] = REF_OUTPUT_FIELDS

BODY_FIELD = {mode: OUTPUT_FIELDS[0] for mode in BASE_MODES}
BODY_FIELD[REF_MODE] = "detailed_description"

WHAT_MODE_MEANS = {
    "T2VA": "text to audio-video: there is no reference image, so build the whole timeline from the text",
    "I2VA": "image to audio-video: <Picture 1> is the first frame at 0.00 s and the video develops forward from it",
    "FL2VA": "first-and-last-frame to audio-video: Picture 1 opens the video, Picture 2 closes it, and the body is the path between them",
    "L2VA": "last-frame to audio-video: <Picture 1> is the final frame, so infer a plausible earlier state and converge on it",
    REF_MODE: "full-reference generation: reference assets define subjects, frames, structure or audio that the target video reuses",
}

ALIGNMENT_LINE = {
    "I2VA": (
        "For the target video, at 0.00 seconds into the target video, <Picture 1> "
        "(from [Shot 1]) is fully referenced."
    ),
    "FL2VA": (
        "How the reference pictures align with the target video — Picture 1 (from Shot 1) "
        "aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) "
        "aligns with the {seconds}-second mark of the target video."
    ),
    "L2VA": (
        "How the reference pictures align with the target video — <Picture 1> (from [Shot N]) "
        "aligns with the {seconds}-second mark of the target video."
    ),
}

ROLE = "You are a professional prompt rewriter for MiniMax-H3 joint audio-video generation."

GUIDE_OPEN = "===== BEGIN MINIMAX-H3 WRITING GUIDE ====="
GUIDE_CLOSE = "===== END MINIMAX-H3 WRITING GUIDE ====="

CHARS_PER_TOKEN = 3.0
CONTEXT_HEADROOM = 1.1
CONTEXT_GRANULARITY = 1024


def seconds(duration) -> str:
    """The duration as the guide writes it: ``10s``, ``7.5s``.

    A whole number of seconds has to stay whole. The universal node offers a
    tenth-of-a-second slider, so the value arriving here is a float even when the
    user picked ten; ``10.0s`` in the task line is a small thing that the guide
    never shows and the model has no reason to imitate.
    """
    return f"{float(duration):g}s"


def _fields_block(names: tuple[str, ...]) -> str:
    return "\n".join(f"  {name}: ..." for name in names)


def _alignment_rule(mode: str, duration: float) -> str:
    if mode == "T2VA":
        return (
            "- T2VA has no image-alignment instruction: start the answer directly with "
            f"'{OUTPUT_FIELDS[0]}:'."
        )
    line = ALIGNMENT_LINE[mode].format(seconds=f"{float(duration):.2f}")
    rule = (
        "- Start the answer with exactly this line, then one blank line, then the fields:\n"
        f"    {line}"
    )
    if "N" in ALIGNMENT_LINE[mode]:
        rule += "\n  Replace N with the number of the actual final shot. Change nothing else in it."
    return rule


def _base_system(guide: str, mode: str, duration: float) -> str:
    return "\n".join(
        [
            ROLE,
            "Rewrite the user's original prompt into one coherent, production-ready multimodal",
            "description that follows the writing guide below to the letter.",
            "",
            GUIDE_OPEN,
            guide,
            GUIDE_CLOSE,
            "",
            f"The requested task is {mode} — {WHAT_MODE_MEANS[mode]}.",
            "",
            "Output contract:",
            "- Return only these fields, in this exact order, each introduced by its own name",
            "  followed by a colon:",
            _fields_block(OUTPUT_FIELDS),
            _alignment_rule(mode, duration),
            "- Compose the scene for the requested aspect ratio, and fit the number, timing and",
            "  pacing of the shots to the requested duration.",
            "- Write everything in English. Dialogue and lyrics inside <d> and text visible on",
            "  screen keep their original wording and punctuation.",
            "- Use N/A for a sound field only in the cases the guide allows it.",
            "- Do not add explanations, notes, headings, Markdown fences, or any field that is",
            "  not listed above. Do not restate the guide.",
        ]
    )


def _ref_system(guide: str, duration: float) -> str:
    return "\n".join(
        [
            ROLE,
            "Rewrite the user's original prompt into one full-reference (Ref2VA) description",
            "that follows the writing guide below to the letter.",
            "",
            GUIDE_OPEN,
            guide,
            GUIDE_CLOSE,
            "",
            f"The requested task is {REF_MODE} — {WHAT_MODE_MEANS[REF_MODE]}.",
            "",
            "Output contract:",
            "- Return only these six fields, in this exact order, each introduced by its own",
            "  name followed by a colon:",
            _fields_block(REF_OUTPUT_FIELDS),
            "- Every asset listed under reference_assets gets a label in subject_definitions and",
            "  a line in retention_analysis. Do not invent assets that are not listed there.",
            "- summary starts with its square-bracketed task-type prefix.",
            "- detailed_description opens with one or two sentences of overall style before",
            "  [Shot 1], and cites each label where its role actually applies.",
            "- Compose the scene for the requested aspect ratio, and fit the number, timing and",
            "  pacing of the shots to the requested duration.",
            "- Write everything in English. Dialogue and lyrics inside <d> and text visible on",
            "  screen keep their original wording and punctuation.",
            "- Do not add explanations, notes, headings, Markdown fences, or any field that is",
            "  not listed above. Do not restate the guide.",
        ]
    )


def system_prompt(guide: str, mode: str, duration: float) -> str:
    """The full system message for one mode, with the guide embedded."""
    if mode not in ALL_MODES:
        raise ValueError(f"unknown mode '{mode}'; expected one of {', '.join(ALL_MODES)}")
    guide = (guide or "").strip()
    if not guide:
        raise ValueError("the writing guide is empty")
    if mode == REF_MODE:
        return _ref_system(guide, duration)
    return _base_system(guide, mode, duration)


def user_prompt(mode: str, prompt: str, resolution: str, duration: float, references: str = "") -> str:
    """The task message: what to write, for what shape, from what material."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")

    lines = [
        f"task: {mode}",
        f"resolution: {resolution}",
        f"duration: {seconds(duration)}",
    ]
    references = (references or "").strip()
    if references:
        label = "reference_assets" if mode == REF_MODE else "reference_material"
        lines.append(f"{label}:\n{references}")
    lines.append(f"original_prompt: {prompt}")
    return "\n".join(lines)


def build_messages(
    guide: str,
    mode: str,
    prompt: str,
    resolution: str,
    duration: float,
    references: str = "",
) -> list[dict[str, str]]:
    """The chat messages a guided writer sends to whatever model is running."""
    if mode == REF_MODE and not (references or "").strip():
        raise ValueError(
            "Ref2VA describes how a target video reuses reference assets, so it needs to know "
            "what they are. List them in 'reference_assets', one per line, for example:\n"
            "    Picture 1: young woman, long dark hair, blue cardigan, seated by a window\n"
            "    Video 1: source clip being edited — handheld walk down a street at night\n"
            "    Audio 1: voice-timbre reference for the woman\n"
            "With no reference assets, use the T2VA writer instead."
        )
    return [
        {"role": "system", "content": system_prompt(guide, mode, duration)},
        {"role": "user", "content": user_prompt(mode, prompt, resolution, duration, references)},
    ]


def context_needed(messages: list[dict[str, str]], max_new_tokens: int) -> int:
    """A context size the rendered prompt plus its answer will actually fit in.

    A guide is 4-7k tokens on its own, so the 8192 default that suits the LoRA's
    seven-line system prompt truncates it here -- and llama.cpp truncates from
    the *front*, taking the output contract with it. Estimating and rounding up
    costs KV cache; guessing wrong costs the whole run.
    """
    characters = sum(len(message.get("content") or "") for message in messages)
    tokens = characters / CHARS_PER_TOKEN + int(max_new_tokens)
    needed = int(math.ceil(tokens * CONTEXT_HEADROOM / CONTEXT_GRANULARITY) * CONTEXT_GRANULARITY)
    return max(needed, CONTEXT_GRANULARITY * 2)
