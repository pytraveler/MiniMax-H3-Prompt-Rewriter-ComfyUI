"""The user-editable list of base models and adapters offered by the node.

The shipped ``models.json`` is a seed, not the live file: it is copied into the
ComfyUI user directory on first use and read from there afterwards, so updating
the node pack never overwrites a list somebody has curated. The node's "Open
model list" button opens that copy.

**New entries are merged in, though**, because "we will not overwrite your list"
turned into "you will never see a model added after you installed" -- a silent
one. Somebody on 0.6.0 who updated to 0.6.2 kept getting the old quant list, with
nothing anywhere to say the node knew about more.

The merge is set algebra, not a version comparison. Beside the lists the live
file records ``seed_offered``: every name the packaged list has *ever* put in
front of this installation. An update then adds exactly

    names in the seed  -  names in your file  -  names you were already offered

so a model you deleted stays deleted, a model you renamed is not duplicated, and
a genuinely new one arrives. The one exception is unavoidable and happens once:
a file written before this mechanism existed has no record of what it was
offered, so on the first update everything missing is added back, including
anything deleted by hand. The previous file is kept beside it as ``.bak``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, replace

from .constants import ADAPTER_REPO

log = logging.getLogger(__name__)

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SEED_FILE = os.path.join(PACKAGE_DIR, "models.json")
USER_SUBDIR = "minimax_h3_rewriter"
FILE_NAME = "models.json"
BACKUP_SUFFIX = ".bak"

FORMAT_TRANSFORMERS = "transformers"
FORMAT_GGUF = "gguf"
FORMATS = (FORMAT_TRANSFORMERS, FORMAT_GGUF)

PLACEHOLDER = "REPLACE_ME"

SECTIONS = ("models", "models_8b", "models_omni", "writers", "captioners")

ADAPTERS_27B = "adapters"
ADAPTERS_8B = "adapters_8b"
ADAPTERS_OMNI = "adapters_omni"
ADAPTER_SECTIONS = (ADAPTERS_27B, ADAPTERS_8B, ADAPTERS_OMNI)

RENAMED_REPOS = {
    "ivanfromm/minimax-h3-prompt-rewriter-lora-gguf":
        "pytraveler/minimax-h3-prompt-rewriter-lora-gguf",
}


def same_repo(one: str, other: str) -> bool:
    """Whether two repository ids name the same publication."""
    first, second = one.casefold(), other.casefold()
    return RENAMED_REPOS.get(first, first) == RENAMED_REPOS.get(second, second)


OFFERED_KEY = "seed_offered"
VERSION_KEY = "seed_version"

OLLAMA_KEY = "ollama_stores"


@dataclass
class CatalogEntry:
    name: str
    repo: str
    fmt: str = FORMAT_TRANSFORMERS
    file: str = ""
    mmproj: str = ""
    download_gb: float = 0.0
    vram: str = ""
    note: str = ""

    @property
    def is_gguf(self) -> bool:
        return self.fmt == FORMAT_GGUF

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.download_gb:
            parts.append(f"{self.download_gb:g} GB download")
        if self.vram:
            parts.append(self.vram)
        label = " · ".join(parts)
        if self.note:
            label += f" — {self.note}"
        return label


@dataclass
class AdapterSpec:
    repo: str
    file: str = ""
    download_gb: float = 0.0
    note: str = ""
    alternatives: tuple["AdapterSpec", ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.repo) and PLACEHOLDER not in self.repo


def user_file() -> str:
    """Path of the live list, seeded from the packaged copy on first use."""
    try:
        import folder_paths

        base = os.path.join(folder_paths.get_user_directory(), USER_SUBDIR)
    except Exception:
        base = os.path.join(PACKAGE_DIR, "_user")

    path = os.path.join(base, FILE_NAME)
    if not os.path.isfile(path):
        try:
            os.makedirs(base, exist_ok=True)
            shutil.copyfile(SEED_FILE, path)
            log.info("[minimax_h3_rewriter.catalog] seeded model list at %s", path)
        except OSError as error:
            log.warning("[minimax_h3_rewriter.catalog] could not seed %s: %s", path, error)
            return SEED_FILE
    return path


_PROBLEM = ""


def problem() -> str:
    """What is wrong with the live list, in one line, or ``""``.

    A ``models.json`` with a typo in it used to fail in the quietest way there
    is: the parse threw, the packaged seed was returned instead, and the
    dropdown went back to its defaults with the user's own entries simply not
    there. One line in the log, on a console nobody was reading, under a node
    that looked fine. So the message comes back out here and goes into the
    dropdown itself -- the one place somebody editing that file is looking.
    """
    return _PROBLEM


def _read_reporting(path: str) -> tuple[dict, str]:
    """Parse a list file. Returns ``(data, what went wrong)``."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle), ""
    except ValueError as error:
        log.error("[minimax_h3_rewriter.catalog] %s is not valid JSON: %s", path, error)
        return {}, f"{FILE_NAME} is not valid JSON — {error}"
    except OSError as error:
        log.error("[minimax_h3_rewriter.catalog] %s could not be read: %s", path, error)
        return {}, f"{FILE_NAME} could not be read — {error}"


