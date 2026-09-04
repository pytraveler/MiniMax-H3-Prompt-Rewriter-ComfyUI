"""The bundled presets: that they are there, readable, and still our own format.

These read the two files that ship in the pack, so they are also the check that
a rebuild did not quietly change what is in them -- a prompt that no longer
passes the pack's own self-check, or a frame that decodes to nothing, is a bad
build, and nothing else in the repository would notice.

The package is registered by hand, as in test_library: importing it for real
would run ``__init__.py`` and pull in ComfyUI.
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

presets = importlib.import_module(f"{_PKG}.presets")
library = importlib.import_module(f"{_PKG}.library")

pytestmark = pytest.mark.skipif(
    not presets.catalog(), reason="the pack was installed without the preset files"
)


@pytest.fixture(scope="module")
def records():
    return presets.catalog()["records"]


def test_the_catalogue_and_the_frames_describe_the_same_presets(records):
    assert set(presets.thumbs()) == {record["id"] for record in records}


def test_every_preset_carries_a_frame_of_real_webp():
    for preset_id, frame in presets.thumbs().items():
        assert frame[:4] == b"RIFF" and frame[8:12] == b"WEBP", preset_id
        assert len(frame) > 400, preset_id


def test_a_preset_reads_as_the_answer_a_writer_would_have_given(records):
    """The whole point of the format: the library cannot tell the two apart."""
    for record in records[::20]:
        body = presets.text(record)
        assert body.startswith("integrated_multimodal_description: ")
        assert "\noverall_soundscape: " in body
        assert "\nnon_diegetic_music: " in body
        assert not library.inspect(
            body, task=presets.TASK, duration=round(record["seconds"]), having=[]
        )


def test_the_collection_asks_for_no_references(records):
    """It is T2VA throughout, and a window must not offer it as anything else."""
    for record in records:
        assert "<Picture " not in record["description"]
        assert "<Video " not in record["description"]
        assert "<Audio " not in record["description"]


def test_a_preset_dressed_as_a_library_record_is_one(records):
    made = presets.as_record(records[0])
    assert set(made) >= {"name", "description", "groups", "task", "text", "sections", "references"}
    assert made["kind"] == "preset"
    assert made["task"] == presets.TASK
    assert len(made["sections"]) == 3
    assert made["references"] == []
    assert made["source"]["id"] == records[0]["id"]
    assert made["source"]["video"]["mirror"].endswith(f"/{records[0]['id']}.mp4")


def test_a_saved_copy_says_on_its_face_where_it_came_from(records):
    """The credit rides in the description, which the library window draws."""
    made = presets.as_record(records[0])
    assert records[0]["id"] in made["description"]
    assert "ostris" in made["description"]
    assert "huggingface.co" in made["description"]


def test_a_saved_copy_takes_what_the_person_typed(records):
    made = presets.as_record(
        records[0], name="  Mine  ", tags=["Night", "  ", "Chase"], prompt="one: two"
    )
    assert made["name"] == "Mine"
    assert made["groups"] == ["Night", "Chase"]
    assert made["text"] == "one: two"
    assert presets.as_record(records[0], description="   ")["description"]


def test_the_sections_follow_the_text_that_is_actually_saved(records):
    """Edited on the way in, the split has to be redone or the outputs disagree."""
    edited = presets.text(records[0]).replace("overall_soundscape: ", "overall_soundscape: Rain. ")
    made = presets.as_record(records[0], prompt=edited)
    assert made["sections"][1].startswith("Rain. ")
    assert made["sections"][0] == records[0]["description"]
    assert made["sections"][2] == records[0]["music"]


def test_a_saved_preset_is_an_ordinary_library_record(records, tmp_path, monkeypatch):
    """And an edit keeps its provenance -- the roadmap's question about `kind`."""
    monkeypatch.setattr(library, "root", lambda: str(tmp_path))
    saved = library.add("global", presets.as_record(records[0]))
    found = library.find("global", saved["id"])
    assert found["kind"] == presets.KIND
    assert found["source"]["id"] == records[0]["id"]
    assert found["task"] == presets.TASK
    assert len(found["sections"]) == 3

    library.edit("global", saved["id"], {"name": "Renamed"})
    again = library.find("global", saved["id"])
    assert again["name"] == "Renamed"
    assert again["kind"] == presets.KIND
    assert again["source"] == found["source"]
    assert again["sections"] == found["sections"]


def test_a_preset_files_under_its_style_and_its_subjects(records):
    labelled = next(record for record in records if record.get("style"))
    found = presets.groups(labelled)
    assert found and found[0] == presets.catalog()["styles"][labelled["style"]]
    assert len(found) == 1 + len(labelled["topics"])


