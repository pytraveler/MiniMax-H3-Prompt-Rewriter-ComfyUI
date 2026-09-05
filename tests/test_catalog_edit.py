"""Editing the model list from the window rather than in a text editor.

The merge that keeps a list current is set algebra over ``seed_offered``, and
every one of these tests exists because writing to the file from a button, many
times a minute, reaches states hand-editing never did: a deletion recorded
before the first merge has run, two edits inside one second, a write attempted
over a file that does not parse.

Nothing here needs ComfyUI. ``user_file`` is pointed at a temporary copy of the
packaged list, which is what a fresh install has.
"""

import importlib
import json
import os
import pathlib
import shutil
import sys
import types

import pytest

_PKG = "minimax_h3_rewriter"
ROOT = pathlib.Path(__file__).resolve().parent.parent

if _PKG not in sys.modules:
    _package = types.ModuleType(_PKG)
    _package.__path__ = [str(ROOT / _PKG)]
    sys.modules[_PKG] = _package

catalog = importlib.import_module(f"{_PKG}.catalog")

SECTION = "writers"
MINE = {"name": "Mine", "repo": "someone/repo", "file": "mine.gguf", "format": "gguf"}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A fresh install's list: a byte copy of the packaged one, and no cache."""
    path = tmp_path / "models.json"
    shutil.copyfile(catalog.SEED_FILE, path)
    monkeypatch.setattr(catalog, "user_file", lambda: str(path))
    catalog._DATA_CACHE.clear()
    yield path
    catalog._DATA_CACHE.clear()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def offered(path, section=SECTION):
    return read(path).get(catalog.OFFERED_KEY, {}).get(section) or []


def names(section=SECTION):
    return [entry.name for entry in catalog._entries(catalog._data(), section)]


def a_packaged_name():
    return sorted(catalog.seed_names(SECTION))[0]


def test_the_packaged_list_carries_no_record_of_what_it_offered(live):
    """Which is why ``remove`` cannot rely on a merge having written one."""
    assert catalog.OFFERED_KEY not in read(live)


def test_a_deleted_packaged_entry_does_not_come_back(live):
    """The hostile order: deleted before any merge has ever run on this file."""
    victim = a_packaged_name()
    assert catalog.remove(SECTION, victim)
    assert victim in offered(live)

    catalog._DATA_CACHE.clear()
    assert victim not in names()


def test_a_deleted_entry_survives_a_second_read(live):
    victim = a_packaged_name()
    catalog.remove(SECTION, victim)
    for _ in range(3):
        catalog._DATA_CACHE.clear()
        assert victim not in names()


def test_a_name_of_your_own_is_not_recorded_as_offered(live):
    """``seed_offered`` is documented as editable, and inventing names in it would
    silently refuse a future entry of the pack's own under the same name."""
    catalog.add(SECTION, MINE)
    catalog.remove(SECTION, "Mine")
    assert "Mine" not in offered(live)


def test_removing_something_that_is_not_there_says_so(live):
    assert catalog.remove(SECTION, "Never Existed") is False


def test_restoring_brings_the_packaged_entries_back(live):
    victim = a_packaged_name()
    catalog.remove(SECTION, victim)
    assert catalog.restorable(SECTION) == [victim]

    assert catalog.restore_packaged(SECTION) == [victim]
    catalog._DATA_CACHE.clear()
    assert victim in names()
    assert catalog.restorable(SECTION) == []


def test_restoring_a_whole_list_is_not_an_error(live):
    assert catalog.restore_packaged(SECTION) == []


def test_your_own_entries_survive_a_restore(live):
    catalog.add(SECTION, MINE)
    catalog.remove(SECTION, a_packaged_name())
    catalog.restore_packaged(SECTION)
    catalog._DATA_CACHE.clear()
    assert "Mine" in names()


def test_an_edit_of_the_same_size_in_the_same_second_is_not_served_from_cache(live):
    """The cache is keyed on ``(path, size, whole seconds)``, and neither moves here.

    Changing one digit of a VRAM note keeps the byte count exactly, and two
    clicks land inside one second easily. The timestamps are pinned rather than
    raced for, so this fails for the reason it is named after and not on timing.
    """
    def labels():
        return [catalog.entry_label(raw) for raw in catalog.raw_entries(SECTION)]

    catalog._data()
    catalog.add(SECTION, dict(MINE, vram="8 GB"))

    stamp = os.stat(live).st_mtime
    os.utime(live, (stamp, stamp))
    before = os.stat(live).st_size
    assert "Mine · 8 GB" in labels()

    catalog.update(SECTION, "Mine", dict(MINE, vram="9 GB"))
    os.utime(live, (stamp, stamp))
    assert os.stat(live).st_size == before, "the two files have to be the same size"

    assert "Mine · 9 GB" in labels()
    assert "Mine · 8 GB" not in labels()