def _read(path: str) -> dict:
    data, _trouble = _read_reporting(path)
    return data


_SEED_CACHE: dict[tuple, dict] = {}


def _seed() -> dict:
    """The packaged list, parsed once. It cannot change while ComfyUI runs.

    Worth caching because the adapter lookups below consult it on the ordinary
    path, not only the fallback one, and those run on every graph validation.
    """
    try:
        stat = os.stat(SEED_FILE)
        key = (stat.st_size, int(stat.st_mtime))
    except OSError:
        return _read(SEED_FILE)
    cached = _SEED_CACHE.get(key)
    if cached is None:
        cached = _read(SEED_FILE)
        _SEED_CACHE.clear()
        _SEED_CACHE[key] = cached
    return cached


def _names(entries) -> list[str]:
    if not isinstance(entries, list):
        return []
    return [str(raw["name"]) for raw in entries if isinstance(raw, dict) and raw.get("name")]


def _merge_adapters(merged: dict, seed: dict, offered: dict, changes: list[str]) -> None:
    """Fold new adapter entries in, one format at a time.

    An adapter section is a dict, not a list, so the set algebra that keeps the
    lists current never reached it: a section or a format published after
    somebody's copy was made simply never appeared in their file. Reading still
    worked -- ``adapter`` falls back to the packaged value for anything
    unconfigured -- but there was no line for them to point at a conversion of
    their own, which is the whole reason the file is theirs to edit.

    Tracked in ``seed_offered`` under the section name, the same way and for the
    same reason as the lists: a format somebody deleted on purpose stays
    deleted, and only something genuinely new arrives.
    """
    for section in ADAPTER_SECTIONS:
        available = seed.get(section)
        if not isinstance(available, dict) or not available:
            continue

        current = merged.get(section)
        current = dict(current) if isinstance(current, dict) else {}
        seen = set(offered.get(section) or [])
        fresh = [
            fmt for fmt, entry in available.items()
            if isinstance(entry, dict) and fmt not in current and fmt not in seen
        ]
        if fresh:
            for fmt in fresh:
                current[fmt] = json.loads(json.dumps(available[fmt]))
            merged[section] = current
            changes.append(f"{section}: added {', '.join(fresh)}")
        elif not isinstance(merged.get(section), dict) and current:
            merged[section] = current

        offered[section] = sorted(seen | set(available))


def merge(live: dict, seed: dict) -> tuple[dict, list[str]]:
    """Fold new seed entries into a live list. Returns ``(merged, what changed)``.

    Pure, so the rule is testable without a filesystem: nothing here reads or
    writes anything.
    """
    merged = dict(live)
    offered = dict(merged.get(OFFERED_KEY) or {})
    changes: list[str] = []

    for section in SECTIONS:
        available = seed.get(section)
        if not isinstance(available, list) or not available:
            continue

        current = merged.get(section)
        known = set(_names(current)) if isinstance(current, list) else set()
        seen = set(offered.get(section) or [])
        fresh = [
            raw for raw in available
            if isinstance(raw, dict) and raw.get("name")
            and raw["name"] not in known and raw["name"] not in seen
        ]

        if not isinstance(current, list):
            # The section is absent, which is two different situations told
            # apart by ``seed_offered``: an installation that predates the
            # section has never been offered its entries and gets all of them,
            # while somebody who deleted the section has been offered every one
            # and gets none. Adding them back regardless is what this used to
            # do, and it made a whole section the one edit the file would not
            # keep -- deleting the entries one at a time already stuck.
            if fresh:
                merged[section] = fresh
                changes.append(f"{section}: added {len(fresh)} (section is new)")
        elif fresh:
            merged[section] = current + fresh
            changes.append(f"{section}: added {', '.join(_names(fresh))}")

        offered[section] = sorted(set(offered.get(section) or []) | set(_names(available)))

    _merge_adapters(merged, seed, offered, changes)

    if offered == (live.get(OFFERED_KEY) or {}) and not changes:
        return merged, changes

    merged[OFFERED_KEY] = offered
    version = seed.get(VERSION_KEY)
    if version:
        merged[VERSION_KEY] = version
    return merged, changes


