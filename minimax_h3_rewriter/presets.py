"""The prompt presets that ship inside the pack.

A thousand finished T2VA prompts with the clip they were written for, kept as
two gzipped files under ``presets/`` and read from disk. Nothing here goes to
the network: a node that dies when somebody else's site is down is a node that
breaks on a Tuesday for a reason its user cannot see. The files are built by
``tools/build_presets.py`` and are only ever read here.

They are two files rather than one because they are read at different moments.
The words are 0.4 MB and are wanted the instant the picker opens; the pictures
are 6 MB and are wanted a screenful at a time, or not at all if the graph
already knows which preset it holds. Each is unpacked the first time it is
asked for and then held, so a session that never opens the picker pays nothing
and one that does pays once.

Where they came from is written into the files themselves and is repeated to
the browser with the records, because a credit that lives only in a README is a
credit the person reading the prompts never sees.

Everything here is a plain function over plain data, and no part of it imports
ComfyUI -- which is what lets the tests read the real collection. The node that
shows all this is ``preset_node.py``, and it is deliberately thin.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os

from .fields import split_fields

log = logging.getLogger(__name__)

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")
PROMPTS_FILE = "prompts.json.gz"
THUMBS_FILE = "thumbs.json.gz"

TASK = "T2VA"

FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
PARTS = ("description", "soundscape", "music")

KIND = "preset"

NOTICE = (
    "The prompts and the clips they describe are ostris's work, carried here with credit "
    "and not relicensed. The frames were cut from those clips by this pack."
)

_catalog: dict | None = None
_thumbs: dict[str, bytes] | None = None
_index: dict[str, dict] | None = None
_payload: bytes | None = None


def _read(name: str) -> dict:
    """One gzipped file, parsed. A missing or broken one reads as empty."""
    target = os.path.join(FOLDER, name)
    if not os.path.isfile(target):
        log.info("[minimax_h3_rewriter.presets] %s is not installed", name)
        return {}
    try:
        with gzip.open(target, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        log.warning("[minimax_h3_rewriter.presets] %s could not be read: %s", target, error)
        return {}


def catalog() -> dict:
    """The prompts and their tags, unpacked once and then held.

    ``{"records": [...], "styles": {...}, "topics": {...}, "credit": {...}}``,
    or empty when the file is absent -- a pack installed without it still works,
    it just has nothing to offer.
    """
    global _catalog
    if _catalog is None:
        data = _read(PROMPTS_FILE)
        records = data.get("records")
        _catalog = data if isinstance(records, list) else {}
        if _catalog:
            log.info(
                "[minimax_h3_rewriter.presets] %d preset(s) loaded", len(_catalog["records"])
            )
    return _catalog


def thumbs() -> dict[str, bytes]:
    """The frames, base64 undone once so the route can hand out bytes.

    Decoded rather than kept as text: the browser wants image bytes, and holding
    the base64 would cost a third more for the privilege of decoding it again on
    every request.
    """
    global _thumbs
    if _thumbs is None:
        data = _read(THUMBS_FILE)
        found = data.get("thumbs")
        _thumbs = {}
        if isinstance(found, dict):
            for key, frame in found.items():
                try:
                    _thumbs[key] = base64.b64decode(frame)
                except (ValueError, TypeError):
                    log.warning("[minimax_h3_rewriter.presets] the frame of %s is unreadable", key)
        if _thumbs:
            log.info("[minimax_h3_rewriter.presets] frames for %d preset(s) loaded", len(_thumbs))
    return _thumbs


def thumb(preset_id: str) -> bytes | None:
    """The frame of one preset, or ``None`` when there is no such preset."""
    return thumbs().get(str(preset_id or "")) or None


def find(preset_id: str) -> dict | None:
    """One preset by its id.

    Indexed rather than scanned: a node reports ``IS_CHANGED`` on every graph
    validation, so this is asked far more often than the picker is opened.
    """
    global _index
    if _index is None:
        _index = {
            str(record.get("id")): record for record in catalog().get("records", ())
        }
    return _index.get(str(preset_id or ""))


def text(record: dict) -> str:
    """A preset written out the way a node writes an answer.

    Same three labels in the same order, so what comes out of the library is
    indistinguishable from what a writer put there -- the self-check reads it,
    ``split_fields`` splits it, and no branch anywhere asks where it came from.
    """
    return "\n".join(
        f"{field}: {record.get(part, '')}" for field, part in zip(FIELDS, PARTS)
    )


def groups(record: dict) -> list[str]:
    """The tags this preset files under, as library group names.

    The style first, then the subjects: the library's filter is one flat list of
    groups, and a person looking for a look starts from the look.
    """
    data = catalog()
    styles = data.get("styles") or {}
    topics = data.get("topics") or {}
    found = []
    style = record.get("style") or ""
    if style:
        found.append(styles.get(style, style))
    for topic in record.get("topics") or ():
        found.append(topics.get(topic, topic))
    return found


def links(record: dict) -> dict:
    """Where the clip this prompt was written for can be watched.

    Two addresses for one file: huggingface.co, and the hf-mirror.com copy that
    answers from mainland China, which is where much of this model's audience
    is. Composed from the id rather than stored a thousand times over.
    """
    where = catalog().get("video") or {}
    return {
        name: template.replace("{id}", str(record.get("id", "")))
        for name, template in where.items()
        if isinstance(template, str) and template
    }


def label(record: dict) -> str:
    """What to call this preset on a button and on the node.

    The number identifies it in the collection and the style says what it looks
    like, which between them is what a person needs to recognise the one they
    chose without opening the picker again.
    """
    styles = catalog().get("styles") or {}
    style = styles.get(record.get("style") or "", "")
    name = f"H3 1K #{record.get('id', '')}"
    return f"{name} - {style}" if style else name


def source(record: dict) -> str:
    """Where this preset came from, as text a graph can show or save.

    An output rather than a comment in the code: a prompt that travels into
    somebody's workflow should be able to say whose it is without them having
    to find this repository.
    """
    lines = [label(record)]
    for name, address in links(record).items():
        lines.append(f"{name}: {address}")
    for part in (catalog().get("credit") or {}).values():
        what, who = part.get("what", ""), part.get("who", "")
        if not (what and who):
            continue
        url = part.get("url", "")
        lines.append(f"{what}: {who}{' -- ' + url if url else ''}")
    lines.append(NOTICE)
    return "\n".join(lines)


def outputs(record: dict) -> tuple:
    """Everything the node hands on, in the order its schema declares.

    Assembled here rather than in the node because the node module imports
    ComfyUI and this one does not, which is the difference between a rule the
    tests can read the real collection through and a rule nobody checks.
    """
    return (
        text(record),
        str(record.get("description", "")),
        str(record.get("soundscape", "")),
        str(record.get("music", "")),
        float(record.get("seconds") or 0.0),
        int(record.get("w") or 0),
        int(record.get("h") or 0),
        source(record),
    )


def one(preset_id: str) -> dict | None:
    """One preset, dressed for the node that is holding it.

    What a page that has just been reloaded needs to draw itself again: the
    label, the text and the two addresses. It costs one small request instead
    of the whole catalogue, which the node has no other reason to pull.
    """
    record = find(preset_id)
    if record is None:
        return None
    return {
        "id": record.get("id", ""),
        "label": label(record),
        "text": text(record),
        "groups": groups(record),
        "credit": attribution(record),
        "video": links(record),
        "seconds": record.get("seconds"),
        "w": record.get("w"),
        "h": record.get("h"),
    }


def stamp(preset_id: str) -> str:
    """What the node reports from ``IS_CHANGED``.

    The collection's build time is in it on purpose: rebuilt files with the
    same ids would otherwise be served from ComfyUI's execution cache forever,
    since nothing else about the node's inputs would have moved.
    """
    wanted = str(preset_id or "").strip()
    if not wanted:
        return ""
    if find(wanted) is None:
        return "missing:" + wanted
    return f"{wanted}:{catalog().get('made_at', '')}"


def attribution(record: dict) -> str:
    """The credit a saved copy carries on its face.

    It goes in the record's ``description`` because that is a field the library
    window already draws on the card. The alternative was teaching that window
    about presets, and a line of prose costs nothing and cannot rot.
    """
    where = links(record).get("huggingface", "")
    said = f"Bundled preset {record.get('id', '')} -- the prompt and its clip are ostris's work"
    return f"{said}: {where}" if where else said + "."


def sections_of(prompt: str) -> list[str]:
    """The three fields of a prompt, split the way every node here splits one.

    Split rather than copied from the preset, because what is being saved may
    have been edited on the way: a record whose sections no longer match its
    text would hand one thing to the first output and something else to the
    rest, which is the very thing ``library.edit`` guards against.
    """
    found = split_fields(prompt or "", FIELDS, fallback=FIELDS[0])
    return [str(found.get(name, "")) for name in FIELDS]


def as_record(
    record: dict,
    name: str = "",
    description: str = "",
    tags=None,
    prompt: str = "",
) -> dict:
    """One preset dressed as a library record, ready for ``library.add``.

    ``kind`` marks it as a starting point rather than an answer a node gave.
    Records saved before the field existed carry no ``kind`` at all and read as
    answers, which is what they are. Nothing gates on it: it is provenance, and
    a preset saved here is a finished prompt in this pack's own format, so the
    library has no reason to treat it differently.
    """
    seconds = record.get("seconds") or 0
    body = prompt or text(record)
    return {
        "name": (name or "").strip() or label(record),
        "description": (description or "").strip() or attribution(record),
        "groups": (
            [str(tag).strip() for tag in tags if str(tag).strip()]
            if tags is not None
            else groups(record)
        ),
        "kind": KIND,
        "source": {
            "id": record.get("id", ""),
            "video": links(record),
            "credit": catalog().get("credit", {}),
        },
        "made_at": 0.0,
        "node_class": "",
        "task": TASK,
        "about": {
            "duration": round(float(seconds)) if seconds else None,
            "aspect": record.get("aspect", ""),
            "width": record.get("w"),
            "height": record.get("h"),
        },
        "text": body,
        "sections": sections_of(body),
        "references": [],
    }


def summary() -> dict:
    """What the window needs to draw the list: every record, minus nothing.

    The whole catalogue is 1.3 MB of JSON and the browser wants to filter over
    all of it, so it goes across in one piece rather than a page at a time. The
    frames are not in it; those come one request each, as they are shown.

    ``video`` goes across as the two templates, not composed: the browser has
    the id in every record and can put the address together itself, which keeps
    the response from carrying two thousand near-identical strings.
    """
    data = catalog()
    return {
        "task": TASK,
        "records": data.get("records", []),
        "styles": data.get("styles", {}),
        "topics": data.get("topics", {}),
        "video": data.get("video", {}),
        "credit": data.get("credit", {}),
        "notice": NOTICE,
        "fields": list(FIELDS),
        "parts": list(PARTS),
        "made_at": data.get("made_at", ""),
    }


def payload() -> bytes:
    """The catalogue as the bytes the route sends, serialised once.

    A megabyte and a third of JSON is cheap to send and not cheap to build, and
    it is the same bytes every time: opening the picker twice should not cost
    two passes over a thousand records.
    """
    global _payload
    if _payload is None:
        _payload = json.dumps(summary(), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    return _payload
