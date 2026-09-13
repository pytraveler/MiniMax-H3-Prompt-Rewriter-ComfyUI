"""LoRA trigger words, and where in a finished prompt each one belongs.

Some LoRA adapters do nothing at all unless a particular word is in the prompt
-- the word their captions were prefixed with while they were trained. No other
node in this pack knows that. The writers write prose, the reducer shortens it,
the self-check judges it, and none of the three has any reason to keep a word
whose whole purpose is to wake an adapter that lives further down the graph. So
the word gets typed in by hand after every run, and lost on the next one.

This module is the list, and the putting of it into a prompt. Nothing in it
reads a LoRA file. The word is almost never in one -- of forty-four adapters on
the machine this was written on, a single file carried ``trigger_word`` and one
more carried an empty ``trigger_words`` -- and an adapter often answers to
several words anyway, where the metadata has room for one. The list is the
user's, and that is the design rather than a shortfall.

Two rules shape everything below.

**The prompt is passed through character for character.** The same promise the
effect embeddings node makes: what a writing node produced is what H3 was meant
to read, so nothing here re-wraps a line, re-labels a field or rebuilds a text
out of its parsed sections.

**Presence is checked, never removal.** ``embeddings.clear`` can take its own
tokens back out because there are ten of them and it knows all ten by name.
These words are somebody else's, and a word like ``detail`` cutting itself out
of a description would eat the prose it was sitting in. So a word already in
the text is not added a second time, and switching a row off stops it being
added rather than taking it away -- the word may well have come from the writer.

Nothing here imports ComfyUI, so all of it can be tested on its own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .constants import OUTPUT_FIELDS
from .fields import ALL_FIELDS, BODY_FIELDS, offsets, split_sections

BODY = "start of the description"
TOP = "top of the prompt"
SOUND = "start of the soundscape"
MUSIC = "start of the music"
END = "end of the prompt"
PLACEMENTS = (BODY, TOP, SOUND, MUSIC, END)

FIELDS = {
    BODY: BODY_FIELDS,
    SOUND: (OUTPUT_FIELDS[1],),
    MUSIC: (OUTPUT_FIELDS[2],),
}

STOPS = ".,;:!?-"

SEPARATOR = ", "

NOTHING = frozenset({"", "-", "--", "n/a", "na", "none", "nil", "null"})


@dataclass(frozen=True)
class Trigger:
    """One row of the list: an adapter, its words, and where they go."""

    name: str
    words: str
    where: str
    on: bool

    @property
    def pieces(self) -> tuple[str, ...]:
        """The row's words as the separate triggers they may be.

        One row is one adapter, and an adapter can answer to more than one
        word. Commas are how anybody writes those, so commas are the split.

        It matters beyond tidiness: the check for "already in the prompt" runs
        per piece, and a row that went in as a whole would be either skipped
        entirely because one of its words is in the text, or added entire and
        duplicate that word. Neither is what was asked for.
        """
        return tuple(part.strip() for part in self.words.split(",") if part.strip())

    @property
    def title(self) -> str:
        """What to call the row when saying something about it out loud."""
        return self.name or self.words


def _row(item) -> Trigger | None:
    if isinstance(item, str):
        words = item.strip()
        return Trigger("", words, BODY, True) if words else None
    if not isinstance(item, dict):
        return None

    words = str(item.get("words") or "").strip()
    if not words:
        return None

    where = str(item.get("where") or BODY)
    if where not in PLACEMENTS:
        where = BODY

    raw = item.get("on", True)
    return Trigger(
        name=str(item.get("name") or "").strip(),
        words=words,
        where=where,
        on=bool(True if raw is None else raw),
    )


def entries(value) -> tuple[Trigger, ...]:
    """What the workflow has in its list, in the order the list has it.

    The widget holds a JSON array of ``{name, words, where, on}`` objects, and
    that is what a saved workflow carries. Two friendlier shapes are read as
    well, because they are what somebody driving the API by hand writes first
    and refusing them would be a puzzle rather than a rule: an array of plain
    strings is a list of trigger words, and text that is not JSON at all is one
    row's worth of words.

    A row with nothing in its ``words`` is dropped rather than carried as an
    empty one. The list is allowed to hold a half-typed row while somebody is
    typing it; a run is not the moment to act on it.
    """
    if isinstance(value, (list, tuple)):
        data = list(value)
    else:
        text = value if isinstance(value, str) else ""
        try:
            data = json.loads(text or "[]")
        except (TypeError, ValueError):
            data = [text]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            data = []

    found = (_row(item) for item in data)
    return tuple(row for row in found if row is not None)


def _wordish(char: str) -> bool:
    return char.isalnum() or char == "_"


def _phrase(words: str) -> re.Pattern | None:
    """A matcher for one trigger, forgiving about the whitespace inside it.

    Boundaries rather than a plain substring test: "man" must not be found
    inside "woman", or a trigger would be judged present because an unrelated
    word contains it and would never be added at all.

    The boundary is only asked for where it means something. A trigger that
    opens or closes on punctuation -- and plenty do, they come out of tag
    training sets -- has no word boundary there, and demanding one would make
    it unfindable, which fails the other way: added again on every run.
    """
    parts = [re.escape(part) for part in words.split()]
    if not parts:
        return None
    lead = r"(?<!\w)" if _wordish(words[0]) else ""
    tail = r"(?!\w)" if _wordish(words[-1]) else ""
    return re.compile(lead + r"\s+".join(parts) + tail, re.IGNORECASE)


def present(text: str, words: str) -> bool:
    """Is this trigger already in the prompt?

    Case is ignored. A trigger is a word the tokenizer has to see, not a
    spelling the writer has to match, and adding "ohwx man" to a text that
    already opens on "Ohwx man" would be this node arguing with itself.
    """
    pattern = _phrase((words or "").strip())
    return bool(pattern and pattern.search(text or ""))


def _is_nothing(content: str) -> bool:
    """Does this field say, in one of the usual ways, that it is empty?"""
    return (content or "").strip().strip(".").lower() in NOTHING


def _field_here(text: str, placement: str) -> tuple[str, int | None]:
    """Which field this placement opens in *this* prompt, and where.

    ``("", None)`` when the prompt carries no label for it. The name comes back
    alongside the offset because the caller wants to read what is already in
    the field, and only one of the two names a placement may carry is here.
    """
    marks = offsets(text, ALL_FIELDS)
    for candidate in FIELDS[placement]:
        found = marks.get(candidate.lower())
        if found is not None:
            return candidate, found
    return "", None


def _closed(block: str, tail: str) -> str:
    """The block with whatever it needs to not run into what follows it."""
    if block[-1:] not in STOPS:
        block += "."
    if tail[:1] and not tail[:1].isspace():
        block += " "
    return block


def insert(text: str, rows) -> tuple[str, tuple[str, ...], list[tuple[str, str]]]:
    """Put the switched-on triggers into ``text``.

    Returns the prompt, the words this run actually added, and the findings as
    ``(level, message)`` pairs.

    Everything is measured against the text as it arrived and the edits are
    then applied back to front, so an insertion near the top cannot move a
    later one out from under itself. Two rows pointing at the same section are
    one insertion, joined by commas in the order the list has them.
    """
    text = text or ""
    findings: list[tuple[str, str]] = []
    added: list[str] = []
    wanted: dict[str, list[str]] = {}
    seen: set[str] = set()

    for row in rows:
        if not row.on:
            continue
        for piece in row.pieces:
            if piece.lower() in seen:
                continue
            seen.add(piece.lower())
            if present(text, piece):
                findings.append((
                    "info",
                    f"'{piece}' is already in the prompt, so it was left where it is. "
                    f"Nothing is taken out of a text this node did not write.",
                ))
                continue
            wanted.setdefault(row.where, []).append(piece)
            added.append(piece)

    if not added:
        return text, (), findings

    if not text.strip():
        findings.append((
            "warn",
            "the prompt coming in is empty, so the trigger words are the whole of what "
            "goes out. Usually that means the input is not connected to anything yet.",
        ))
        return SEPARATOR.join(added), tuple(added), findings

    edits: list[tuple[int, str]] = []
    top: list[str] = []
    end: list[str] = []
    sections: dict[str, str] | None = None

    for placement, pieces in wanted.items():
        if placement == TOP:
            top.extend(pieces)
            continue
        if placement == END:
            end.extend(pieces)
            continue

        listed = SEPARATOR.join(pieces)
        field, offset = _field_here(text, placement)
        if offset is None:
            findings.append((
                "info",
                f"this prompt has no '{placement}' to open, so '{listed}' went to the "
                f"{TOP} instead.",
            ))
            top.extend(pieces)
            continue

        if sections is None:
            sections = split_sections(text, ALL_FIELDS)[1]
        said = sections.get(field, "")
        if _is_nothing(said):
            findings.append((
                "info",
                f"'{field}' says '{said}' in this prompt, so it now reads "
                f"'{listed}. {said}' -- a field naming something and saying there is "
                f"none of it. The words are in and nothing was taken out, but "
                f"'{TOP}' or '{END}' may be the better home for them.",
            ))
        edits.append((offset, listed))

    for offset, block in sorted(edits, reverse=True):
        head, tail = text[:offset], text[offset:]
        if head and not head[-1:].isspace():
            head += " "
        text = head + _closed(block, tail) + tail

    if top:
        text = SEPARATOR.join(top) + "\n\n" + text
    if end:
        text = text.rstrip() + "\n\n" + SEPARATOR.join(end)

    return text, tuple(added), findings