def _write(path: str, data: dict, backup: bool = True) -> None:
    """Replace the live file atomically, keeping one step back as ``.bak``.

    ``backup`` is off for edits somebody made on purpose. The ``.bak`` has one
    documented job -- the file as it was before the pack changed it behind their
    back -- and a window that rewrites the list on every click would spend that
    one snapshot within seconds of the merge that earned it.

    The cache is cleared here rather than by the callers, because its key is
    ``(path, size, whole seconds)`` and two edits inside one second can produce
    a file of exactly the same size: renaming a note from "8 GB" to "9 GB" does
    it. The next read would then be served the copy from before the edit.
    """
    directory = os.path.dirname(path) or "."
    handle, staging = tempfile.mkstemp(prefix=FILE_NAME, suffix=".part", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        if backup and os.path.isfile(path):
            shutil.copyfile(path, path + BACKUP_SUFFIX)
        os.replace(staging, path)
    except OSError:
        try:
            os.unlink(staging)
        except OSError:
            pass
        raise
    finally:
        _DATA_CACHE.clear()


_DATA_CACHE: dict[tuple, dict] = {}


def _data() -> dict:
    """The live list, with anything new from the packaged one folded in.

    Cached per file identity: ``INPUT_TYPES`` runs on every graph validation and
    re-reading two files each time would be wasteful. Writing the merge back
    changes the mtime, so the next call re-reads and finds nothing left to do.
    """
    path = user_file()
    try:
        stat = os.stat(path)
        key = (os.path.normcase(path), stat.st_size, int(stat.st_mtime))
    except OSError:
        key = None

    if key is not None:
        cached = _DATA_CACHE.get(key)
        if cached is not None:
            return cached

    global _PROBLEM
    live, _PROBLEM = _read_reporting(path)
    seed = _read(SEED_FILE)
    if not live:
        return seed

    merged, changes = merge(live, seed)
    if merged != live and path != SEED_FILE:
        try:
            _write(path, merged)
            for line in changes:
                log.info("[minimax_h3_rewriter.catalog] %s", line)
            if changes:
                log.info(
                    "[minimax_h3_rewriter.catalog] merged into %s (previous copy at %s)",
                    path, path + BACKUP_SUFFIX,
                )
        except OSError as error:
            log.warning("[minimax_h3_rewriter.catalog] could not update %s: %s", path, error)
        else:
            try:
                stat = os.stat(path)
                key = (os.path.normcase(path), stat.st_size, int(stat.st_mtime))
            except OSError:
                key = None

    if key is not None:
        _DATA_CACHE.clear()
        _DATA_CACHE[key] = merged
    return merged


def _entries(data: dict, key: str) -> list[CatalogEntry]:
    entries = []
    for raw in data.get(key, []):
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        if not raw.get("repo") and not raw.get("file"):
            log.warning(
                "[minimax_h3_rewriter.catalog] '%s' has neither 'repo' nor 'file', skipping",
                raw.get("name"),
            )
            continue
        fmt = str(raw.get("format") or FORMAT_TRANSFORMERS).lower()
        if fmt not in FORMATS:
            log.warning("[minimax_h3_rewriter.catalog] unknown format %r in %r", fmt, raw.get("name"))
            continue
        try:
            entries.append(
                CatalogEntry(
                    name=str(raw["name"]),
                    repo=str(raw.get("repo") or ""),
                    fmt=fmt,
                    file=str(raw.get("file") or ""),
                    mmproj=str(raw.get("mmproj") or ""),
                    download_gb=float(raw.get("download_gb") or 0.0),
                    vram=str(raw.get("vram") or ""),
                    note=str(raw.get("note") or ""),
                )
            )
        except (TypeError, ValueError):
            log.warning("[minimax_h3_rewriter.catalog] skipping malformed entry %r", raw)
    return entries


def load() -> list[CatalogEntry]:
    """Base models for the LoRA rewriter, from the live list or the seed."""
    return _entries(_data(), "models")


def models_8b() -> list[CatalogEntry]:
    """Base models for the multimodal 8B rewriter, which reads frames itself.

    A list of its own rather than entries in ``models``: the two rewriters take
    different architectures, so anything offered here would fail to load in the
    27B node and vice versa. Same reason the writers and the captioners have
    their own lists.
    """
    return _entries(_data(), "models_8b")


def models_omni() -> list[CatalogEntry]:
    """Base models for the Omni rewriter, which reads frames, clips and sound.

    A third list for the third architecture, and the same reason as the other
    two: an entry here loads in no other node, and offering it there would only
    produce a tensor-shape error after a multi-gigabyte download.
    """
    return _entries(_data(), "models_omni")


def writers() -> list[CatalogEntry]:
    """General-purpose models offered by the guided writer nodes."""
    return _entries(_data(), "writers")


def captioners() -> list[CatalogEntry]:
    """Multimodal models offered by the reference captioner node.

    Shorter than it looks like it should be. Publishing a GGUF and an mmproj is
    not the same as llama.cpp's ``mtmd`` being able to load them -- several
    current models abort outright -- so this list holds only the ones that have
    actually been run.
    """
    return _entries(_data(), "captioners")


def ollama_stores() -> list[str]:
    """Ollama model stores named by hand in the live file, if any.

    Not a section: there are no entries to merge, restore or clash, only paths.
    ``merge`` copies the live file before folding the seed into it, so a key the
    packaged list never mentions survives an update untouched.

    It exists because the store worth naming is the one that cannot be found --
    inside WSL, inside a container, on a drive the server does not know about.
    ``ollama_store.roots`` says why looking for those automatically is the wrong
    thing to do.
    """
    value = _data().get(OLLAMA_KEY)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(one).strip() for one in value if str(one).strip()]


