"""The last answer each node produced, kept for this ComfyUI session only.

ComfyUI's own cache cannot stand in for this. A node's cache key is its class,
its IS_CHANGED value and every input it received, so editing the prompt is
precisely what drops the entry -- and "hand me the previous answer even though
the prompt changed" is the opposite of what a cache is for. IS_CHANGED can add
invalidation but never mask it. So the answers live here, in a plain dict keyed
by node id, and each node decides for itself whether to hand one back.

Nothing here is written to disk and nothing travels with the workflow: restart
ComfyUI and every node is back to running the model.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

from .fields import body_field, split_fields

log = logging.getLogger(__name__)

EVENT = "minimax_h3_rewriter.memory"
PREVIEW = 400

SKIP = ("self", "cls", "unique_id", "repeat_last", "library_pick", "bypass")

REPEAT_TOOLTIP = (
    "Hand back a prompt this node already has instead of running the model again.\n\n"
    "By default that is the node's own last answer: with nothing kept yet it runs once, "
    "keeps what it wrote and says so, and from then on returns that same text for as long "
    "as the switch is on, whatever else you change. Pick something in the library window "
    "and this switch hands that saved prompt on instead -- the window chooses which "
    "prompt, this switch is what makes it happen.\n\n"
    "Off is always a real run. The session store is in memory only, one answer per node: "
    "it is not saved with the workflow and does not survive a restart, while a saved "
    "prompt does both. 'bypass' still wins over all of it."
)

REPEAT_CAPTION_TOOLTIP = (
    "Hand back the last caption this node produced instead of looking at the asset again.\n\n"
    "With nothing kept yet the node describes it once, keeps the caption and says so; from "
    "then on it reuses that caption, numbered into the chain as usual, so the assets before "
    "and after this one stay correct. Switch it off for a real run, which replaces what is "
    "kept.\n\n"
    "The store is in memory for this ComfyUI session only, one caption per node: it is not "
    "saved with the workflow and does not survive a restart. 'bypass' still wins over it, and "
    "a 'description' typed by hand skips the model regardless."
)


@dataclass
class Record:
    """One node's last answer, and enough about the run to describe it later."""

    node_class: str
    outputs: tuple
    signature: str
    about: dict
    at: float
    task: str = ""
    references: list = field(default_factory=list)
    fields: tuple = ()
    edited_at: float = 0.0

    @property
    def text(self) -> str:
        return str(self.outputs[0]) if self.outputs else ""

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.at))

    @property
    def editable(self) -> bool:
        """Whether this record is a prompt, and so has something to edit.

        The captioners keep a record too, and theirs is a line about one asset
        rather than an answer with fields. They are told apart by whether the
        node said what its fields were when it kept the record.
        """
        return bool(self.fields)


LAST: dict[str, Record] = {}


