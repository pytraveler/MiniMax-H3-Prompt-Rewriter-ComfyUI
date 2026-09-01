"""Checking a finished rewrite against the rules H3 actually reads by.

The writers put the format in the model -- trained into a LoRA, or carried by
the guide in the system prompt -- and until now nothing looked at what came
back beyond which fields it filled. This module is that look: pure text in,
a list of findings out, no model and no ComfyUI anywhere in it.

Every rule here is a rule of the MiniMax prompt-writing guides, not a style
preference: shot numbering, cut times inside the duration, dialogue markup,
reference tags that point at media the task can or does have. Findings are
said, never enforced -- a prompt that trips a rule still ships, because the
model is sometimes right to bend one and the person is the judge. Warnings
are things H3 will likely misread; notes are things the guide merely suggests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .fields import body_field

WARN = "warn"
INFO = "info"

REPORT_ALL = "warnings and notes"
REPORT_WARNINGS = "warnings only"
REPORT_NONE = "off"
REPORT_LEVELS = (REPORT_ALL, REPORT_WARNINGS, REPORT_NONE)


def reportable(issues, setting: str = REPORT_ALL) -> list:
    """The findings this setting wants said. Unknown settings report everything.

    An unreadable setting reporting everything is deliberate: a stale workflow
    or a typo should leave the check louder than intended, never silent.
    """
    if setting == REPORT_NONE:
        return []
    if setting == REPORT_WARNINGS:
        return [issue for issue in issues if issue.level == WARN]
    return list(issues)


@dataclass(frozen=True)
class Issue:
    level: str
    message: str
    code: str = ""


TASK_ALIASES = {
    "t2va": "t2va", "t2av": "t2va",
    "i2va": "i2va", "i2av": "i2va",
    "fl2va": "fl2va", "fl2av": "fl2va", "flf2va": "fl2va", "flf2av": "fl2va",
    "l2va": "l2va", "l2av": "l2va",
    "ref2va": "ref2va", "ref2av": "ref2va", "ref": "ref2va",
}

CAPACITY = {
    "t2va": {"Picture": 0, "Video": 0, "Audio": 0},
    "i2va": {"Picture": 1, "Video": 0, "Audio": 0},
    "l2va": {"Picture": 1, "Video": 0, "Audio": 0},
    "fl2va": {"Picture": 2, "Video": 0, "Audio": 0},
    "ref2va": {"Picture": 9, "Video": 3, "Audio": 3},
}

KIND_TAG = {"image": "Picture", "video": "Video", "audio": "Audio"}

ALIGNMENT_PHRASE = {
    "i2va": "fully referenced",
    "fl2va": "aligns with",
    "l2va": "aligns with",
}

TAG = re.compile(r"<(Picture|Video|Audio)\s+(\d+)>")
SHOT = re.compile(r"\[Shot\s+(\d+)\]\s*(?:[Aa]t\s+(\d{1,2}):(\d{2}(?:\.\d{1,3})?))?")
DIALOGUE = re.compile(r"<d>(.*?)</d>", re.DOTALL)
LANGUAGE = re.compile(r"^\s*\[[A-Za-z]")
SUBJECT = re.compile(r"<Subject\s+(\d+)>")

REF_WORDS = (350, 500)


def over_capacity(task, counts: dict) -> list[tuple]:
    """Which reference kinds this task cannot take as many of as are offered.

    The same numbers ``_tags`` reads a finished answer by, asked before anything
    runs. A tenth picture is not a stylistic matter: H3 has nowhere to put it,
    so a node holding one is better stopped than described.

    ``counts`` is keyed by tag word -- Picture, Video, Audio. Words the task has
    no ceiling for, Subject among them, are not this rule's business.

    Findings come back as ``(word, offered, allowed)`` rather than as sentences,
    because the nodes that ask do not share a vocabulary: one calls the task
    Ref2VA and points at a strip, the other calls it Ref2AV and points at
    squares. The numbers are this module's; the wording is theirs.
    """
    capacity = CAPACITY.get(normalize(task))
    if not capacity:
        return []
    return [
        (word, count, capacity[word])
        for word, count in sorted(counts.items())
        if word in capacity and count > capacity[word]
    ]


def normalize(task) -> str:
    return TASK_ALIASES.get(str(task or "").strip().lower(), "")


def review(
    text: str,
    sections: dict,
    names: tuple,
    task="",
    duration=None,
    having=None,
) -> list:
    """Everything worth saying about one answer, worst first.

    ``sections`` and ``names`` are what the caller already split the answer
    into; ``having`` is the kinds of reference the node was actually shown
    (None when it cannot know, as on the text-only writers, which skips the
    connected-versus-cited rules but not the task-capacity ones).
    """
    if not (text or "").strip():
        return [Issue(WARN, "the answer is empty", "empty")]

    wanted = normalize(task)
    body = str(sections.get(body_field(names)) or "")

    issues = []
    issues += _fields(sections, names)
    issues += _alignment(text, wanted)
    issues += _shots(body, duration)
    issues += _dialogue(body)
    issues += _tags(text, wanted, having)
    if wanted == "ref2va":
        issues += _subjects(sections)
        issues += _length(body)
    issues.sort(key=lambda issue: issue.level != WARN)
    return issues


def describe(issues) -> str:
    """The findings as a caption block, empty when there are none."""
    if not issues:
        return ""
    warnings = sum(1 for issue in issues if issue.level == WARN)
    notes = len(issues) - warnings
    counts = [
        part
        for part in (
            f"{warnings} warning(s)" if warnings else "",
            f"{notes} note(s)" if notes else "",
        )
        if part
    ]
    head = "self-check: " + ", ".join(counts)
    lines = [
        ("! " if issue.level == WARN else "- ") + issue.message for issue in issues
    ]
    return "\n".join([head] + lines)


def _fields(sections, names) -> list:
    absent = [name for name in names if not str(sections.get(name) or "").strip()]
    if not absent:
        return []
    return [
        Issue(
            WARN,
            f"{len(absent)} field(s) missing from the answer: {', '.join(absent)} -- "
            "lower the temperature, or try a larger writer model",
            "fields",
        )
    ]


def _alignment(text, wanted) -> list:
    phrase = ALIGNMENT_PHRASE.get(wanted)
    if phrase and phrase not in text:
        return [
            Issue(
                WARN,
                f"no alignment line -- {wanted.upper()} prompts open with the fixed "
                "sentence telling H3 where the reference frames land",
   
            "alignment",
        )
        ]
    return []


def _shots(body, duration) -> list:
    """Shot structure: numbering, cut times, and both against the duration.

    Only new shot numbers advance the sequence; a smaller number later in the
    text is a back-reference inside a sentence and none of this rule's business.
    """
    if not body:
        return []
    issues = []
    if "[Shot 1]" not in body:
        issues.append(
            Issue(WARN, "the description has no [Shot 1] -- shots are how H3 reads structure", "shots")
        )
        return issues

    highest = 0
    last_cut = -1.0
    unstamped = []
    limit = None
    try:
        limit = float(duration) if duration else None
    except (TypeError, ValueError):
        pass

    for match in SHOT.finditer(body):
        number = int(match.group(1))
        if number <= highest:
            continue
        if number > highest + 1:
            issues.append(
                Issue(WARN, f"shot numbering jumps from {highest} to {number}", "shots")
            )
        highest = number

        stamped = match.group(2) is not None
        if number == 1:
            if stamped:
                issues.append(
                    Issue(WARN, "[Shot 1] carries a cut time -- the guide leaves the first shot unstamped", "shots")
                )
            continue
        if not stamped:
            unstamped.append(number)
            continue
        cut = int(match.group(2)) * 60 + float(match.group(3))
        if cut <= last_cut:
            issues.append(
                Issue(WARN, f"[Shot {number}] cut time is not later than the previous one", "shots")
            )
        if limit and cut >= limit:
            issues.append(
                Issue(WARN, f"[Shot {number}] cuts at {cut:g}s, past the {limit:g}s end", "shots")
            )
        last_cut = max(last_cut, cut)

    if unstamped:
        listed = ", ".join(f"[Shot {n}]" for n in unstamped)
        issues.append(
            Issue(WARN, f"{listed} missing the 'At MM:SS.mmm' cut time a later shot opens with", "shots")
        )
    return issues


def _dialogue(body) -> list:
    issues = []
    opened = body.count("<d>")
    closed = body.count("</d>")
    if opened != closed:
        issues.append(
            Issue(WARN, f"<d> tags are unbalanced: {opened} opened, {closed} closed", "dialogue")
        )
    for match in DIALOGUE.finditer(body):
        if not LANGUAGE.match(match.group(1)):
            spoken = " ".join(match.group(1).split())[:40]
            issues.append(
                Issue(WARN, f"a <d> block has no [Language] tag: \"{spoken}...\"", "dialogue")
            )
    return issues


def _tags(text, wanted, having) -> list:
    """Reference tags against what the task takes and what the node was shown."""
    capacity = CAPACITY.get(wanted)
    cited = {}
    for match in TAG.finditer(text):
        word, number = match.group(1), int(match.group(2))
        cited[word] = max(cited.get(word, 0), number)

    issues = []
    if capacity:
        for word, top in sorted(cited.items()):
            allowed = capacity.get(word, 0)
            if allowed == 0:
                issues.append(
                    Issue(
                        WARN,
                        f"<{word} {top}> is cited, but {wanted.upper()} has no "
                        f"{word.lower()} references",
                         "tags",
                    )
                )
            elif top > allowed:
                span = f"1-{allowed}" if allowed > 1 else "1"
                issues.append(
                    Issue(
                        WARN,
                        f"<{word} {top}> is cited, but {wanted.upper()} only takes "
                        f"{word.lower()} {span}",
                         "tags",
                    )
                )

    if having is None:
        return issues

    shown = {}
    for kind in having:
        word = KIND_TAG.get(str(kind or ""))
        if word:
            shown[word] = shown.get(word, 0) + 1

    for word, top in sorted(cited.items()):
        allowed = capacity.get(word, 0) if capacity else top
        if allowed and top > shown.get(word, 0):
            issues.append(
                Issue(
                    WARN,
                    f"<{word} {top}> is cited, but only {shown.get(word, 0)} "
                    f"{word.lower()}(s) reached this node",
                     "tags",
                )
            )

    uncited = []
    for word, count in sorted(shown.items()):
        allowed = capacity.get(word, count) if capacity else count
        for number in range(1, min(count, allowed) + 1):
            if number > cited.get(word, 0):
                uncited.append(f"<{word} {number}>")
    if uncited:
        issues.append(
            Issue(
                WARN,
                f"{', '.join(uncited)} connected but never cited -- the model still "
                "receives it, with no say in what it is for",
                 "tags",
            )
        )
    return issues


def _subjects(sections) -> list:
    """Every defined subject owes the retention analysis a line."""
    defined = set(SUBJECT.findall(str(sections.get("subject_definitions") or "")))
    retained = set(SUBJECT.findall(str(sections.get("retention_analysis") or "")))
    missing = sorted(defined - retained, key=int)
    if not missing:
        return []
    listed = ", ".join(f"<Subject {n}>" for n in missing)
    return [Issue(WARN, f"{listed} defined but absent from retention_analysis", "subjects")]


def _length(body) -> list:
    words = len(body.split())
    low, high = REF_WORDS
    if words and not low <= words <= high:
        return [
            Issue(
                INFO,
                f"detailed_description is {words} words; the guide suggests {low}-{high}",
                 "length",
            )
        ]
    return []