def _alternatives_from(raw: dict, repo: str) -> tuple[AdapterSpec, ...]:
    """The other precisions listed beside an adapter, which share its repository."""
    found = []
    for other in raw.get("alternatives") or []:
        if not isinstance(other, dict) or not other.get("file"):
            continue
        try:
            found.append(
                AdapterSpec(
                    repo=str(other.get("repo") or repo),
                    file=str(other["file"]),
                    download_gb=float(other.get("download_gb") or 0.0),
                    note=str(other.get("note") or ""),
                )
            )
        except (TypeError, ValueError):
            log.warning("[minimax_h3_rewriter.catalog] skipping malformed adapter %r", other)
    return tuple(found)


def _adapter_from(data: dict, fmt: str, section: str = ADAPTERS_27B) -> AdapterSpec:
    raw = (data.get(section) or {}).get(fmt) or {}
    if fmt == FORMAT_TRANSFORMERS:
        default = ADAPTER_REPO if section == ADAPTERS_27B else ""
        return AdapterSpec(
            repo=str(raw.get("repo") or default),
            download_gb=float(raw.get("download_gb") or 0.0),
        )
    repo = str(raw.get("repo") or "")
    return AdapterSpec(
        repo=repo,
        file=str(raw.get("file") or ""),
        download_gb=float(raw.get("download_gb") or 0.0),
        note=str(raw.get("note") or ""),
        alternatives=_alternatives_from(raw, repo),
    )


def _with_packaged_alternatives(spec: AdapterSpec, fmt: str, section: str) -> AdapterSpec:
    """Let a list written before a quantisation existed still offer it.

    ``adapters`` is a dict, and the merge that keeps live lists current is set
    algebra over named entries in a *list*, so it never reaches this. Without
    something here, an installation seeded at 0.8.0 would go on being offered
    only the F16 build, with nothing anywhere to say a smaller one exists --
    exactly the silent staleness the merge was written to end.

    The packaged alternatives are folded in only when the user's entry points at
    the same repository and lists none of its own. Alternatives they wrote win,
    and a repository they redirected to their own conversions is left alone.
    """
    if spec.alternatives or not spec.repo:
        return spec
    packaged = _adapter_from(_seed(), fmt, section)
    if not packaged.alternatives or not same_repo(packaged.repo, spec.repo):
        return spec
    return replace(spec, alternatives=packaged.alternatives)


