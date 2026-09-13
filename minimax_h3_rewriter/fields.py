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


BODY_FIELDS = ("detailed_description", OUTPUT_FIELDS[0])


def offsets(text: str, names: tuple[str, ...] = ALL_FIELDS) -> dict[str, int]:
    """Where each labelled field's content begins, keyed by lowercased name.

    ``split_sections`` answers what each field says; this answers where each
    one starts, which is a different question. Something that has to put a few
    characters into a prompt and hand the rest of it on untouched cannot go
    through the sections: rebuilding the text from them would silently reformat
    every other field on the way past.

    An offset lands after the label and after whatever spaces follow the colon,
    so text inserted there opens the field. It may sit directly on a newline
    when the field starts on the line below its label.

    A label written twice keeps its first position: a model that restates a
    field is still answering it once, and the opening is where it opened.
    """
    seen: dict[str, int] = {}
    for match in _pattern(names).finditer(text or ""):
        seen.setdefault(match.group(1).lower(), match.end())
    return seen


def field_offset(
    text: str,
    name: str | tuple[str, ...],
    names: tuple[str, ...] = ALL_FIELDS,
) -> int | None:
    """Where one field begins inside ``text``, or None if it is not labelled.

    ``name`` may be several names, in which case the first of them the text
    actually carries wins. That is not a convenience: the field that holds the
    description is called one thing in Ref2VA and another everywhere else, and
    a caller who wants "the description" wants whichever of the two is here.
    """
    wanted = (name,) if isinstance(name, str) else tuple(name)
    seen = offsets(text, names)
    for candidate in wanted:
        found = seen.get(candidate.lower())
        if found is not None:
            return found
    return None


def body_offset(text: str, names: tuple[str, ...] = ALL_FIELDS) -> int | None:
    """Where the description begins inside ``text`` itself, or None if unlabelled."""
    return field_offset(text, BODY_FIELDS, names)


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
