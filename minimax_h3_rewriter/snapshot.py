"""Thumbnails and measurements of what a node was shown.

Taken during the run, because that is the only moment the media exists: by the
time a save dialog opens the node has returned strings and the tensors are gone.
So the session record and the library record are the same shape, and saving one
is a matter of naming it rather than of finding the pictures again.

Everything here is best-effort. A reference that cannot be measured is still
recorded, with fewer fields -- a missing thumbnail is never a reason to lose a
rewrite that took a minute to write.
"""

from __future__ import annotations

import base64
import io
import logging

from . import media

log = logging.getLogger(__name__)

THUMB = 50
DATA_URI = "data:image/png;base64,"


def kind_of(value) -> str:
    """Picture, clip or sound, asked of the value rather than of the socket.

    An IMAGE is a tensor and carries a shape, an AUDIO is a mapping holding a
    waveform -- possibly a lazy one, which is why membership is asked rather
    than the key read -- and a VIDEO is neither.
    """
    if hasattr(value, "shape"):
        return "image"
    if hasattr(value, "keys"):
        try:
            if "waveform" in value:
                return "audio"
        except TypeError:
            pass
    return "video"


def _square(image) -> str:
    """One PIL frame as a 50x50 PNG data URI, centre-cropped rather than squashed."""
    from PIL import Image, ImageOps

    fitted = ImageOps.fit(image.convert("RGB"), (THUMB, THUMB), Image.LANCZOS)
    buffer = io.BytesIO()
    fitted.save(buffer, format="PNG", optimize=True)
    return DATA_URI + base64.b64encode(buffer.getvalue()).decode("ascii")


def _of_image(value) -> dict:
    frames = media.pil_frames(value, max_frames=1)
    if not frames:
        return {}
    width, height = frames[0].size
    found = {"width": width, "height": height, "thumb": _square(frames[0])}
    shape = getattr(value, "shape", None)
    if shape is not None and len(shape) == 4 and int(shape[0]) > 1:
        found["frames"] = int(shape[0])
    return found


def _of_video(value) -> dict:
    with media.Workspace() as workspace:
        batch, total, seconds = media.video_tensor(value, workspace, max_frames=1)
        frames = media.pil_frames(batch, max_frames=1)

    found = {}
    if frames:
        width, height = frames[0].size
        found.update({"width": width, "height": height, "thumb": _square(frames[0])})
    if total:
        found["frames"] = int(total)
    if seconds:
        found["seconds"] = round(float(seconds), 2)
        if total:
            found["fps"] = round(total / seconds, 2)
    return found


def _of_audio(value) -> dict:
    waveform, rate = media.audio_parts(value)
    rate = int(rate or 0)
    shape = getattr(waveform, "shape", None)
    if not shape or not rate:
        return {}
    found = {"rate": rate, "channels": int(shape[-2]) if len(shape) > 1 else 1}
    found["seconds"] = round(int(shape[-1]) / rate, 2)
    return found


MEASURE = {"image": _of_image, "video": _of_video, "audio": _of_audio}


def take(items) -> list[dict]:
    """``_take`` with a net under it: a record is worth more than its pictures."""
    try:
        return _take(items)
    except Exception:
        log.info(
            "[minimax_h3_rewriter.snapshot] the references could not be described",
            exc_info=True,
        )
        return []


def _take(items) -> list[dict]:
    """Describe every reference a node was given, in the order it numbered them.

    ``items`` is an iterable of ``(slot, kind, value)``; the kind may be None and
    is then read off the value. Labels are positional on purpose -- ``ref1-image``
    rather than a file name, which a node cannot see anyway.
    """
    taken = []
    for slot, kind, value in items:
        if value is None:
            continue
        kind = kind or kind_of(value)
        found = {"label": f"ref{len(taken) + 1}-{kind}", "slot": slot, "kind": kind}
        try:
            found.update(MEASURE.get(kind, lambda _value: {})(value))
        except Exception as error:
            log.info(
                "[minimax_h3_rewriter.snapshot] %s could not be measured (%s), recording it "
                "without a thumbnail", found["label"], error,
            )
        taken.append(found)
    return taken