def adapter(fmt: str, section: str = ADAPTERS_27B) -> AdapterSpec:
    """The adapter to pair with a base model of the given format.

    A live list seeded before an adapter had a home still carries the placeholder,
    and the seed is only ever copied once. Rather than rewrite somebody's file,
    an unconfigured entry falls back to the packaged value — a real repository
    always beats a placeholder, and a real entry the user wrote always wins. A
    whole section added after that copy was made is the same case, one level up.
    """
    spec = _adapter_from(_data(), fmt, section)
    if spec.configured:
        return _with_packaged_alternatives(spec, fmt, section)
    fallback = _adapter_from(_seed(), fmt, section)
    return fallback if fallback.configured else spec


def adapter_entries(fmt: str, section: str = ADAPTERS_27B) -> list[CatalogEntry]:
    """Every published build of one adapter, the default first.

    ``models.json`` names one file and lists the rest under ``alternatives``.
    They are the same LoRA at different precisions, and which one a card wants
    is a question to answer while building a graph rather than while editing
    JSON -- so they go in a dropdown, and they come back as ``CatalogEntry`` to
    be labelled exactly the way the base models beside them are.
    """
    spec = adapter(fmt, section)
    if not spec.configured:
        return []
    entries = []
    for candidate in (spec, *spec.alternatives):
        if not candidate.file:
            continue
        entries.append(
            CatalogEntry(
                name=candidate.file,
                repo=candidate.repo,
                fmt=FORMAT_GGUF,
                file=candidate.file,
                download_gb=candidate.download_gb,
                note=candidate.note,
            )
        )
    return entries


class CatalogWriteError(RuntimeError):
    """The list must not be written right now, and why."""


def writable() -> str:
    """The live file, or a refusal saying why editing it would do harm.

    Two states look like an ordinary list and are not. A seeding that failed
    leaves ``user_file`` pointing at the packaged copy, where an edit would be
    written into the node pack and lost on the next update. A file that does not
    parse leaves ``_data`` serving the seed, so writing that back would replace a
    curated list with the defaults -- the exact loss the dropdown warning exists
    to prevent, arriving through the window meant to fix it.
    """
    path = user_file()
    if os.path.normcase(path) == os.path.normcase(SEED_FILE):
        raise CatalogWriteError(
            "the model list could not be created in the ComfyUI user directory, so the "
            "packaged copy is being read instead. Anything written here would live inside "
            "the node pack and disappear on the next update. Check that the ComfyUI user "
            "directory is writable, then reopen this window."
        )
    _data()
    trouble = problem()
    if trouble:
        raise CatalogWriteError(
            f"{trouble}. The file has to parse before it can be edited here, or saving would "
            f"replace your own entries with the packaged list. Open {path}, fix it, then "
            f"reopen this window."
        )
    return path


def _mutable() -> tuple[str, dict]:
    """The live file's path and a private copy of it, safe to edit.

    ``merge`` copies shallowly, so the section lists inside the cached dict are
    the very objects ``json.load`` produced. Editing one in place would change
    what every ``INPUT_TYPES`` call sees, including after a write that failed.
    """
    path = writable()
    return path, json.loads(json.dumps(_data()))


def raw_entries(section: str) -> list[dict]:
    """One list as it is written, for something that edits it.

    ``load()`` and its siblings return ``CatalogEntry``, which is what the nodes
    want and the wrong shape for an editor: defaults are already applied, and a
    malformed entry has been dropped, so the file that needs fixing looks fine.
    """
    found = _data().get(section)
    if not isinstance(found, list):
        return []
    return [dict(raw) for raw in found if isinstance(raw, dict)]


def entry_label(raw: dict) -> str:
    """The dropdown text one raw entry would produce.

    Through ``CatalogEntry`` rather than a second copy of the rule. The label is
    what a saved workflow remembers, so two implementations of it would be two
    chances to quietly stop matching.
    """
    try:
        return CatalogEntry(
            name=str(raw.get("name") or ""),
            repo=str(raw.get("repo") or ""),
            download_gb=float(raw.get("download_gb") or 0.0),
            vram=str(raw.get("vram") or ""),
            note=str(raw.get("note") or ""),
        ).label
    except (TypeError, ValueError):
        return str(raw.get("name") or "")


def seed_names(section: str) -> set[str]:
    """Every name the packaged list holds for one section."""
    found = _seed().get(section)
    if not isinstance(found, list):
        return set()
    return {str(raw["name"]) for raw in found if isinstance(raw, dict) and raw.get("name")}


def _missing_from(data: dict, section: str) -> list[str]:
    present = {
        str(raw.get("name")) for raw in (data.get(section) or ()) if isinstance(raw, dict)
    }
    return sorted(seed_names(section) - present)


