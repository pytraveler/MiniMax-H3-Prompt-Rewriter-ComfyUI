"""Editing a saved record, and checking the text a person typed into one.

The package is registered by hand, as in test_checks: importing it for real
would run ``__init__.py`` and pull in ComfyUI. ``library.root`` is redirected
at a temporary folder, so a test run never touches a real prompt set.
"""

import importlib
import json
import pathlib
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
_ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / _PKG)]
    sys.modules[_PKG] = package

library = importlib.import_module(f"{_PKG}.library")

FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")

BODY = "[Shot 1] A field at dawn. [Shot 2] At 00:04.000 the light turns."
GOOD = "\n\n".join(
    [
        f"integrated_multimodal_description: {BODY}",
        "overall_soundscape: Wind in dry grass.",
        "non_diegetic_music: N/A",
    ]
)


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    """An empty prompt set on disk, with one record in it."""
    monkeypatch.setattr(library, "root", lambda: str(tmp_path))
    library.add(
        "global",
        {
            "name": "Dawn",
            "description": "a field",
            "groups": ["outdoor"],
            "task": "T2VA",
            "node_class": "MiniMaxH3PromptRewriter",
            "about": {"duration": 10},
            "text": GOOD,
            "sections": ["Wind in dry grass.", "N/A"],
            "references": [],
        },
    )
    return library.load("global")["records"][0]["id"]


def test_edit_changes_only_what_it_is_given(shelf):
    library.edit("global", shelf, {"name": "Dusk"})
    record = library.find("global", shelf)
    assert record["name"] == "Dusk"
    assert record["description"] == "a field"
    assert record["text"] == GOOD
    assert record["node_class"] == "MiniMaxH3PromptRewriter"


def test_edit_keeps_the_account_of_the_run(shelf):
    before = library.find("global", shelf)
    library.edit("global", shelf, {"text": "something else entirely"})
    after = library.find("global", shelf)
    for key in ("id", "saved_at", "node_class", "task", "about", "references"):
        assert after[key] == before[key]


def test_editing_the_text_drops_the_stale_split(shelf):
    library.edit("global", shelf, {"text": GOOD.replace("dawn", "dusk")})
    assert "sections" not in library.find("global", shelf)


def test_an_unchanged_text_keeps_the_split(shelf):
    library.edit("global", shelf, {"text": GOOD, "name": "Dawn again"})
    record = library.find("global", shelf)
    assert record["sections"] == ["Wind in dry grass.", "N/A"]
    assert record["name"] == "Dawn again"


def test_a_change_that_changes_nothing_is_not_recorded(shelf):
    library.edit("global", shelf, {"name": "Dawn", "text": GOOD})
    assert "edited_at" not in library.find("global", shelf)


def test_a_real_change_is_stamped(shelf):
    library.edit("global", shelf, {"name": "Dusk"})
    assert library.find("global", shelf)["edited_at"] > 0


def test_groups_are_trimmed_and_emptied(shelf):
    library.edit("global", shelf, {"groups": ["  night ", "", "  "]})
    assert library.find("global", shelf)["groups"] == ["night"]


def test_a_blank_name_falls_back(shelf):
    library.edit("global", shelf, {"name": "   "})
    assert library.find("global", shelf)["name"] == "Untitled"


def test_an_unknown_id_is_not_an_error(shelf):
    assert library.edit("global", "nosuchrecord", {"name": "x"}) is None


def test_the_file_stays_readable(shelf):
    library.edit("global", shelf, {"text": "rewritten"})
    with open(library.path("global"), encoding="utf-8") as handle:
        assert json.load(handle)["records"][0]["text"] == "rewritten"


def test_inspect_passes_a_good_answer():
    assert library.inspect(GOOD, task="T2VA", duration=10, having=[]) == []


def test_inspect_finds_a_cut_past_the_end():
    said = library.inspect(GOOD, task="T2VA", duration=4, having=[])
    assert any("past the 4s end" in issue["message"] for issue in said)


def test_inspect_carries_the_rule_that_found_it():
    said = library.inspect("integrated_multimodal_description: no shots here", task="T2VA")
    assert {"fields", "shots"} <= {issue["code"] for issue in said}


def test_inspect_uses_the_reference_fields_for_a_reference_task():
    said = library.inspect("detailed_description: [Shot 1] A room.", task="Ref2VA", duration=10)
    absent = [issue for issue in said if issue["code"] == "fields"]
    assert absent and "subject_definitions" in absent[0]["message"]


def test_inspect_of_nothing_says_so():
    assert [issue["code"] for issue in library.inspect("", task="T2VA")] == ["empty"]


def pick(record_id, file="global"):
    return json.dumps({"file": file, "id": record_id})


def test_stamp_is_empty_when_no_saved_prompt_is_in_play(shelf):
    assert library.stamp("", True) == ""
    assert library.stamp(pick(shelf), False) == ""


def test_stamp_follows_the_text(shelf):
    before = library.stamp(pick(shelf), True)
    library.edit("global", shelf, {"text": "rewritten"})
    assert library.stamp(pick(shelf), True) != before


def test_stamp_ignores_what_never_reaches_an_output(shelf):
    before = library.stamp(pick(shelf), True)
    library.edit("global", shelf, {"name": "Dusk", "description": "later", "groups": ["x"]})
    assert library.stamp(pick(shelf), True) == before


def test_stamp_reports_a_record_that_has_gone(shelf):
    assert library.stamp(pick("gone00000000"), True) == "missing:gone00000000"


def test_stamp_survives_a_pick_it_cannot_read(shelf):
    for raw in ("not json", "{}", "null", '{"file": "global"}'):
        assert library.stamp(raw, True) == ""
