"""Going the other way: a finished H3 prompt back to the idea it came from.

Every other writing node here expands. This one contracts, and the two are not
mirror images of each other. Expanding is a writing job -- there is no way to
get four hundred words of blocking, light and sound out of "a black cat walks
along a fence" except by inventing them. Contracting is mostly a *parsing* job,
and treating it as a writing job is what makes it hard.

An H3 prompt is not prose. It is a known shape: named fields, a fixed alignment
sentence at the top, ``[Shot 2] At 0:03`` markers through the body, ``<Picture
1>`` tags wherever a reference is cited, dialogue fenced in ``<d>``. All of that
is scaffolding, all of it is recognisable without a model, and all of it has to
go. So it goes here, in :func:`strip`, and what reaches the model is one
paragraph of ordinary description with a short instruction over it. That is the
whole trick: the system prompt is short because the parser did the structural
half first, and a 4B model can hold a short instruction where it could not hold
"reverse this document".

What is left genuinely does need a model, because it is a judgement call --
which of forty adjectives carried the idea and which were the expansion. And it
needs guarding, because the reflex of every instruction-following model handed a
paragraph and the word "shorter" is to write *better* prose rather than plainer
prose: "a black cat" comes back as "a sleek obsidian feline". Rules alone do not
stop that. The worked example does, which is why one ships with every request
and is picked to match the requested length.

Nothing here imports ComfyUI, so the rules can be tested on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import checks
from .constants import OUTPUT_FIELDS, REF_OUTPUT_FIELDS
from .fields import ALL_FIELDS, split_sections

BODY_FIELDS = ("detailed_description", OUTPUT_FIELDS[0])

AUDIO_FIELDS = OUTPUT_FIELDS[1:]

REFERENCE_FIELDS = REF_OUTPUT_FIELDS[:3]

NOT_APPLICABLE = re.compile(r"^\s*n\s*/\s*a\s*\.?\s*$", re.IGNORECASE)

_PARENTHETICAL = re.compile(
    r"\s*\(\s*(?:from|see|as\s+in)\s+(?:\[Shot\s+\d+\]|<[A-Za-z]+\s+\d+>)\s*\)",
    re.IGNORECASE,
)

_LEAD_IN = r"\b(?:referenced\s+in|consistent\s+with|matching|as\s+in|from|in|of|per)"
_TAG = r"<(?:Picture|Video|Audio|Subject)\s+\d+>"

_CITED = re.compile(
    rf",\s*{_LEAD_IN}\s+{_TAG}\s*,"
    rf"|\s*,?\s*{_LEAD_IN}\s+{_TAG}",
    re.IGNORECASE,
)

_BARE_TAG = re.compile(_TAG)

_SENTENCE_START = re.compile(r"(\A|[.!?]\s+|\n)([a-z])")

_LABEL = re.compile(
    r"^\s*(?:\*\*|__)?\s*"
    r"(?:short[_ ]?prompt|prompt|output|answer|result|idea|original[_ ]?prompt)"
    r"\s*(?:\*\*|__)?\s*[:\-]\s*(?:\*\*|__)?\s*",
    re.IGNORECASE,
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_DOUBLED_PUNCT = re.compile(r"([,;:])\s*(?=[,.;:])")
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class Stripped:
    """A finished prompt with the scaffolding taken off.

    ``body`` is what the model is asked to shorten. Everything else is either
    handed to it separately, under its own label, or reported to the user so
    they can see what the parser did before the model ever ran.
    """

    body: str = ""
    audio: str = ""
    dialogue: tuple[str, ...] = ()
    shots: int = 0
    tags: int = 0
    sections: dict = field(default_factory=dict)
    had_fields: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.body.strip()


def _tidy_prose(text: str) -> str:
    """Close up the gaps that removing a tag or a marker leaves behind."""
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _DOUBLED_PUNCT.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"^[ \t]*[,;:]\s*", "", text, flags=re.MULTILINE)
    text = _BLANK_RUN.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    return _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), text)


def strip(text: str) -> Stripped:
    """Take a finished H3 prompt apart into the half a model has to read.

    Works on any of the five tasks without being told which one, because it
    splits against every field name either family uses: a T2VA answer simply
    leaves the Ref2VA fields empty, and an answer with no labels at all lands
    whole in the body. The alignment sentence at the top is dropped with the
    rest of the scaffolding -- it names a picture and a timestamp, which is
    exactly the kind of binding a reusable short prompt must not carry.
    """
    raw = str(text or "")
    _head, sections = split_sections(raw, ALL_FIELDS, fallback=OUTPUT_FIELDS[0])

    body = ""
    for name in BODY_FIELDS:
        if sections.get(name, "").strip():
            body = sections[name].strip()
            break

    audio = "\n".join(
        sections[name].strip()
        for name in AUDIO_FIELDS
        if sections.get(name, "").strip() and not NOT_APPLICABLE.match(sections[name])
    )

    dialogue = tuple(
        line.strip() for line in checks.DIALOGUE.findall(body) if line.strip()
    )
    shots = len(checks.SHOT.findall(body))
    tags = len(checks.TAG.findall(body)) + len(checks.SUBJECT.findall(body))

    body = checks.SHOT.sub("", body)
    body = _PARENTHETICAL.sub("", body)
    body = _CITED.sub("", body)
    body = _BARE_TAG.sub("", body)

    return Stripped(
        body=_tidy_prose(body),
        audio=_tidy_prose(audio),
        dialogue=dialogue,
        shots=shots,
        tags=tags,
        sections=sections,
        had_fields=tuple(name for name in ALL_FIELDS if sections.get(name, "").strip()),
    )


ROLE = "You recover the short idea a long video prompt was written from."

DETAIL = {
    "idea": "Answer with one short sentence of at most ten words.",
    "sentence": "Answer with one sentence. It may name the place and the time of day.",
    "paragraph": (
        "Answer with two to four sentences: one for each thing that actually happens, "
        "in the order it happens."
    ),
}

DETAIL_ORDER = ("idea", "sentence", "paragraph")

EXAMPLE_SCENE = (
    "Live-action, cinematic, a low-angle medium shot frames a sleek black cat walking "
    "steadily along the top of a weathered wooden fence in a quiet suburban yard at dusk. "
    "The camera tracks right with small amplitude at slow speed, following the feline as its "
    "soft fur catches the fading golden light and its ears swivel to catch distant sounds. "
    "The cat pauses briefly to glance over the fence toward an open garden beyond, then "
    "resumes its unhurried pace toward the far end."
)

EXAMPLE_SOUND = (
    "A gentle evening breeze rustles through nearby grass while the cat's paws tap softly "
    "against the wooden fence. A sparse piano melody at a slow tempo underneath."
)

EXAMPLE_ANSWER = {
    "idea": {
        False: "A black cat walks along a fence.",
        True: "A low-angle shot tracks a black cat walking along a fence.",
    },
    "sentence": {
        False: "A black cat walks along a wooden fence in a yard at dusk.",
        True: (
            "A low-angle medium shot tracks right as a black cat walks along a wooden fence "
            "in a yard at dusk."
        ),
    },
    "paragraph": {
        False: (
            "A black cat walks along a wooden fence in a suburban yard at dusk. It stops to "
            "look over at the garden on the other side, then walks on to the far end."
        ),
        True: (
            "A low-angle medium shot tracks right as a black cat walks along a wooden fence "
            "in a suburban yard at dusk. The camera follows as it stops to look over at the "
            "garden on the other side, then walks on to the far end."
        ),
    },
}

EXAMPLE_STYLE_PREFIX = "Live-action, cinematic. "

EXAMPLE_SOUND_SUFFIX = " A breeze in the grass and a slow piano underneath."


def example_answer(
    detail: str, keep_camera: bool, keep_audio: bool, keep_style: bool
) -> str:
    """The worked answer for one setting of the axes."""
    text = EXAMPLE_ANSWER[detail][bool(keep_camera)]
    if keep_style:
        text = EXAMPLE_STYLE_PREFIX + text
    if keep_audio:
        text = text + EXAMPLE_SOUND_SUFFIX
    return text

SUBJECTS = {
    "as written": (
        "Name the subjects the way the input names them, keeping the same appearance, "
        "clothing and colours."
    ),
    "age and gender": (
        "Reduce every person to age and gender and nothing else: 'a young woman', 'an "
        "elderly man', 'a child'. Drop hair, build, clothing, occupation and every other "
        "feature. An animal or an object keeps only its kind and its colour. For example, "
        "'a fisherman in his sixties with a grey beard and a yellow oilskin jacket' becomes "
        "'an elderly man'."
    ),
    "impersonal": (
        "Do not describe the subjects at all. A person is 'a subject'; anything else is "
        "its bare kind -- 'an animal', 'a vehicle', 'a bird'. Drop age, gender, breed, "
        "colour, clothing and occupation with everything else: a trade is an identity too. "
        "For example, 'a fisherman in his sixties with a grey beard' becomes 'a subject', "
        "not 'a fisherman'."
    ),
}

SUBJECT_ORDER = ("as written", "age and gender", "impersonal")

KEEP_CAMERA = (
    "Keep the camera work: shot size, angle, and any camera move, in as few words as "
    "the input used."
)
DROP_CAMERA = (
    "Drop the camera entirely: shot size, angle, lens, framing and every camera move."
)
KEEP_STYLE = (
    "Keep the medium and the look the input opens with -- live-action, animation, "
    "cinematic, documentary -- as a short leading clause."
)
DROP_STYLE = "Drop the medium and the look: live-action, animated, cinematic, film grade."
KEEP_AUDIO = (
    "The input has a 'sound:' block. Fold it into one short sentence at the end, naming "
    "only what makes a sound. Do not give it a heading or a label of its own."
)
KEEP_DIALOGUE = (
    "The lines under 'spoken_lines:' are said out loud. Keep them word for word, still "
    "inside their <d> and </d> markers, at the point in the sentence where they belong."
)


def _language_rule(language: str) -> str:
    wanted = (language or "").strip()
    if not wanted:
        return "Write the answer in the same language the input is written in."
    return (
        f"Write the answer in {wanted}. Words that are quoted from the input -- dialogue "
        f"inside <d>, text visible on screen -- keep their original wording."
    )


def _language_last_word(language: str) -> list[str]:
    """The language asked for again, after the example, in the last line of all.

    The worked example is written in English and cannot be anything else -- the
    node cannot translate it into a language named in a widget at build time --
    and a model handed an English demonstration copies its language along with
    everything else it copies. Measured on three models: with the keeps off the
    rule above was obeyed, and with them on, which lengthens the example, all
    three answered in English regardless.

    So the demonstration is named for what it is and the instruction is put
    where the model reads it last, closest to the token it is about to write.
    """
    wanted = (language or "").strip()
    if not wanted:
        return []
    return [
        "",
        f"The example above is written in English. Copy which things it keeps and which it "
        f"drops. Do not copy the language it is written in.",
        f"Write your own answer in {wanted}.",
    ]


def system_prompt(
    detail: str = "sentence",
    subjects: str = "as written",
    keep_camera: bool = False,
    keep_audio: bool = False,
    keep_style: bool = False,
    language: str = "",
    dialogue: bool = False,
) -> str:
    """The whole instruction, assembled from the axes.

    Each widget contributes one line, and a line that is not contributed is
    absent rather than negated: a model reads "drop the camera" and a model
    reads "keep the camera", but a model given both in different words picks
    whichever came last.

    The worked example at the bottom is assembled from the same axes, and that
    is not a nicety. Where the rules and the demonstration disagree, a small
    model follows the demonstration -- so an example whose answer has no camera
    in it turns keep_camera off no matter what the rule above it says.
    """
    if detail not in DETAIL:
        raise ValueError(f"unknown detail '{detail}'; expected one of {', '.join(DETAIL_ORDER)}")
    if subjects not in SUBJECTS:
        raise ValueError(
            f"unknown subjects '{subjects}'; expected one of {', '.join(SUBJECT_ORDER)}"
        )

    rules = [
        "Never upgrade a word. 'a black cat' stays 'a black cat'; it does not become "
        "'a sleek obsidian feline'. Plain everyday words only.",
        "Add nothing. Every subject, action and place you name must be present in the "
        "input. If the input does not say where it happens, neither do you.",
        "Drop the writing rather than the story: adjectives of light, weather, texture "
        "and mood, and any clause about how something feels or what it suggests.",
        DETAIL[detail],
        SUBJECTS[subjects],
        KEEP_CAMERA if keep_camera else DROP_CAMERA,
        KEEP_STYLE if keep_style else DROP_STYLE,
    ]
    if keep_audio:
        rules.append(KEEP_AUDIO)
    if dialogue:
        rules.append(KEEP_DIALOGUE)
    rules.append(_language_rule(language))

    return "\n".join(
        [
            ROLE,
            "The input under 'scene:' is one video prompt, expanded by another model from a "
            "single plain line a person typed. Give that line back.",
            "",
            "Rules:",
            *(f"- {rule}" for rule in rules),
            "",
            "Output contract:",
            "- Return the recovered prompt alone. No preface, no label, no quotation marks, "
            "no Markdown, no explanation of what you dropped.",
            "- Do not use the field names, the shot markers or the reference tags of the "
            "input format. What you write is ordinary prose.",
            "",
            "Worked example",
            "scene:",
            EXAMPLE_SCENE,
            *(["", "sound:", EXAMPLE_SOUND] if keep_audio else []),
            "",
            "answer:",
            example_answer(detail, keep_camera, keep_audio, keep_style),
            *_language_last_word(language),
        ]
    )


def user_prompt(stripped: Stripped, keep_audio: bool = False) -> str:
    """The material itself, each kind under its own label.

    Labels rather than one run of prose, because the model is told about them
    by name in the rules: the sound is folded in only when it is there to fold,
    and the dialogue is quoted only when there is dialogue.
    """
    if stripped.empty:
        raise ValueError(
            "there is nothing to shorten: the prompt is empty, or it carries no "
            "description field. Feed this the finished prompt from a writer node, or the "
            "text of one."
        )

    parts = ["scene:", stripped.body]
    if keep_audio and stripped.audio:
        parts += ["", "sound:", stripped.audio]
    if stripped.dialogue:
        parts += ["", "spoken_lines:"] + [f"<d>{line}</d>" for line in stripped.dialogue]
    return "\n".join(parts)


def build_messages(
    text: str,
    detail: str = "sentence",
    subjects: str = "as written",
    keep_camera: bool = False,
    keep_audio: bool = False,
    keep_style: bool = False,
    language: str = "",
    system: str = "",
) -> tuple[list[dict[str, str]], Stripped]:
    """The chat messages, and what the parser made of the input on the way.

    ``system`` replaces the assembled instruction wholesale, the way it does on
    every writer here. The parsing is not skipped with it: stripping the shot
    markers and the reference tags is right whatever the instruction over them
    says, and a system prompt aimed at some other house style still wants the
    scaffolding gone.
    """
    stripped = strip(text)
    given = (system or "").strip()
    message = given or system_prompt(
        detail=detail,
        subjects=subjects,
        keep_camera=keep_camera,
        keep_audio=keep_audio,
        keep_style=keep_style,
        language=language,
        dialogue=bool(stripped.dialogue),
    )
    return (
        [
            {"role": "system", "content": message},
            {"role": "user", "content": user_prompt(stripped, keep_audio)},
        ],
        stripped,
    )


def tidy(answer: str, detail: str = "sentence") -> str:
    """The model's answer, with the decoration it was told not to add taken off.

    Told plainly enough, most models return the sentence alone. Enough of them
    return ``Short prompt: "..."`` in a fenced block that stripping it is
    cheaper than a second round trip, and none of the four things removed here
    can be part of a legitimate answer.
    """
    text = _THINK.sub("", str(answer or "")).strip()

    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    text = _LABEL.sub("", text).strip()

    opening = "\"'" + "\u201c\u2018\u00ab"
    closing = "\"'" + "\u201d\u2019\u00bb"
    if len(text) > 1 and text[0] in opening and text[-1] in closing:
        text = text[1:-1].strip()

    if detail != "paragraph":
        text = " ".join(text.split())

    return _tidy_prose(text)


TRANSLATE_SYSTEM = (
    "You are a translator. Translate the user's message into {language}.\n"
    "Return the translation alone: no preface, no label, no quotation marks, no Markdown, "
    "no note about what you did, and no copy of the original.\n"
    "Translate every sentence. Do not shorten it, expand it or reword it. Dialogue inside "
    "<d> and </d> keeps those markers."
)


def translate_messages(text: str, language: str) -> list[dict[str, str]]:
    """A second pass that translates a finished short prompt, and why there is one.

    Asking for the reduction and the language in one request does not work, and
    the reason is the worked example again. It is written in English, it cannot
    be anything else -- nothing here can translate it into a language typed into
    a widget -- and a model copying the demonstration copies its language too.
    Measured on three models: with the keeps off, the language rule was obeyed;
    with them on, which makes the example longer, all three answered in English
    however the rule was phrased or placed.

    Translating afterwards is a different request with one objective, no example
    to copy and nothing to trade off against, and the same models obey it: four
    of four, on the two languages tried, including the model that had ignored
    the rule outright. It costs one short generation on a model already loaded.
    """
    wanted = (language or "").strip()
    if not wanted:
        raise ValueError("translate_messages needs a language")
    return [
        {"role": "system", "content": TRANSLATE_SYSTEM.format(language=wanted)},
        {"role": "user", "content": str(text or "").strip()},
    ]


def measure(text: str) -> str:
    """How long a piece of text is, in the unit its script is actually counted in.

    Splitting on spaces is a word count for a language that writes them and
    nonsense for one that does not: a finished Chinese answer came back as
    "1 out", which is true of the spaces and says nothing about the answer.
    """
    text = str(text or "")
    han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    words = len(text.split())
    if han > words:
        return f"{han} characters"
    return f"{words} words"


def report(stripped: Stripped, answer: str = "") -> str:
    """One line for the caption under the node: what came off, what came back.

    ``answer`` is left out by the node that only builds the messages: it runs
    no model, so there is no answer to measure and "0 out" would be a fact
    about this node rather than about the prompt.
    """
    size_in = measure(stripped.body)
    if (answer or "").strip():
        parts = [f"{size_in} in, {measure(answer)} out"]
    else:
        parts = [f"{size_in} of scene"]
    if stripped.shots:
        parts.append(f"{stripped.shots} shot markers dropped")
    if stripped.tags:
        parts.append(f"{stripped.tags} reference tags dropped")
    if stripped.dialogue:
        parts.append(f"{len(stripped.dialogue)} spoken lines kept")
    dropped = [name for name in REFERENCE_FIELDS if name in stripped.had_fields]
    if dropped:
        parts.append("dropped " + ", ".join(dropped))
    return " - ".join(parts)
