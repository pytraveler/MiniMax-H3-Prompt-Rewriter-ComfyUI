"""The READMEs' own links: the contents list, and every jump inside the page.

Both go stale silently. A contents list written before a section was added
still renders, still looks authoritative, and quietly omits the thing you were
looking for; a cross-reference to a heading that has since been reworded lands
you at the top of a two-thousand-line page. Neither shows up in a diff.
"""

import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("mmx_toc", ROOT / "tools" / "toc.py")
toc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(toc)

INTERNAL = re.compile(r"\]\(#([^)]+)\)")

READMES = sorted(toc.FILES)


@pytest.mark.parametrize("name", READMES)
def test_the_contents_list_is_current(name):
    """Every heading is listed, in order, under the anchor it actually has."""
    source = (ROOT / name).read_text(encoding="utf-8")
    assert toc.rebuilt(source, toc.FILES[name]["title"]) == source, (
        f"{name}: the contents list no longer matches the headings -- "
        "run 'python tools/toc.py write'"
    )


@pytest.mark.parametrize("name", READMES)
def test_every_link_into_the_page_lands_somewhere(name):
    """Including the ones written by hand, which outlive the headings they name."""
    source = (ROOT / name).read_text(encoding="utf-8")
    anchors = {slug for _level, _text, slug in toc.headings(source, skip="")}
    broken = sorted({found for found in INTERNAL.findall(source) if found not in anchors})
    assert not broken, f"{name}: these links point at no heading: {broken}"


@pytest.mark.parametrize("name", READMES)
def test_the_contents_list_is_not_empty(name):
    """A rule that passes on an empty file is a rule that never ran."""
    source = (ROOT / name).read_text(encoding="utf-8")
    assert len(toc.headings(source, toc.FILES[name]["title"])) > 20
