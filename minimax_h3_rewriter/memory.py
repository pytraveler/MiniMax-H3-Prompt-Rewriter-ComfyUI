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

log = logging.getLogger(__name__)

EVENT = "minimax_h3_rewriter.memory"
PREVIEW = 400

SKIP = ("self", "cls", "unique_id", "repeat_last", "bypass")

REPEAT_TOOLTIP = (
    "Hand back the last answer this node produced instead of running the model again.\n\n"
    "With nothing kept yet the node runs once, keeps the answer and says so on the node; "
    "from then on it returns that same answer for as long as the switch is on, whatever "
    "else you change. Switch it off and the next run is a real one, which replaces what "
    "is kept.\n\n"
    "The store is in memory for this ComfyUI session only, one answer per node: it is not "
    "saved with the workflow and does not survive a restart. 'bypass' still wins over it."
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
    references: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return str(self.outputs[0]) if self.outputs else ""

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.at))


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


def keep(node_id, node_class: str, outputs, given: dict, references=None) -> Record | None:
    """Remember what this node just produced."""
    if node_id is None:
        return None
    record = Record(
        node_class=node_class,
        outputs=tuple(outputs),
        signature=signature(given),
        about=about(given),
        at=time.time(),
        references=list(references or ()),
    )
    LAST[str(node_id)] = record
    log.info(
        "[minimax_h3_rewriter.memory] %s #%s: kept %d characters as the last prompt",
        node_class, node_id, len(record.text),
    )
    announce(node_id, summary(record))
    return record


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
