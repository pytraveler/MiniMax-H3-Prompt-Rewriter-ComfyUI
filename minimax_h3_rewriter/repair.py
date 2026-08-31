"""Acting on what the self-check found, when the person asked for it.

Two layers, both under the Options node's 'fix_once', and both off by default:
the answer a node returns is what the model wrote unless you say otherwise.

**What the node can put right itself.** The alignment line of the frame tasks is
a fixed sentence the node already knows -- it formats one when it builds the
prompt. A model that dropped it has not made a judgement worth respecting, so
the line goes back on, deterministically, with no model and no risk.

**One re-run, never a loop.** For the mechanical findings -- a cut time past the
end, an unbalanced ``<d>``, a tag pointing at a reference that is not there --
the writer is asked once more, with those findings folded into the prompt as
constraints. Once, not until clean: a model that ignored a rule twice will
ignore it a third time, and each attempt costs a full generation.

Three things keep that safe. The re-run is refused outright on an answer too
broken to rescue, because a model that cannot hold the format will not hold it
on the second pass either. The constraints travel inside the prompt rather than
as a second conversational turn, which keeps the trained single-turn shape the
LoRAs were built on. And the result is kept only if it is actually better --
otherwise the first answer stands. The worst case is a minute spent, never a
worse prompt.
"""

from __future__ import annotations

from . import checks

FIXABLE = ("fields", "shots", "dialogue", "tags", "subjects")

HOPELESS_SHARE = 0.5

INSTRUCTION = (
    "A previous attempt at this prompt broke the following rules. Write it "
    "again, obeying every one of them:"
)


def hopeless(issues, sections, names) -> bool:
    """Is this answer past the point where asking again could help?"""
    if any(issue.code == "empty" for issue in issues):
        return True
    if not names:
        return False
    absent = sum(1 for name in names if not str((sections or {}).get(name) or "").strip())
    return absent >= max(1, len(names) * HOPELESS_SHARE)


def fixable(issues) -> list:
    """The findings worth spending one more generation on."""
    return [issue for issue in issues if issue.code in FIXABLE]


def instruct(issues) -> str:
    """Those findings as constraints, to be appended to the writer's prompt.

    Phrased as rules to obey rather than as complaints about an answer the
    model is not being shown: it is writing afresh, not editing.
    """
    wanted = fixable(issues)
    if not wanted:
        return ""
    lines = "\n".join(f"- {issue.message}" for issue in wanted)
    return f"\n\n{INSTRUCTION}\n{lines}"


def score(issues) -> tuple:
    """Warnings first, then everything -- lower is better."""
    warnings = sum(1 for issue in issues if issue.level == checks.WARN)
    return warnings, len(issues)


def better(before, after) -> bool:
    """Is the second answer an improvement worth keeping?

    Ties keep the first answer. A re-run that merely swapped one warning for
    another has not earned the replacement, and the first is what the person
    has already seen going past.
    """
    return score(after) < score(before)


def restore_alignment(text: str, line: str) -> str:
    """Put a missing alignment line back at the top of an answer.

    The line is the caller's, already formatted for the duration and the final
    shot -- this only decides that it is absent and where it goes.
    """
    body = (text or "").lstrip()
    if not line or not body:
        return text
    return f"{line}\n\n{body}"


def final_shot(body: str) -> int:
    """The highest shot number in a description, for the alignment line's N."""
    highest = 0
    for match in checks.SHOT.finditer(body or ""):
        highest = max(highest, int(match.group(1)))
    return highest or 1
