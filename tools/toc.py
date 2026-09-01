"""Keeping the READMEs' contents lists in step with their headings.

A table of contents written by hand goes stale the first time a section is
added, and a stale one is worse than none: it promises a map and hands you a
wrong one. So it is generated from the headings themselves.

    python tools/toc.py check        say whether either file is out of date
    python tools/toc.py write        rewrite both lists in place

The list covers ``##`` and ``###``. Deeper headings are detail inside a
section rather than places to go, and adding them turns a map into a wall.

Anchors follow GitHub's own rule -- lowercase, drop everything that is not a
letter, digit, underscore, space or hyphen, then spaces to hyphens, then a
numeric suffix for a repeat -- which is what makes the links work on the
rendered page rather than only here.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEADING = re.compile(r"^(#{2,3}) +(.*?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")

FILES = {
    "README.md": {"title": "Contents", "intro": None},
    "README_RU.md": {"title": "Содержание", "intro": None},
}


def anchor(text: str, seen: dict) -> str:
    """The fragment GitHub gives this heading, repeats numbered as it numbers them."""
    slug = re.sub(r"[^\w\s-]", "", text.strip().lower(), flags=re.UNICODE)
    slug = slug.replace(" ", "-")
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return slug if not count else f"{slug}-{count}"


def headings(source: str, skip: str) -> list[tuple[int, str, str]]:
    """``(level, text, anchor)`` for every heading outside a code fence.

    ``skip`` is the contents heading itself: it is numbered like any other for
    anchor purposes, so it has to be walked, but it never lists itself.
    """
    found = []
    seen: dict[str, int] = {}
    fenced = False
    for line in source.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if not match:
            continue
        level, text = len(match.group(1)), match.group(2)
        slug = anchor(text, seen)
        if text == skip:
            continue
        found.append((level, text, slug))
    return found


def render(items) -> str:
    lines = []
    for level, text, slug in items:
        lines.append(f"{'  ' * (level - 2)}- [{text}](#{slug})")
    return "\n".join(lines)


def block(source: str, title: str) -> tuple[int, int] | None:
    """Where the existing contents list starts and ends, or None."""
    lines = source.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == f"## {title}":
            start = index
            break
    if start is None:
        return None
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            return start, index
    return start, len(lines)


def rebuilt(source: str, title: str) -> str:
    listed = render(headings(source, title))
    wanted = f"## {title}\n\n{listed}\n\n"

    where = block(source, title)
    lines = source.splitlines(keepends=True)
    if where:
        start, end = where
        return "".join(lines[:start]) + wanted + "".join(lines[end:])

    for index, line in enumerate(lines):
        if line.startswith("## "):
            return "".join(lines[:index]) + wanted + "".join(lines[index:])
    raise SystemExit(f"no '## ' heading to put the contents before")


def run(write: bool) -> int:
    stale = 0
    for name, how in FILES.items():
        path = os.path.join(ROOT, name)
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        wanted = rebuilt(source, how["title"])
        if wanted == source:
            print(f"{name}: contents up to date")
            continue
        stale += 1
        if not write:
            print(f"{name}: contents OUT OF DATE -- run 'python tools/toc.py write'")
            continue
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(wanted)
        listed = len(headings(wanted, how["title"]))
        print(f"{name}: contents rewritten, {listed} entries")
    return 1 if (stale and not write) else 0


def main(argv) -> int:
    if len(argv) != 2 or argv[1] not in ("check", "write"):
        print(__doc__.strip().splitlines()[0])
        print("usage: python tools/toc.py {check|write}")
        return 2
    return run(argv[1] == "write")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
