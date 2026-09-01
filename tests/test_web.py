"""A shape of JavaScript bug that a syntax check cannot see.

`node --check` parses; it does not run. So a module-level `function held()`
that some inner scope also declares as `const held` parses perfectly and then
throws at the first click -- the inner declaration hoists over the whole
function body, and the call at the top of it lands in the temporal dead zone.
That shipped once. This is the guard.

Nothing here is a substitute for a real linter; it is one rule, chosen because
it is the one that has actually bitten, and it costs no dependency.
"""

import pathlib
import re

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web" / "js"

TOP_LEVEL = re.compile(r"^function\s+([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)

def declarations(source: str, name: str) -> list[int]:
    """Line numbers where `name` is bound by const/let/var anywhere in the file."""
    pattern = re.compile(
        r"\b(?:const|let|var)\s+(?:\[[^\]]*\b" + re.escape(name) + r"\b[^\]]*\]"
        r"|\{[^}]*\b" + re.escape(name) + r"\b[^}]*\}"
        r"|" + re.escape(name) + r"\b)"
    )
    return [
        source.count("\n", 0, match.start()) + 1 for match in pattern.finditer(source)
    ]


def modules():
    return sorted(WEB.glob("*.js"))


def test_there_are_modules_to_check():
    assert modules(), "no JavaScript found -- this test would pass vacuously"


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_no_top_level_function_is_shadowed_by_a_binding(path):
    """A module-level function whose name is also bound with const/let/var.

    The inner binding wins for its whole scope, so any call to the function
    from that scope throws before the binding's line is reached -- and reads
    as a call to a function that plainly exists.
    """
    source = path.read_text(encoding="utf-8")
    clashes = {}
    for match in TOP_LEVEL.finditer(source):
        name = match.group(1)
        where = declarations(source, name)
        if where:
            clashes[name] = where
    assert not clashes, (
        f"{path.name}: these module-level functions are also bound as variables, "
        f"which shadows them wherever that binding lives: "
        + "; ".join(f"{name} at line(s) {lines}" for name, lines in clashes.items())
    )