def restorable(section: str) -> list[str]:
    """Packaged entries this list no longer has, which a restore would bring back."""
    return _missing_from(_data(), section)


def _record_offered(data: dict, section: str, name: str) -> None:
    """Record that a packaged entry has already been put in front of this install.

    Without this a deletion does not stick. ``merge`` offers back every seed
    entry that is neither present nor recorded, and the recording happens only
    inside its own loop -- which is skipped for a file that failed to parse, and
    has never run at all on a copy seeded and edited before its first read. The
    packaged file ships without the key entirely.

    Only seed names are recorded. ``seed_offered`` is documented as the user's to
    edit, and inventing names in it would silently refuse a future entry of the
    pack's own that happened to be called the same thing.
    """
    if name not in seed_names(section):
        return
    offered = dict(data.get(OFFERED_KEY) or {})
    offered[section] = sorted(set(offered.get(section) or ()) | {name})
    data[OFFERED_KEY] = offered


def _entries_of(data: dict, section: str) -> list[dict]:
    found = data.get(section)
    return list(found) if isinstance(found, list) else []


def _find(entries: list, name: str) -> int:
    for at, raw in enumerate(entries):
        if isinstance(raw, dict) and str(raw.get("name") or "") == name:
            return at
    return -1


def _refuse_clash(entries: list, entry: dict, skip: int = -1) -> None:
    """Refuse a name or a label another entry in the same list already carries.

    Both, and for different reasons. ``name`` is how an edit and a deletion
    address an entry, so a duplicate makes those ambiguous. The label is what the
    dropdown is keyed on -- every ``_build_*_map`` is a dict of it -- so two
    entries sharing one silently shadow each other and only ever offer the one.
    """
    name = str(entry.get("name") or "")
    label = entry_label(entry)
    for at, raw in enumerate(entries):
        if at == skip or not isinstance(raw, dict):
            continue
        if str(raw.get("name") or "") == name:
            raise CatalogWriteError(
                f"'{name}' is already in this list. Edit that entry instead of adding a "
                f"second one under the same name."
            )
        if entry_label(raw) == label:
            raise CatalogWriteError(
                f"'{raw.get('name')}' already shows as '{label}' in the dropdown, and two "
                f"entries with the same label hide one another. Give this one a different "
                f"name, download size, VRAM note or note."
            )


def add(section: str, entry: dict) -> dict:
    """Append one entry to a list. Returns it as written."""
    path, data = _mutable()
    entries = _entries_of(data, section)
    _refuse_clash(entries, entry)
    entries.append(entry)
    data[section] = entries
    _write(path, data, backup=False)
    return entry


def update(section: str, name: str, entry: dict) -> dict:
    """Replace the entry called ``name``. Returns it as written."""
    path, data = _mutable()
    entries = _entries_of(data, section)
    at = _find(entries, name)
    if at < 0:
        raise CatalogWriteError(
            f"'{name}' is not in this list any more -- something else changed the file. "
            f"Reopen this window to see what is there now."
        )
    _refuse_clash(entries, entry, skip=at)
    entries[at] = entry
    data[section] = entries
    if str(entry.get("name") or "") != name:
        _record_offered(data, section, name)
    _write(path, data, backup=False)
    return entry


def remove(section: str, name: str) -> bool:
    """Delete one entry, and record it so the next merge does not bring it back."""
    path, data = _mutable()
    entries = _entries_of(data, section)
    at = _find(entries, name)
    if at < 0:
        return False
    entries.pop(at)
    data[section] = entries
    _record_offered(data, section, name)
    _write(path, data, backup=False)
    return True


def restore_packaged(section: str) -> list[str]:
    """Offer this list's packaged entries again, by forgetting they were offered.

    The other half of ``remove``: ``seed_offered`` is the whole mechanism that
    makes a deletion stick, so dropping the section from it is all a restore
    needs. The next read merges the missing entries back in on its own. This is a
    button for what the file's own comment block already tells people to do by
    hand.
    """
    path, data = _mutable()
    coming = _missing_from(data, section)
    if not coming:
        return []
    offered = dict(data.get(OFFERED_KEY) or {})
    offered.pop(section, None)
    data[OFFERED_KEY] = offered
    _write(path, data, backup=False)
    return coming


def reveal() -> str:
    """Open the list in whatever the desktop uses for .json files."""
    import subprocess
    import sys

    path = user_file()
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opening the user's own config file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return path