def test_a_failed_write_does_not_leave_the_cache_lying(live, monkeypatch):
    """``merge`` copies shallowly, so an in-place edit would reach the cached dict."""
    before = names()

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(catalog, "_write", refuse)
    with pytest.raises(OSError):
        catalog.add(SECTION, MINE)
    assert names() == before


def test_window_edits_leave_the_backup_alone(live):
    """The ``.bak`` records the file as it was before the pack changed it behind
    your back. Edits made on purpose are not that, and would spend it in seconds."""
    catalog._data()
    backup = pathlib.Path(str(live) + catalog.BACKUP_SUFFIX)
    if backup.exists():
        backup.unlink()

    catalog.add(SECTION, MINE)
    catalog.update(SECTION, "Mine", dict(MINE, vram="8 GB"))
    catalog.remove(SECTION, "Mine")
    assert not backup.exists()


def test_the_merge_still_keeps_a_backup(live):
    """The one case that earns it: the pack folding new entries into your file."""
    data = read(live)
    data[SECTION] = [raw for raw in data[SECTION]][:1]
    data.pop(catalog.OFFERED_KEY, None)
    live.write_text(json.dumps(data), encoding="utf-8")
    catalog._DATA_CACHE.clear()

    catalog._data()
    assert pathlib.Path(str(live) + catalog.BACKUP_SUFFIX).exists()


def test_a_file_that_does_not_parse_refuses_every_write(live):
    """Writing would replace a curated list with the packaged one -- the exact
    loss the dropdown warning exists to prevent, through the window meant to fix it."""
    live.write_text("{ not json", encoding="utf-8")
    catalog._DATA_CACHE.clear()

    with pytest.raises(catalog.CatalogWriteError):
        catalog.writable()
    with pytest.raises(catalog.CatalogWriteError):
        catalog.add(SECTION, MINE)
    with pytest.raises(catalog.CatalogWriteError):
        catalog.remove(SECTION, a_packaged_name())
    assert live.read_text(encoding="utf-8") == "{ not json"


def test_writing_into_the_package_is_refused(monkeypatch):
    """``user_file`` falls back to the packaged copy when seeding fails. An edit
    there would live inside the node pack and vanish on the next update."""
    monkeypatch.setattr(catalog, "user_file", lambda: catalog.SEED_FILE)
    catalog._DATA_CACHE.clear()
    with pytest.raises(catalog.CatalogWriteError):
        catalog.writable()
    catalog._DATA_CACHE.clear()


def test_a_duplicate_name_is_refused(live):
    catalog.add(SECTION, MINE)
    with pytest.raises(catalog.CatalogWriteError):
        catalog.add(SECTION, dict(MINE, file="other.gguf"))


def test_a_duplicate_label_is_refused(live):
    """Every ``_build_*_map`` is a dict keyed on the label, so two entries sharing
    one silently shadow each other and only ever offer the one."""
    catalog.add(SECTION, dict(MINE, name="Qwen 4B", vram="8 GB VRAM"))
    with pytest.raises(catalog.CatalogWriteError):
        catalog.add(SECTION, dict(MINE, name="Qwen 4B · 8 GB VRAM"))


def test_an_edit_may_keep_its_own_name(live):
    catalog.add(SECTION, MINE)
    catalog.update(SECTION, "Mine", dict(MINE, note="now with a note"))
    assert catalog.entry_label(catalog.raw_entries(SECTION)[-1]).endswith("now with a note")


def test_editing_something_that_is_gone_says_so(live):
    with pytest.raises(catalog.CatalogWriteError):
        catalog.update(SECTION, "Never Existed", MINE)


def test_a_rename_records_the_old_packaged_name(live):
    """Otherwise the merge offers the original back beside the renamed copy."""
    victim = a_packaged_name()
    held = next(raw for raw in catalog.raw_entries(SECTION) if raw["name"] == victim)
    catalog.update(SECTION, victim, dict(held, name="Renamed"))
    assert victim in offered(live)

    catalog._DATA_CACHE.clear()
    assert victim not in names()
    assert "Renamed" in names()


def test_raw_entries_shows_the_file_rather_than_the_defaults(live):
    """An editor has to see what is written, including what ``_entries`` would drop."""
    data = read(live)
    data[SECTION].append({"name": "Broken", "note": "no repo and no file"})
    live.write_text(json.dumps(data), encoding="utf-8")
    catalog._DATA_CACHE.clear()

    assert "Broken" in [raw.get("name") for raw in catalog.raw_entries(SECTION)]
    assert "Broken" not in names()


def test_the_label_is_the_one_the_dropdown_uses(live):
    entry = {"name": "N", "repo": "a/b", "download_gb": 2.5, "vram": "8 GB", "note": "small"}
    assert catalog.entry_label(entry) == "N · 2.5 GB download · 8 GB — small"


def test_a_label_survives_a_malformed_size(live):
    assert catalog.entry_label({"name": "N", "download_gb": "lots"}) == "N"
