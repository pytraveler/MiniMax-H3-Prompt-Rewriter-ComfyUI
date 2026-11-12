"""The models this pack is holding in memory, and the ways they are let go.

A writer kept with 'keep_model_loaded' lives in a module-level slot of the
engine that loaded it: ``engine`` for a Transformers checkpoint, ``gguf_engine``
for a GGUF run through llama-cpp-python. Neither is a ComfyUI model patcher, so
ComfyUI's model management has never heard of them. Its "Unload Models" button
walks its own list and leaves these where they are, and so does every clean-up
node that calls ``unload_all_models``. That left one way to let a kept model
go: run the node again with the switch off, which writes the same text a second
time to do it.

This is the one place that knows what is resident and how to drop it:

- ``release`` unloads everything, for the button on the nodes and its route;
- ``settle`` does it for a node that handed back a kept answer and so never
  reached the engine that would have read the switch;
- ``install`` puts ``release`` behind ComfyUI's own ``unload_all_models``, so
  the stock button, the ``/free`` route and other packs' clean-up nodes reach
  this pack's models as well.

What a subprocess holds is not here because it is not anywhere: the llama.cpp
binaries take their model with them when they exit.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import threading

log = logging.getLogger(__name__)

HOOKED = "_minimax_h3_rewriter_hooked"

_GUARD = threading.Lock()
_RUNS = {"active": 0, "owed": False}
_OWN = threading.local()


def _engines() -> tuple:
    from . import engine, gguf_engine, qwen_engine

    return (engine, gguf_engine, qwen_engine)


def held() -> list[str]:
    """A name for every model this process is holding right now."""
    return [name for name in (one.holding() for one in _engines()) if name]


@contextlib.contextmanager
def in_use():
    """Hold whatever is loaded for the length of one run.

    A release that arrives meanwhile -- the button, clicked while the node is
    still writing, or another pack's route answering on the server's own
    thread -- is owed rather than done. Closing a llama.cpp context under the
    call that is reading from it does not raise, it takes the process down.

    The debt is paid on the way out whatever 'keep_model_loaded' said: someone
    asked for the memory after that switch was set.
    """
    with _GUARD:
        _RUNS["active"] += 1
    try:
        yield
    finally:
        with _GUARD:
            _RUNS["active"] -= 1
            owed = _RUNS["owed"] and not _RUNS["active"]
            if owed:
                _RUNS["owed"] = False
        if owed:
            release("the run it was waiting for has finished")


@contextlib.contextmanager
def making_room():
    """Mark a call into ComfyUI's unload as this pack clearing the card for itself.

    ``runner.free_comfy_vram`` evicts ComfyUI's models just before one of ours
    loads. With the hook in place that same call would come back round and
    release the pack's own models too: the one a sibling engine was asked to
    keep, and, through the debt above, the one being loaded.
    """
    depth = getattr(_OWN, "depth", 0)
    _OWN.depth = depth + 1
    try:
        yield
    finally:
        _OWN.depth = depth


def release(why: str = "") -> dict:
    """Unload every model the pack holds in this process.

    Returns ``{"released": [...], "waiting": bool}``: the names that went, and
    whether a run is in the way, in which case they go when it ends.

    The guard is held across the unloading itself. A run that starts in that
    moment would otherwise be handed a model out of the engine's slot an
    instant before it is closed.
    """
    with _GUARD:
        if _RUNS["active"]:
            _RUNS["owed"] = True
            log.info(
                "[minimax_h3_rewriter.resident] asked to unload while a run is using the "
                "model; it goes when that run ends"
            )
            return {"released": [], "waiting": True}

        released = []
        for one in _engines():
            name = one.holding()
            if not name:
                continue
            one.unload()
            released.append(name)

    if released:
        log.info(
            "[minimax_h3_rewriter.resident] unloaded %s%s",
            ", ".join(released), f" -- {why}" if why else "",
        )
    return {"released": released, "waiting": False}


def settle(keep_loaded) -> list[str]:
    """Honour 'keep_model_loaded' for a node that is handing back a kept answer.

    A real run ends in the engine, which unloads unless it was told to keep. A
    run that 'repeat_last' answers never gets that far, so the switch went
    unread and the model stayed whatever it said -- in the one case where
    turning it off most plainly means "I have what I wanted, give the card
    back".

    ``None`` is a node with no such switch, a captioner, and changes nothing.
    Everything the pack holds goes, not one engine's share: which runtime this
    node would have used is only known by resolving its model, and that is the
    work being skipped. Returns the names that went.
    """
    if keep_loaded is None or keep_loaded:
        return []
    return release("'keep_model_loaded' is off on a node that handed back a kept answer")[
        "released"
    ]


def freed(released) -> str:
    """One line for a node's caption saying what ``settle`` let go, or nothing."""
    if not released:
        return ""
    return f"keep_model_loaded is off, so {', '.join(released)} was unloaded"


def install() -> bool:
    """Put ``release`` behind ComfyUI's ``unload_all_models``.

    Wrapping the function where it lives catches everyone who reaches it
    through the module, which is nearly everyone: the worker answering the
    "Unload Models" button and the ``/free`` route, the out-of-memory handler,
    and the clean-up nodes of other packs. A pack that imported the function by
    name before this one loaded keeps the stock one and is not caught.

    ComfyUI's own unloading runs first and its answer is what comes back, so
    nothing this pack does can stand in its way. Under ``--disable-smart-memory``
    ComfyUI calls this after every prompt, and then a kept model lasts for the
    run it was kept in and no longer -- which is what that flag asks of every
    other model too.

    False when there is no ComfyUI to hook or it is hooked already.
    """
    try:
        import comfy.model_management as mm
    except Exception:
        log.debug("[minimax_h3_rewriter.resident] no model management to hook", exc_info=True)
        return False

    stock = getattr(mm, "unload_all_models", None)
    if not callable(stock) or getattr(stock, HOOKED, False):
        return False

    @functools.wraps(stock)
    def unload_all_models(*args, **kwargs):
        result = stock(*args, **kwargs)
        if not getattr(_OWN, "depth", 0):
            try:
                release("ComfyUI unloaded its own models")
            except Exception:
                log.warning(
                    "[minimax_h3_rewriter.resident] could not unload alongside ComfyUI",
                    exc_info=True,
                )
        return result

    setattr(unload_all_models, HOOKED, True)
    mm.unload_all_models = unload_all_models
    log.info("[minimax_h3_rewriter] unloading hooked into ComfyUI's own")
    return True
