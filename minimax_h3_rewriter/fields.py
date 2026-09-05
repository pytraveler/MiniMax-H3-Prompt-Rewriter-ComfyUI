"""Splitting a rewrite into the labelled sections it is supposed to contain.

Three fields for the T2VA family, six for full-reference mode. A model that
ignored the output contract still has to yield something usable downstream, so
an unlabelled answer lands whole in the body field rather than nowhere.
"""

from __future__ import annotations

import re

from .constants import OUTPUT_FIELDS, REF_OUTPUT_FIELDS

ALL_FIELDS = (
    OUTPUT_FIELDS[0],
    *REF_OUTPUT_FIELDS[:4],
    *OUTPUT_FIELDS[1:],
)

_PATTERNS: dict[tuple[str, ...], re.Pattern] = {}


def _pattern(names: tuple[str, ...]) -> re.Pattern:
    """A label matcher for one set of field names, built once per set.

    The leading character class forgives the decoration a general-purpose model
    adds when it is halfway through obeying the contract: bold, a heading, a
    quote marker, a list bullet.
    """
    cached = _PATTERNS.get(names)
    if cached is None:
        cached = re.compile(
            r"^[ \t]*[*_#>\-\s]*(" + "|".join(re.escape(name) for name in names) + r")[*_ \t]*:[ \t]*",
            re.IGNORECASE | re.MULTILINE,
        )
        _PATTERNS[names] = cached
    return cached


def body_field(names: tuple[str, ...] = OUTPUT_FIELDS) -> str:
    """Which of these fields carries the description everything else hangs off.

    Ref2VA calls it detailed_description and puts three shorter fields in front
    of it; every other task has it first. The shot list, the dialogue and the
    word count are all read out of this one, so the rule lives here rather than
    being restated wherever a set of names is split.
    """
    if "detailed_description" in names:
        return "detailed_description"
    return names[0] if names else ""


def split_sections(
    text: str,
    names: tuple[str, ...] = OUTPUT_FIELDS,
    fallback: str = "",
) -> tuple[str, dict[str, str]]:
    """Return ``(text before the first label, {name: section})``.

    The head is where the alignment instruction of I2VA, FL2VA and L2VA lives:
    it belongs to the prompt but is not one of the fields.
    """
    result = {name: "" for name in names}
    matches = list(_pattern(names).finditer(text or ""))

    if not matches:
        result[fallback or names[0]] = (text or "").strip()
        return "", result

    head = (text[: matches[0].start()] or "").strip()
    for index, match in enumerate(matches):
        name = match.group(1).lower()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[name] = text[match.end():end].strip()
    return head, result


def split_fields(
    text: str,
    names: tuple[str, ...] = OUTPUT_FIELDS,
    fallback: str = "",
) -> dict[str, str]:
    """The labelled sections of a rewrite, without the leading instruction."""
    return split_sections(text, names, fallback)[1]


def missing(sections: dict[str, str], names: tuple[str, ...] = OUTPUT_FIELDS) -> list[str]:
    """Which required fields the answer did not actually fill in."""
    return [name for name in names if not sections.get(name)]
