"""Small pictures of what is plugged into a reference strip, for the strip to show.

The squares under a writer say what a reference is *for* and where it sits, and
until now nothing about what it *is*: three pictures read as three blue squares,
and putting the right one first meant remembering which socket it came in on.
So every run, the node sends its strip a glimpse of each connected reference --
a frame of a picture, a few frames of a clip, the shape of a sound -- and the
strip draws it into the square.

Two things are worth knowing:

- **It is what the node was given.** A picture that went through a resize or a
  crop arrives here as the result, which a browser has no way to see. Before
  the first run the strip can only show a loader's own file; after it, this --
  the one preview that is right for any graph.
- **It is best-effort all the way down.** A reference that cannot be read gets
  no preview and costs nothing else. The run it rides on is the point, and a
  missing thumbnail is never a reason to stop one.
"""

from __future__ import annotations

import base64
import io
import logging

from . import media
from .snapshot import kind_of

log = logging.getLogger(__name__)

EVENT = "minimax_h3_rewriter.previews"

SIZE = 96
FRAMES = 4
BARS = 24
JPEG_QUALITY = 82

LISTEN_SECONDS = 6.0
LISTEN_RATE = 22050
LISTEN_BITRATE = 48000

LAST: dict[str, dict] = {}


def peaks(samples, bars: int = BARS) -> list[float]:
    """The loudest moment in each of ``bars`` equal stretches, the loudest overall as 1.0.

    Peaks rather than averages: speech is mostly the gaps between words, and
    averaged it draws as a flat line with nothing to tell one voice from
    another. Silence is all zeros rather than a division by nothing.
    """
    import numpy

    values = numpy.abs(numpy.asarray(samples, dtype=numpy.float32)).reshape(-1)
    count = int(values.shape[0])
    if not count or bars <= 0:
        return []
    edges = numpy.linspace(0, count, bars + 1).astype(int)
    found = [
        float(values[start:end].max()) if end > start else 0.0
        for start, end in zip(edges[:-1], edges[1:])
    ]
    top = max(found)
    if top <= 0:
        return [0.0] * len(found)
    return [round(value / top, 2) for value in found]


def _jpeg(image) -> str:
    """One PIL frame as a square JPEG data URI, centre-cropped rather than squashed."""
    from PIL import Image, ImageOps

    fitted = ImageOps.fit(image.convert("RGB"), (SIZE, SIZE), Image.LANCZOS)
    buffer = io.BytesIO()
    fitted.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _pictures(frames) -> dict:
    if not frames:
        return {}
    shots = [_jpeg(frame) for frame in frames]
    width, height = frames[0].size
    found = {"thumb": shots[0], "width": width, "height": height}
    if len(shots) > 1:
        found["frames"] = shots
    return found


def _of_image(value) -> dict:
    found = _pictures(media.pil_frames(value, max_frames=FRAMES))
    shape = getattr(value, "shape", None)
    if shape is not None and len(shape) == 4 and int(shape[0]) > 1:
        found["count"] = int(shape[0])
    return found


def _of_video(value) -> dict:
    with media.Workspace() as workspace:
        batch, total, seconds = media.video_tensor(value, workspace, max_frames=FRAMES)
    found = _pictures(media.pil_frames(batch, max_frames=FRAMES))
    if total:
        found["count"] = int(total)
    if seconds:
        found["seconds"] = round(float(seconds), 2)
    return found


def opening(samples, rate: int, seconds: float = LISTEN_SECONDS):
    """The first ``seconds`` of a sound as one channel: the channels averaged, not picked."""
    import numpy

    data = numpy.asarray(samples, dtype=numpy.float32)
    if data.ndim == 2:
        data = data.mean(axis=0)
    return numpy.ascontiguousarray(data.reshape(-1)[: max(0, int(round(seconds * rate)))])


def _mp3(samples, rate: int) -> str:
    """A mono sound as a small MP3 data URI. MP3 because every browser plays it.

    A sound that reached the node through other nodes has no file the page could
    fetch, so the strip gets its opening seconds this way -- a few tens of
    kilobytes, where the waveform itself would be megabytes.
    """
    import av
    import numpy

    if not samples.size:
        raise ValueError("nothing to encode")
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp3") as container:
        stream = container.add_stream("libmp3lame", rate=LISTEN_RATE, layout="mono")
        stream.bit_rate = LISTEN_BITRATE
        frame = av.AudioFrame.from_ndarray(
            numpy.clip(samples, -1.0, 1.0).reshape(1, -1), format="fltp", layout="mono"
        )
        frame.sample_rate = int(rate)
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return "data:audio/mpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _of_audio(value) -> dict:
    import numpy

    waveform, rate = media.audio_parts(value)
    if waveform is None:
        return {}
    array = (
        waveform.detach().cpu().numpy()
        if hasattr(waveform, "detach")
        else numpy.asarray(waveform, dtype=numpy.float32)
    )
    if array.ndim == 3:
        array = array[0]
    found = {"peaks": peaks(numpy.abs(array).max(axis=0) if array.ndim == 2 else array)}
    if rate:
        found["seconds"] = round(int(array.shape[-1]) / float(rate), 2)
        try:
            found["listen"] = _mp3(opening(array, int(rate)), int(rate))
        except Exception as error:
            log.info("[minimax_h3_rewriter.previews] a sound could not be encoded (%s)", error)
    return found


MEASURE = {"image": _of_image, "video": _of_video, "audio": _of_audio}


def describe(items) -> dict[str, dict]:
    """``{slot: preview}`` for every ``(slot, value)`` that holds something.

    A slot is always there once connected, with at least its kind: the strip
    tells an unreadable reference from a missing one by that.
    """
    found: dict[str, dict] = {}
    for slot, value in items:
        if value is None:
            continue
        kind = kind_of(value)
        entry = {"kind": kind}
        try:
            entry.update(MEASURE[kind](value))
        except Exception as error:
            log.info(
                "[minimax_h3_rewriter.previews] %s could not be previewed (%s)", slot, error
            )
        found[str(slot)] = entry
    return found


def announce(node_id, items) -> None:
    """Describe what a node was given and hand it to the strip. Never raises."""
    if node_id is None:
        return
    try:
        slots = describe(items)
    except Exception:
        log.info("[minimax_h3_rewriter.previews] no previews this run", exc_info=True)
        return
    LAST[str(node_id)] = slots
    try:
        from server import PromptServer

        PromptServer.instance.send_sync(EVENT, {"node": str(node_id), "slots": slots})
    except Exception:
        log.debug("[minimax_h3_rewriter.previews] could not send the previews", exc_info=True)
