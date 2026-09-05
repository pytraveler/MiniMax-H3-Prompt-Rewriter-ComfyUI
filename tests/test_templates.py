"""The example workflows, read as files rather than opened in a browser.

Everything checked here went wrong once in the workflow that shipped before
these, and none of it shows in a diff: a graph saved at the zoom it happened to
be left at, a loader still naming a picture from the machine it was built on,
a node parked with its outputs going nowhere, a note pointing at a template
that has since been renamed. A workflow is data, so all of it is readable
without ComfyUI running.

The card images are checked for existence only. What they *look* like is not
something a test can hold, and `python tools/template_cards.py` draws them.
"""

import importlib
import json
import pathlib
import re
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "example_workflows"

if _PKG not in sys.modules:
    _package = types.ModuleType(_PKG)
    _package.__path__ = [str(ROOT / _PKG)]
    sys.modules[_PKG] = _package

catalog = importlib.import_module(f"{_PKG}.catalog")

OFFERED = {
    entry.label
    for section in catalog.SECTIONS
    for entry in catalog._entries(catalog._seed(), section)
}

LOOKS_LIKE_A_MODEL = re.compile(r"GB download|^on disk: ")

NAMED = re.compile(r"^\d+ - [A-Za-z0-9 ,'()-]+$")

POINTER = re.compile(r"\*{1,2}(\d+ - [^*\n]+?)\*{1,2}")

MUTED, BYPASSED = 2, 4
SHOWS_ITS_RESULT = {"PreviewAny", "SaveVideo", "SaveImage", "SaveAudio"}

FILES = sorted(WORKFLOWS.glob("*.json"))
NAMES = [path.stem for path in FILES]


def graph(name):
    return json.loads((WORKFLOWS / f"{name}.json").read_text(encoding="utf-8"))


def ours(node):
    """A node from this pack. None of them is an output node."""
    return str(node.get("type", "")).startswith("MiniMaxH3")


def test_there_are_templates_at_all():
    assert FILES, f"no workflows in {WORKFLOWS}"


@pytest.mark.parametrize("name", NAMES)
def test_the_file_name_is_the_card(name):
    """It is the title, the description and the sort order, all three."""
    assert NAMED.match(name), (
        f"'{name}': the template browser shows this name and nothing else, so it "
        "reads as '<number> - <plain words>'"
    )


@pytest.mark.parametrize("name", NAMES)
def test_the_card_picture_is_there(name):
    """Without it the card in the browser is a blank rectangle."""
    assert (WORKFLOWS / f"{name}.jpg").is_file(), (
        f"'{name}': no card picture -- run 'python tools/template_cards.py'"
    )


def test_no_picture_is_left_behind():
    """A renamed workflow leaves its old card sitting there, still listed."""
    orphans = [
        path.name
        for path in sorted(WORKFLOWS.glob("*.jpg"))
        if not (WORKFLOWS / f"{path.stem}.json").is_file()
    ]
    assert not orphans, f"card pictures with no workflow beside them: {orphans}"


@pytest.mark.parametrize("name", NAMES)
def test_it_opens_at_a_scale_it_can_be_read_at(name):
    """The view is saved with the file, and a graph left zoomed out stays so."""
    view = (graph(name).get("extra") or {}).get("ds") or {}
    scale = view.get("scale")
    assert isinstance(scale, (int, float)), f"'{name}': no saved view"
    assert scale >= 0.4, (
        f"'{name}': saved at {scale:.2f} zoom, which opens as unreadable boxes"
    )


@pytest.mark.parametrize("name", NAMES)
def test_nothing_is_switched_off_on_open(name):
    """A bypassed node in a template reads as broken, not as optional."""
    asleep = [
        f"{node.get('id')} {node.get('type')}"
        for node in graph(name)["nodes"]
        if node.get("mode") in (MUTED, BYPASSED)
    ]
    assert not asleep, f"'{name}': muted or bypassed on open: {asleep}"


@pytest.mark.parametrize("name", NAMES)
def test_every_node_of_ours_feeds_something(name):
    """None of this pack's nodes is an output node, so an unwired one never runs."""
    stranded = [
        f"{node.get('id')} {node.get('type')}"
        for node in graph(name)["nodes"]
        if ours(node)
        and not any(slot.get("links") for slot in node.get("outputs") or ())
    ]
    assert not stranded, (
        f"'{name}': nothing reads from {stranded}, so they sit there and never run"
    )


@pytest.mark.parametrize("name", NAMES)
def test_something_shows_the_result(name):
    """A chain that ends in nothing is a run with nothing to look at."""
    types = {node.get("type") for node in graph(name)["nodes"]}
    assert types & SHOWS_ITS_RESULT, (
        f"'{name}': no preview and no save, so a run displays nothing"
    )


@pytest.mark.parametrize("name", NAMES)
def test_the_loaders_name_no_file(name):
    """A picture named in a template is a picture nobody else has."""
    named = [
        node.get("widgets_values")
        for node in graph(name)["nodes"]
        if node.get("type") in {"LoadImage", "LoadAudio", "LoadVideo"}
        and (node.get("widgets_values") or [""])[0]
    ]
    assert not named, (
        f"'{name}': a loader ships pointing at {named}, which is on your machine "
        "and no one else's"
    )


@pytest.mark.parametrize("name", NAMES)
def test_every_model_named_is_one_the_catalogue_still_offers(name):
    """The label is the value, download size and all, so rewording one breaks this.

    An 'on disk:' entry fails here too, and should: those are the files found in
    the model folders of the machine the template was saved on.
    """
    unknown = sorted(
        {
            value
            for node in graph(name)["nodes"]
            if node.get("type") != "MarkdownNote"
            for value in node.get("widgets_values") or []
            if isinstance(value, str)
            and LOOKS_LIKE_A_MODEL.search(value)
            and value not in OFFERED
        }
    )
    assert not unknown, (
        f"'{name}': names models the catalogue does not offer under that label: {unknown}"
    )


@pytest.mark.parametrize("name", NAMES)
def test_a_note_points_at_a_template_that_exists(name):
    """Notes send people to the next template by name, and names get edited."""
    known = set(NAMES)
    missing = sorted(
        {
            pointer.strip()
            for node in graph(name)["nodes"]
            if node.get("type") == "MarkdownNote"
            for pointer in POINTER.findall((node.get("widgets_values") or [""])[0])
            if pointer.strip() not in known
        }
    )
    assert not missing, f"'{name}': its note names templates that are not here: {missing}"


@pytest.mark.parametrize("name", NAMES)
def test_nothing_machine_specific_travelled_with_it(name):
    """Saving carries whatever the editor had set that day."""
    extra = graph(name).get("extra") or {}
    strangers = sorted(key for key in extra if key not in {"ds", "frontendVersion"})
    assert not strangers, (
        f"'{name}': 'extra' carries {strangers}, which belong to the machine it "
        "was saved on"
    )


@pytest.mark.parametrize("name", NAMES)
def test_no_path_from_this_machine_is_written_into_it(name):
    """A drive letter in a workflow is a workflow that only ever worked here."""
    source = (WORKFLOWS / f"{name}.json").read_text(encoding="utf-8")
    found = sorted(set(re.findall(r"[A-Za-z]:\\\\[^\"]{0,60}", source)))
    assert not found, f"'{name}': absolute paths written into it: {found}"
