"""Saved prompts: the long-term half of the memory, as JSON under the user directory.

One file is one working set -- ``global`` unless a workflow says otherwise -- and
holds whole records: the text, what produced it, and a thumbnail of every
reference the node was shown. They are written where the model list and the
guides are written, so they survive an update of the pack and are yours to edit,
copy between machines, or delete.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid

from . import checks, guide_prompt
from .constants import OUTPUT_FIELDS
from .fields import split_fields

log = logging.getLogger(__name__)

USER_SUBDIR = "minimax_h3_rewriter"
FOLDER = "prompts"
DEFAULT_FILE = "global"
VERSION = 1
SUFFIX = ".json"

ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-")


def root() -> str:
    """``<ComfyUI user>/minimax_h3_rewriter/prompts``, created on first use."""
    try:
        import folder_paths

        base = folder_paths.get_user_directory()
    except Exception:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_user")
    directory = os.path.join(base, USER_SUBDIR, FOLDER)
    os.makedirs(directory, exist_ok=True)
    return directory


def clean(name: str) -> str:
    """A file name that cannot leave the folder, whatever was typed into the box."""
    kept = "".join(ch for ch in (name or "").strip() if ch in ALLOWED).strip(" .")
    return kept[:64] or DEFAULT_FILE


def path(name: str) -> str:
    return os.path.join(root(), clean(name) + SUFFIX)


def files() -> list[str]:
    """Every set on disk, with ``global`` first and always present."""
    try:
        found = sorted(
            entry[: -len(SUFFIX)]
            for entry in os.listdir(root())
            if entry.endswith(SUFFIX) and not entry.startswith(".")
        )
    except OSError:
        found = []
    rest = [name for name in found if name != DEFAULT_FILE]
    return [DEFAULT_FILE] + rest


def load(name: str) -> dict:
    """The set as it is on disk. A missing or broken file reads as an empty one."""
    target = path(name)
    if not os.path.isfile(target):
        return {"version": VERSION, "records": []}
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        log.warning("[minimax_h3_rewriter.library] %s could not be read: %s", target, error)
        return {"version": VERSION, "records": [], "problem": str(error)}
    records = data.get("records")
    return {
        "version": int(data.get("version") or VERSION),
        "records": records if isinstance(records, list) else [],
    }


def store(name: str, data: dict) -> str:
    """Write the set out through a temporary file, so a crash cannot truncate it."""
    target = path(name)
    temporary = target + ".writing"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, target)
    return target


def create(name: str) -> str:
    """Make an empty set, or leave an existing one alone. Returns the cleaned name."""
    wanted = clean(name)
    if not os.path.isfile(path(wanted)):
        store(wanted, {"version": VERSION, "records": []})
        log.info("[minimax_h3_rewriter.library] new prompt set '%s'", wanted)
    return wanted


def add(name: str, record: dict) -> dict:
    """Append one record, giving it an id and the time it was saved."""
    data = load(name)
    saved = dict(record)
    saved["id"] = uuid.uuid4().hex[:12]
    saved["saved_at"] = time.time()
    data["records"].append(saved)
    target = store(name, data)
    log.info(
        "[minimax_h3_rewriter.library] '%s' saved to %s (%d record(s))",
        saved.get("name") or saved["id"], target, len(data["records"]),
    )
    return saved


def remove(name: str, record_id: str) -> bool:
    data = load(name)
    kept = [entry for entry in data["records"] if entry.get("id") != record_id]
    if len(kept) == len(data["records"]):
        return False
    data["records"] = kept
    store(name, data)
    log.info("[minimax_h3_rewriter.library] record %s deleted from '%s'", record_id, clean(name))
    return True


MINE = ("name", "description", "groups", "text")


def edit(name: str, record_id: str, changes: dict) -> dict | None:
    """Change the parts of a saved record that are a person's to change.

    Only the name, the description, the groups and the prompt itself. What
    wrote the record, when, the settings it ran under and the reference
    thumbnails all stay: they are the account of a run, and a card that
    misreported its own provenance would be worse than no card at all.

    Editing the text drops the stored ``sections``. Those were split out of the
    answer as it was, so a record whose sections no longer match its text would
    hand one thing to the first output and something else to the rest. Without
    them every writer splits the text for itself, exactly as it already does
    for a record another writer produced.

    Returns the record as it now stands, or None when the id is not in the set.
    A change that changes nothing is not written.
    """
    data = load(name)
    for entry in data["records"]:
        if entry.get("id") != record_id:
            continue
        before = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        for key in ("name", "description"):
            if key in changes:
                entry[key] = str(changes[key] or "").strip()
        if "groups" in changes:
            entry["groups"] = [
                str(group).strip() for group in (changes["groups"] or ()) if str(group).strip()
            ]
        if "text" in changes:
            text = str(changes["text"] or "")
            if text != entry.get("text"):
                entry["text"] = text
                entry.pop("sections", None)
        entry["name"] = entry.get("name") or "Untitled"
        if json.dumps(entry, sort_keys=True, ensure_ascii=False) == before:
            return entry
        entry["edited_at"] = time.time()
        store(name, data)
        log.info(
            "[minimax_h3_rewriter.library] record %s in '%s' edited: %s",
            record_id, clean(name), ", ".join(key for key in MINE if key in changes),
        )
        return entry
    return None


MODE_FOR_TASK = {mode.lower(): mode for mode in guide_prompt.ALL_MODES}


def inspect(text: str, task: str = "", duration=None, having=None) -> list[dict]:
    """The self-check, run over a prompt that is sitting in the library.

    The writers check what the model hands back; this checks what a person
    typed over it, by the same rules and out of the same module. It is the
    reason editing a record here beats editing the JSON file: a hand-written
    shot list is exactly where a cut time drifts past the end of the video.

    Findings come back as plain dicts, which is what the browser reads.
    """
    mode = MODE_FOR_TASK.get(checks.normalize(task), "")
    names = guide_prompt.FIELDS_FOR_MODE.get(mode) or OUTPUT_FIELDS
    fallback = guide_prompt.BODY_FIELD.get(mode) or names[0]
    sections = split_fields(text or "", names, fallback=fallback)
    return [
        {"level": issue.level, "message": issue.message, "code": issue.code}
        for issue in checks.review(
            text or "", sections, names, task=task, duration=duration, having=having
        )
    ]


def groups(records) -> list[str]:
    """Every group named by any record, for the filter and the dialog."""
    found = set()
    for entry in records or ():
        for group in entry.get("groups") or ():
            text = str(group).strip()
            if text:
                found.add(text)
    return sorted(found, key=str.casefold)


def from_record(record, name: str, description: str, wanted: list[str], task: str = "") -> dict:
    """One session record, dressed as something worth keeping.

    ``record`` is a ``memory.Record``: it already carries the answer, the scalar
    settings and the reference thumbnails, all taken while the run still had
    them. Only the name, the description and the groups come from the person.
    """
    return {
        "name": (name or "").strip() or "Untitled",
        "description": (description or "").strip(),
        "groups": [str(group).strip() for group in (wanted or ()) if str(group).strip()],
        "made_at": record.at,
        "node_class": record.node_class,
        "task": task or record.task or "",
        "about": record.about,
        "text": record.text,
        "sections": [str(value) for value in record.outputs[1:]],
        "references": record.references,
    }


PICK_TOOLTIP = (
    "Which saved prompt this node hands on instead of writing one, as JSON written by "
    "the library window. It is a widget so the choice is saved with the workflow and "
    "reaches an API run: a pick the graph does not carry is a graph that reproduces "
    "something else.\n\n"
    "It applies only while 'repeat_last' is on. That switch is what hands a kept prompt "
    "on at all; this says which one, and empty means the node's own last answer."
)


KINDS = ("image", "video", "audio")


def shape(references) -> dict:
    """How many of each kind, from records or from a list of bare kind names."""
    counts = {}
    for entry in references or ():
        kind = entry.get("kind") if isinstance(entry, dict) else entry
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def spell(counts: dict) -> str:
    parts = [
        f"{counts[kind]} {kind}" + ("s" if counts[kind] > 1 else "")
        for kind in KINDS
        if counts.get(kind)
    ]
    parts += [
        f"{count} {kind}" for kind, count in sorted(counts.items()) if kind not in KINDS
    ]
    return " + ".join(parts) or "no references"


def find(name: str, record_id: str) -> dict | None:
    for entry in load(name)["records"]:
        if entry.get("id") == record_id:
            return entry
    return None


def stamp(raw: str, enabled: bool) -> str:
    """What the picked record is right now, as a value the cache can compare.

    ComfyUI decides whether to run a node at all from its inputs, and a record
    edited in the library window changes none of them: the pick is still the
    same id in the same file. Without this the node would keep handing on the
    text it handed on before the edit, out of the execution cache, with nothing
    on screen to say why. So the nodes report this from IS_CHANGED.

    Only what reaches the outputs counts, so renaming a record or rewording its
    description does not make every node holding it run again.

    It is the empty string whenever no saved prompt is in play, which is the
    ordinary case and leaves caching exactly as it was. A pick that no longer
    resolves reports as missing, so the node runs and raises rather than
    quietly serving the answer from the record that is gone.
    """
    if not enabled:
        return ""
    text = (raw or "").strip()
    if not text or text in ("{}", "null"):
        return ""
    try:
        wanted = json.loads(text)
    except ValueError:
        return ""
    record_id = str(wanted.get("id") or "").strip()
    if not record_id:
        return ""
    record = find(clean(wanted.get("file") or DEFAULT_FILE), record_id)
    if record is None:
        return "missing:" + record_id
    payload = json.dumps(
        [record.get("text"), record.get("sections"), record.get("node_class")],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:16]


def picked(
    raw: str,
    enabled: bool,
    node_class: str,
    count: int,
    node_id=None,
    having=None,
):
    """What the library window has pointed this node at.

    Returns ``(outputs, text)``. ``outputs`` is filled when the record came from
    this kind of node and still has the right shape, and is then the whole answer.
    Otherwise ``text`` carries the prompt alone and the node splits it into its own
    sections the way it splits a fresh one -- which is what makes a record written
    by one writer usable on another.

    Both are None when nothing is picked, and when 'repeat_last' is off: that
    switch is what hands a kept prompt on at all, and this is which one. A pick
    that no longer exists is an error rather than a silent model run -- the graph
    asked for a particular prompt.

    ``having`` is the kinds of reference the node is being shown now, and is what
    the mismatch warning is made of: a prompt for a task with references describes
    those references by name inside the text, so handing it a different set is a
    real hazard rather than a tidiness one. It is said, not refused -- reusing a
    description as a template is a legitimate thing to do.
    """
    if not enabled:
        return None, None

    text = (raw or "").strip()
    if not text or text in ("{}", "null"):
        return None, None

    try:
        wanted = json.loads(text)
    except ValueError:
        log.warning("[minimax_h3_rewriter.library] the saved pick is not readable: %r", text[:200])
        return None, None

    record_id = str(wanted.get("id") or "").strip()
    if not record_id:
        return None, None

    name = clean(wanted.get("file") or DEFAULT_FILE)
    record = find(name, record_id)
    if record is None:
        raise RuntimeError(
            f"the saved prompt this node is set to ({record_id} in '{name}') is not in the "
            f"library any more. Open the library window and pick another, or clear the choice "
            f"to let the node write its own."
        )

    label = record.get("name") or record_id
    log.info(
        "[minimax_h3_rewriter.library] handing on the saved prompt '%s' from '%s', %d characters",
        label, name, len(record.get("text") or ""),
    )

    note = ""
    if having is not None:
        wanted, found = shape(record.get("references")), shape(having)
        if wanted != found:
            mismatch = (
                f"'{label}' was written for {spell(wanted)}, and this node has "
                f"{spell(found)}. A prompt for a task with references describes them by "
                f"name inside the text, so what it says about them is now about "
                f"something else."
            )
            note = "WARNING: " + mismatch
            log.warning("[minimax_h3_rewriter.library] %s", note)
            if node_id is not None:
                from .progress import announce

                announce(node_id, [("warn", mismatch)])
    if node_id is not None:
        from .progress import NodeProgress

        NodeProgress(node_id).text(
            (note + chr(10) + chr(10) if note else "")
            + f"library: '{label}' from {name}\n\n{(record.get('text') or '')[-2000:]}",
            force=True,
        )

    values = [str(record.get("text") or "")] + [str(part) for part in record.get("sections") or ()]
    if record.get("node_class") == node_class and len(values) == count:
        return tuple(values), values[0]
    return None, values[0]
