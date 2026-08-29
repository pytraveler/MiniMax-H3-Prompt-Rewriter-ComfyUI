"""Turning ComfyUI's in-memory media into files a subprocess can open.

``llama-mtmd-cli`` takes paths, not tensors, so an IMAGE has to become a PNG and
an AUDIO a WAV before any of it can be described -- and a VIDEO becomes a
handful of PNGs, for reasons :func:`video_frames` sets out. Everything lands in
one temporary directory that is removed on the way out, so a workflow run leaves
nothing behind even when the child crashes.

Two deliberate choices:

- **WAV is written with the standard library**, not torchaudio or soundfile.
  Both are usually present in a ComfyUI install and neither is guaranteed, and a
  captioner that fails to import is worse than one that writes 16-bit PCM by
  hand -- which is eleven lines and exactly what llama.cpp wants anyway.
- **Frames are sampled, not dumped.** An IMAGE batch out of a video loader is
  hundreds of frames; passing all of them would blow the context window and the
  wall clock. A handful spread evenly across the batch describes the clip about
  as well, and the caller is told how many were used.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import tempfile
import wave

log = logging.getLogger(__name__)

#: Frames taken from an IMAGE batch when it is longer than one.
DEFAULT_MAX_FRAMES = 8

VIDEO_SUFFIX = ".mp4"

PATCH = 28

AUDIO_TOKENS_PER_SECOND = 25


def token_cost(width: int, height: int, patch: int = PATCH) -> int:
    """How many tokens a picture of this size costs the vision tower."""
    return max(1, (int(width) // patch) * (int(height) // patch))


def fit_pixels(image, max_pixels: int, patch: int = PATCH):
    """Scale a picture down to ``max_pixels``, on the grid the tower counts in.

    Down only, and never below one block a side. Each adapter's own inference
    script sets this ceiling on its processor, so a picture arriving at full
    size is outside what the model was trained to look at -- and expensive with
    it: a 1616x1616 frame is 3249 tokens, and two of them overflow an 8k context
    before a word of the prompt has been counted. The GGUF route has no
    processor to do this, so it is done here for both.
    """
    if max_pixels <= 0:
        return image
    width, height = image.size
    if width * height <= max_pixels:
        return image
    scale = math.sqrt(width * height / max_pixels)
    fitted = (
        max(patch, int(width / scale) // patch * patch),
        max(patch, int(height / scale) // patch * patch),
    )

    from PIL import Image

    log.info(
        "[minimax_h3_rewriter.media.fit_pixels] %dx%d -> %dx%d, %d tokens",
        width, height, fitted[0], fitted[1], token_cost(*fitted, patch),
    )
    return image.resize(fitted, Image.LANCZOS)


class Workspace:
    """A temporary directory that cleans up after itself."""

    def __init__(self, prefix: str = "minimax_h3_media_"):
        self.path = tempfile.mkdtemp(prefix=prefix)

    def file(self, name: str) -> str:
        return os.path.join(self.path, name)

    def close(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _numpy():
    import numpy

    return numpy


def frame_indices(count: int, limit: int) -> list[int]:
    """Evenly spread ``limit`` indices across ``count`` frames, endpoints included."""
    if count <= 0:
        return []
    if count <= limit or limit <= 1:
        return list(range(min(count, max(limit, 1))))
    step = (count - 1) / (limit - 1)
    return sorted({int(round(index * step)) for index in range(limit)})


def pil_frames(
    image,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_pixels: int = 0,
    patch: int = PATCH,
) -> list:
    """The frames of an IMAGE batch as PIL images, thinned to ``max_frames``.

    A subprocess engine needs these on disk and a Transformers processor needs
    them in memory, so the conversion from ComfyUI's float tensor lives here and
    only the last step differs.

    ``max_pixels`` scales each frame down to fit; 0 leaves it as it came.
    """
    from PIL import Image

    numpy = _numpy()

    array = image.detach().cpu().numpy() if hasattr(image, "detach") else numpy.asarray(image)
    if array.ndim == 3:
        array = array[None, ...]
    if array.ndim != 4:
        raise ValueError(f"expected an IMAGE tensor of shape (batch, height, width, channels), got {array.shape}")

    frames = []
    for index in frame_indices(array.shape[0], max_frames):
        frame = numpy.clip(array[index] * 255.0 + 0.5, 0, 255).astype(numpy.uint8)
        if frame.shape[-1] == 1:
            frame = frame[..., 0]
        frames.append(fit_pixels(Image.fromarray(frame), max_pixels, patch))
    return frames


def image_files(
    image,
    workspace: Workspace,
    max_frames: int = DEFAULT_MAX_FRAMES,
    prefix: str = "frame",
    max_pixels: int = 0,
    patch: int = PATCH,
) -> list[str]:
    """Write an IMAGE batch out as PNGs. Returns the paths, in order.

    ``prefix`` keeps two calls on one workspace from writing over each other,
    which is what the 8B rewriter does with its first and last reference frame.
    """
    paths = []
    for position, frame in enumerate(pil_frames(image, max_frames, max_pixels, patch)):
        path = workspace.file(f"{prefix}_{position:03d}.png")
        frame.save(path, format="PNG")
        paths.append(path)
    return paths


def _audio_parts(audio) -> tuple[object, object]:
    """Pull ``waveform`` and ``sample_rate`` out of whatever an AUDIO input is.

    ComfyUI's own AUDIO is a plain ``dict``, but nothing enforces that and the
    common video loaders do not oblige: VideoHelperSuite hands over a
    ``LazyAudioMap``, a ``Mapping`` that runs ffmpeg the first time a key is
    read. ``isinstance(audio, dict)`` says no to it, which is how a perfectly
    good audio track ended up rejected. Ask for the two keys instead of asking
    what type the container is -- and reading them is what makes a lazy input
    decode, so it has to happen here rather than in a membership test.
    """
    from collections.abc import Mapping

    if isinstance(audio, Mapping):
        return audio.get("waveform"), audio.get("sample_rate")
    return getattr(audio, "waveform", None), getattr(audio, "sample_rate", None)


audio_parts = _audio_parts


def audio_file(audio, workspace: Workspace, name: str = "audio.wav") -> str:
    """Write a ComfyUI AUDIO input out as 16-bit PCM WAV. Returns the path."""
    numpy = _numpy()

    waveform, sample_rate = _audio_parts(audio)
    if waveform is None:
        raise ValueError(
            "expected a ComfyUI AUDIO input with a 'waveform' and a 'sample_rate', got "
            f"{type(audio).__name__}"
        )

    rate = int(sample_rate or 44100)

    array = waveform.detach().cpu().numpy() if hasattr(waveform, "detach") else numpy.asarray(waveform)
    if array.ndim == 3:  # (batch, channels, samples) -- only the first clip is described
        array = array[0]
    if array.ndim == 1:
        array = array[None, :]
    if array.ndim != 2:
        raise ValueError(f"expected an AUDIO waveform of shape (channels, samples), got {array.shape}")

    # (channels, samples) -> interleaved (samples, channels), which is what a WAV
    # frame actually is.
    interleaved = numpy.ascontiguousarray(array.T)
    clipped = numpy.clip(interleaved, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")

    path = workspace.file(name)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(int(array.shape[0]))
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return path


def wav_seconds(path: str) -> float:
    """How long a WAV this module wrote runs for, for costing it in tokens."""
    try:
        with wave.open(path, "rb") as handle:
            rate = handle.getframerate() or 1
            return handle.getnframes() / float(rate)
    except (OSError, wave.Error):
        log.debug("[minimax_h3_rewriter.media.wav_seconds] cannot read %s", path, exc_info=True)
        return 0.0


SEEK_ABOVE_FRAMES = 300


def _video_source(video, workspace: Workspace):
    """Something PyAV can open: the original file if there is one, else a copy."""
    source = getattr(video, "_VideoFromFile__file", None)
    if isinstance(source, str) and os.path.isfile(source):
        return source
    if hasattr(source, "read") and hasattr(source, "seek"):
        source.seek(0)
        return source

    path = workspace.file("video" + VIDEO_SUFFIX)
    save_to = getattr(video, "save_to", None)
    if callable(save_to):
        save_to(path)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        raise RuntimeError("the VIDEO input produced an empty file")

    raise ValueError(
        "this VIDEO input cannot be read: it is neither a file on disk nor something with a "
        "'save_to'. Feed the frames into the 'image' input instead."
    )


def _frame_count(stream) -> int:
    """How many frames the stream says it has, or an estimate, or 0."""
    if stream.frames:
        return int(stream.frames)
    rate = float(stream.average_rate or 0)
    if stream.duration and stream.time_base and rate:
        return int(float(stream.duration * stream.time_base) * rate)
    return 0


def _in_order(container, stream, wanted: list[int]):
    """Decode from the start, yielding the wanted frames and stopping after the last."""
    remaining = list(wanted)
    for position, frame in enumerate(container.decode(stream)):
        if not remaining:
            return
        if position >= remaining[0]:
            remaining.pop(0)
            yield frame


def _by_seeking(container, stream, wanted: list[int], total: int):
    """Jump straight to each wanted frame -- eight seeks instead of a full decode.

    A seek lands on the keyframe *before* the target, so the frames after it are
    decoded until the target is reached. Taking the keyframe itself would be
    cheaper and wrong: with a 250-frame GOP, eight samples spread over a
    thousand frames would collapse onto four keyframes and describe the clip
    twice over.
    """
    for index in wanted:
        target = int(index / max(total, 1) * float(stream.duration))
        container.seek(target, stream=stream)
        for frame in container.decode(stream):
            if frame.pts is not None and frame.pts < target:
                continue
            yield frame
            break


def _save(frames, workspace: Workspace, max_pixels: int = 0) -> list[str]:
    """Write frames out as PNGs one at a time, so no batch is ever held in memory."""
    paths = []
    for position, frame in enumerate(frames):
        path = workspace.file(f"frame_{position:03d}.png")
        fit_pixels(frame.to_image(), max_pixels).save(path, format="PNG")
        paths.append(path)
    return paths


def _stack(frames, workspace: Workspace, max_pixels: int = 0):
    """Turn frames into one ComfyUI IMAGE batch: ``(frames, height, width, 3)``, 0 to 1.

    ``workspace`` is unused and kept so this can stand in for :func:`_save`.
    ``max_pixels`` is accepted for the same reason and ignored: this batch goes
    to a Transformers processor, which does its own scaling from its own config.
    """
    import torch

    numpy = _numpy()

    batch = [
        torch.from_numpy(frame.to_ndarray(format="rgb24").astype(numpy.float32) / 255.0)
        for frame in frames
    ]
    return torch.stack(batch) if batch else None


def _empty(result) -> bool:
    """Whether a sink produced nothing, for a list of paths or a tensor alike."""
    return result is None or len(result) == 0


def _collect(video, workspace: Workspace, max_frames: int, sink, max_pixels: int = 0):
    """Decode the sampled frames of a VIDEO and hand them to ``sink``.

    Both routes share this, so a clip is described from exactly the same frames
    whether it goes to a subprocess as PNGs or to a resident model as a tensor.
    The sink is given an iterator rather than a list, which is what lets the PNG
    path avoid holding a whole batch at once.

    Returns ``(whatever the sink returned, total frames, seconds)``.
    """
    import av

    source = _video_source(video, workspace)
    result = None

    with av.open(source) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        total = _frame_count(stream)
        seconds = float(container.duration / av.time_base) if container.duration else 0.0
        wanted = frame_indices(total, max_frames) if total else list(range(max(max_frames, 1)))

        if total > SEEK_ABOVE_FRAMES and stream.duration:
            try:
                result = sink(
                    _by_seeking(container, stream, wanted, total), workspace, max_pixels
                )
            except Exception as error:
                log.info(
                    "[minimax_h3_rewriter.media._collect] seeking failed (%s), "
                    "decoding in order instead", error,
                )
                container.seek(0)
                result = None
        if _empty(result):
            result = sink(_in_order(container, stream, wanted), workspace, max_pixels)

    return result, total, seconds


def video_frames(
    video,
    workspace: Workspace,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_pixels: int = 0,
) -> tuple[list[str], int, float]:
    """Sample a VIDEO input into PNGs. Returns ``(paths, total frames, seconds)``.

    The frames are decoded here rather than handed to ``llama-mtmd-cli --video``
    for two reasons, one of them a hang and the other arithmetic:

    - mtmd shells out to ``ffprobe`` and feeds it the file through *stdin*. When
      the MP4 has its ``moov`` atom at the front -- which is what "faststart"
      means, and what ComfyUI, phones and most of the web produce -- ffprobe has
      what it needs after a few kilobytes and exits without reading the rest.
      llama.cpp is still writing the remaining megabytes into that pipe, and
      blocks there forever. Same clip with ``moov`` at the end: works. Verified
      by moving the atom and nothing else.
    - ``--video`` takes every frame. Two seconds at 25 fps is 56 images through
      the vision tower; a thirty-second clip is 750. ``max_frames`` exists so
      the cost of describing a clip does not depend on how long it is.
    """
    paths, total, seconds = _collect(video, workspace, max_frames, _save, max_pixels)
    if _empty(paths):
        raise RuntimeError("no frames could be decoded from the VIDEO input")
    return paths, max(total, len(paths)), seconds


def video_tensor(video, workspace: Workspace, max_frames: int = DEFAULT_MAX_FRAMES):
    """Sample a VIDEO input straight into an IMAGE batch. Returns ``(batch, total, seconds)``.

    The same frames :func:`video_frames` would have written out, kept in memory
    instead: the CLIP route hands tensors to a model that is already loaded, so
    a round trip through PNG would be pure cost. Everything about *which* frames
    are taken is shared, which is what makes a clip described through either
    route describable from the same evidence.
    """
    batch, total, seconds = _collect(video, workspace, max_frames, _stack)
    if _empty(batch):
        raise RuntimeError("no frames could be decoded from the VIDEO input")
    return batch, max(total, len(batch)), seconds