def test_the_credit_survives_a_rebuild():
    """It is the condition the collection is carried under, not decoration."""
    credit = presets.catalog()["credit"]
    assert "huggingface.co/datasets/ostris/minimax_h3_1k" in credit["prompts"]["url"]
    assert credit["prompts"]["who"] == "ostris"
    assert credit["tags"]["url"]


def test_the_node_hands_on_eight_values_in_the_order_its_schema_declares(records):
    """The node is thin on purpose; this is where that order is actually pinned."""
    record = records[0]
    made = presets.outputs(record)
    assert len(made) == 8
    prompt, description, soundscape, music, seconds, width, height, where = made
    assert prompt == presets.text(record)
    assert description == record["description"]
    assert soundscape == record["soundscape"]
    assert music == record["music"]
    assert isinstance(seconds, float) and seconds > 0
    assert isinstance(width, int) and isinstance(height, int)
    assert (width > height) == (record["aspect"] == "landscape")
    assert record["id"] in where
    assert "huggingface.co" in where and "hf-mirror.com" in where
    assert presets.NOTICE in where


def test_a_preset_says_which_collection_and_which_look_it_is(records):
    labelled = next(record for record in records if record.get("style"))
    said = presets.label(labelled)
    assert labelled["id"] in said
    assert presets.catalog()["styles"][labelled["style"]] in said


def test_one_preset_carries_what_a_reloaded_page_needs(records):
    found = presets.one(records[3]["id"])
    assert found["label"] and found["text"] == presets.text(records[3])
    assert set(found["video"]) == {"huggingface", "mirror"}
    assert found["groups"] == presets.groups(records[3])
    assert presets.one("nope") is None


def test_the_fingerprint_moves_with_the_collection_not_only_with_the_number(records):
    """A rebuild keeps the numbers and can change every word behind them."""
    first = presets.stamp(records[0]["id"])
    assert first == presets.stamp(records[0]["id"])
    assert first != presets.stamp(records[1]["id"])
    assert presets.catalog()["made_at"] in first
    assert presets.stamp("") == ""
    assert presets.stamp("nope") == "missing:nope"


def test_the_catalogue_the_browser_gets_is_serialised_once(records):
    once = presets.payload()
    assert once is presets.payload()
    sent = json.loads(once.decode("utf-8"))
    assert len(sent["records"]) == len(records)
    assert sent["task"] == presets.TASK
    assert sent["fields"] == list(presets.FIELDS)
    assert sent["parts"] == list(presets.PARTS)
    assert sent["notice"] == presets.NOTICE
    assert sent["made_at"] and sent["styles"] and sent["topics"]


def test_every_tag_has_a_label_to_show(records):
    """A missing one would put a raw slug like 'live-action-cinema' on a chip."""
    styles, topics = presets.catalog()["styles"], presets.catalog()["topics"]
    for record in records:
        assert record["style"] in styles, record["id"]
        for topic in record["topics"]:
            assert topic in topics, record["id"]


def test_every_preset_carries_what_the_picker_filters_on(records):
    """The guard against a rebuild that quietly drops a facet the window uses."""
    for record in records:
        assert record["aspect"] in ("landscape", "portrait", "square"), record["id"]
        assert isinstance(record["shots"], int) and record["shots"] >= 1, record["id"]
        assert isinstance(record["langs"], list), record["id"]
        assert isinstance(record["seconds"], (int, float)), record["id"]


def test_a_frame_that_is_not_there_is_not_an_error(records):
    assert presets.thumb("nope") is None
    assert presets.thumb("") is None
    assert presets.thumb(records[0]["id"])[:4] == b"RIFF"


def test_every_preset_says_where_its_clip_can_be_watched(records):
    """Both addresses, because one of them does not answer from China."""
    for record in records[::50]:
        found = presets.links(record)
        assert set(found) == {"huggingface", "mirror"}
        assert found["huggingface"].endswith(f"/{record['id']}.mp4")
        assert found["mirror"].endswith(f"/{record['id']}.mp4")
        assert "huggingface.co/datasets/ostris/minimax_h3_1k" in found["huggingface"]
        assert "hf-mirror.com/datasets/ostris/minimax_h3_1k" in found["mirror"]


def test_the_links_are_kept_as_templates_not_written_out_a_thousand_times():
    """One prefix stored once cannot drift into two spellings of itself."""
    assert "{id}" in presets.catalog()["video"]["huggingface"]
    assert all(
        "video" not in record and "url" not in record
        for record in presets.catalog()["records"]
    )