def _rendered(value) -> str:
    """A stable string for one input value.

    Media is summarised by shape rather than read: hashing a batch of frames on
    every run would cost more than it tells us. A VIDEO object carries no shape,
    so two different clips render alike -- that only weakens the "settings
    changed since" note, never the answer itself.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return repr(value)
    if isinstance(value, dict):
        pairs = sorted(value.items(), key=lambda item: str(item[0]))
        return "{" + ",".join(f"{key}:{_rendered(item)}" for key, item in pairs) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_rendered(item) for item in value) + "]"
    shape = getattr(value, "shape", None)
    if shape is not None:
        return f"{type(value).__name__}{tuple(shape)}"
    return type(value).__name__


def signature(given: dict) -> str:
    pairs = sorted((key, value) for key, value in given.items() if key not in SKIP)
    text = ";".join(f"{key}={_rendered(value)}" for key, value in pairs)
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def about(given: dict) -> dict:
    """The scalar half of a call, kept for the card the library will show."""
    return {
        key: value
        for key, value in given.items()
        if key not in SKIP and isinstance(value, (str, int, float, bool))
    }


def summary(record: Record | None, repeated: bool = False, changed: bool = False) -> dict:
    if record is None:
        return {"stored": False}
    return {
        "stored": True,
        "repeated": repeated,
        "changed": changed,
        "at": record.at,
        "clock": record.clock,
        "chars": len(record.text),
        "preview": record.text[:PREVIEW],
        "node_class": record.node_class,
        "task": record.task,
        "references": len(record.references),
        "editable": record.editable,
        "edited_at": record.edited_at,
    }


def announce(node_id, payload: dict) -> None:
    """Tell the frontend what this node's memory holds, for the switch's tooltip."""
    if node_id is None:
        return
    try:
        from server import PromptServer

        PromptServer.instance.send_sync(EVENT, {"node": str(node_id), **payload})
    except Exception:
        log.debug("[minimax_h3_rewriter.memory] could not announce the record", exc_info=True)


def recall(node_id) -> Record | None:
    return LAST.get(str(node_id)) if node_id is not None else None


def forget(node_id) -> None:
    LAST.pop(str(node_id), None)


def keep(
    node_id,
    node_class: str,
    outputs,
    given: dict,
    references=None,
    task: str = "",
    fields: tuple = (),
) -> Record | None:
    """Remember what this node just produced."""
    if node_id is None:
        return None
    record = Record(
        node_class=node_class,
        outputs=tuple(outputs),
        signature=signature(given),
        about=about(given),
        at=time.time(),
        task=task or str(given.get("task") or ""),
        references=list(references or ()),
        fields=tuple(fields or ()),
    )
    LAST[str(node_id)] = record
    log.info(
        "[minimax_h3_rewriter.memory] %s #%s: kept %d characters as the last prompt",
        node_class, node_id, len(record.text),
    )
    announce(node_id, summary(record))
    return record


def rewrite(node_id, text: str) -> Record | None:
    """Replace the answer this node is holding with one a person has edited.

    The sections are split out of the new text rather than carried over: they
    were made from the text as it was, and a record whose sections no longer
    match its own prose would hand one thing to the first output and something
    else to the rest. Whatever the node kept past its sections -- a reference
    block, a list of captions -- belongs to the run and not to the prose, so it
    stays exactly as it was.

    None when there is nothing to edit, or when the record is not a prompt.
    """
    record = recall(node_id)
    if record is None or not record.editable:
        return None
    sections = split_fields(text, record.fields, fallback=body_field(record.fields))
    record.outputs = (
        (text,)
        + tuple(sections.get(name, "") for name in record.fields)
        + tuple(record.outputs[1 + len(record.fields):])
    )
    record.edited_at = time.time()
    log.info(
        "[minimax_h3_rewriter.memory] %s #%s: the kept answer was edited, now %d characters",
        record.node_class, node_id, len(record.text),
    )
    announce(node_id, summary(record))
    return record


def stamp(node_id, enabled: bool) -> str:
    """What this node would hand back out of its own memory, as a cache key.

    The same reason ``library.stamp`` exists: editing the kept answer changes
    none of the node's inputs, so without this ComfyUI would go on serving the
    answer from before the edit and nothing on screen would say why.

    Empty while 'repeat_last' is off, which is when the memory is not consulted
    at all -- so caching is left exactly as it was for every ordinary run.
    """
    if not enabled or node_id is None:
        return ""
    record = recall(node_id)
    if record is None:
        return ""
    return f"|{record.at:.6f}/{record.edited_at:.6f}"


def repeat(node_id, node_class: str, enabled: bool, given: dict, label: str = "prompt"):
    """Return the kept answer when the switch asks for it, or None to run.

    The caller runs the model when this is None and calls ``keep`` afterwards.
    """
    if not enabled:
        return None

    from .progress import NodeProgress

    progress = NodeProgress(node_id)
    record = recall(node_id)

    if record is None or record.node_class != node_class:
        log.info(
            "[minimax_h3_rewriter.memory] %s #%s: 'repeat_last' is on with nothing kept yet, "
            "so this run happens for real and its answer is what gets repeated",
            node_class, node_id,
        )
        progress.text(
            f"repeat_last: nothing kept yet - running once, and this {label} is what "
            f"comes back next time",
            force=True,
        )
        announce(node_id, summary(None))
        return None

    changed = record.signature != signature(given)
    note = " (the settings have changed since)" if changed else ""
    log.info(
        "[minimax_h3_rewriter.memory] %s #%s: repeating the %s kept at %s, %d characters%s",
        node_class, node_id, label, record.clock, len(record.text), note,
    )
    progress.text(
        f"repeat_last: the {label} kept at {record.clock}{note}\n\n{record.text[-2000:]}",
        force=True,
    )
    announce(node_id, summary(record, repeated=True, changed=changed))
    return record.outputs
