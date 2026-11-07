"""Bounded media copies from connected ComfyUI values, never model-supplied paths.

Video is a declared sequence of JPEG frames, not an opaque video upload. Only
an enabled clip sound switch can produce an input_audio part. Decoding and
encoding yield to the host between frames/chunks and own no background task.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import math
import os
import wave
from dataclasses import dataclass, field, replace
from fractions import Fraction

from . import media, refmods
from .api_contract import ContractError, ReferenceSpec, finite_number, positive_int
from .api_media import (MAX_IMAGES, MAX_REFERENCES, MAX_SOURCE_PIXELS, SLOT,
                        ImageReference, PreparedImage, PreparedVideoFrame, _mapping, prepare_images)

REFERENCE_TYPES = "IMAGE,VIDEO,AUDIO," + refmods.TYPE
MAX_SOURCE_BYTES = 2 << 30
MAX_DECODE_FRAMES = 20000
FORMATS = "mov,matroska,webm,avi,mpeg,mpegts,ogg,flv,gif"


@dataclass(frozen=True)
class MediaReference:
    spec: ReferenceSpec
    value: object = field(repr=False, compare=False)
    handed: object = field(repr=False, compare=False)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedAudio:
    slot: str
    component: str
    samples: int
    sample_rate: int
    source_sample_rate: int
    source_channels: int
    start_seconds: float
    source_seconds: float
    wav: bytes = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.slot, str) or SLOT.fullmatch(self.slot) is None
                or self.component not in ("media", "sound")
                or any(not positive_int(value) for value in (
                    self.samples, self.sample_rate, self.source_sample_rate, self.source_channels))
                or any(not finite_number(value) or value < 0 for value in (self.start_seconds, self.source_seconds))
                or not isinstance(self.wav, bytes) or self.wav[:4] != b"RIFF" or self.wav[8:12] != b"WAVE"):
            raise ContractError("Invalid prepared audio transport.")

    def content(self):
        return {"type": "input_audio", "input_audio": {
            "format": "wav", "data": base64.b64encode(self.wav).decode("ascii")}}

    def metadata(self):
        seconds = self.samples / self.sample_rate
        return {"slot": self.slot, "component": self.component, "kind": "audio", "format": "wav",
                "sample_rate": self.sample_rate, "channels": 1, "samples": self.samples,
                "seconds": round(seconds, 6), "start_seconds": round(self.start_seconds, 6),
                "source_sample_rate": self.source_sample_rate, "source_channels": self.source_channels,
                "source_seconds": round(self.source_seconds, 6), "bytes": len(self.wav),
                "resampled": self.sample_rate != self.source_sample_rate,
                "downmixed": self.source_channels != 1,
                "trimmed": self.start_seconds > 0 or seconds + 1 / self.sample_rate < self.source_seconds}


@dataclass(frozen=True)
class Transport:
    images: tuple = ()
    audios: tuple = ()
    notes: tuple[str, ...] = ()
    videos: tuple[dict, ...] = ()

    def metadata(self):
        return [one.metadata() for one in (*self.images, *self.audios)]


def _slot_map(raw, label):
    found = _mapping(raw, label)
    if any(not isinstance(key, str) or SLOT.fullmatch(key) is None for key in found):
        raise ContractError("Invalid reference text slots.")
    return found


def arrange_references(supplied, raw_layout="{}", raw_instructions="{}", raw_descriptions="{}"):
    """Metadata only: disabled inputs and lazy audio waveforms are not read."""
    if not isinstance(supplied, dict) or any(not isinstance(key, str) or SLOT.fullmatch(key) is None for key in supplied):
        raise ContractError("Invalid media reference inputs.")
    layout = _mapping(raw_layout, "reference layout")
    instructions = _slot_map(raw_instructions, "reference instructions")
    descriptions = _slot_map(raw_descriptions, "reference descriptions")
    if set(layout) - {"order", "off", "roles", "sound"}:
        raise ContractError("Unsupported reference layout fields.")
    for key in ("order", "off", "sound"):
        names = layout.get(key, [])
        if not isinstance(names, list) or len(names) > MAX_REFERENCES or any(
            not isinstance(name, str) or SLOT.fullmatch(name) is None for name in names
        ) or len(set(names)) != len(names):
            raise ContractError("Invalid reference layout slots.")
    roles = layout.get("roles", {})
    if not isinstance(roles, dict) or any(not isinstance(key, str) or SLOT.fullmatch(key) is None
                                         or value not in ("Picture", "Subject", "Video") for key, value in roles.items()):
        raise ContractError("Invalid media reference roles.")
    order = list(layout.get("order", []))
    order += sorted(set(supplied) - set(order), key=lambda name: int(name[4:]))
    assets = []
    for slot in order:
        original = supplied.get(slot)
        if original is None or slot in layout.get("off", []):
            continue
        mod = original if refmods.is_refmod(original) else None
        kind = mod.kind if mod is not None else _kind(original)
        if kind not in ("image", "video", "audio"):
            raise ContractError(slot + ": connect an IMAGE, VIDEO, AUDIO or RefMod object; paths and URLs are not accepted.")
        role = {"video": "Video", "audio": "Audio"}.get(kind, roles.get(slot, "Picture"))
        if mod is not None and kind == "image" and role == "Video":
            raise ContractError(slot + ": an image RefMod can be Picture or Subject.")
        sound = slot in layout.get("sound", [])
        if sound and (kind != "video" or mod is not None):
            raise ContractError(slot + ": only a connected VIDEO can have a separate clip sound track.")
        instruction = instructions.get(slot, "")
        if isinstance(instruction, dict) and set(instruction) <= {"text", "add"}:
            if type(instruction.get("add", True)) is not bool:
                raise ContractError("Invalid reference instruction mode.")
            instruction = instruction.get("text", "")
        description = descriptions.get(slot, "")
        audio_description = ""
        if isinstance(description, dict) and set(description) <= {"text", "sound"}:
            audio_description = description.get("sound", "")
            description = description.get("text", "")
        if any(not isinstance(text, str) for text in (instruction, description, audio_description)):
            raise ContractError("Reference descriptions and instructions must be text.")
        value = original
        if mod is not None:
            saved = mod.voice if kind == "audio" else mod.appearance
            description = "\n".join(text for text in (description, saved) if text.strip())
            instruction = "\n".join(text for text in (instruction, refmods.hint(mod)) if text.strip())
            value = mod.sound if kind == "audio" else mod.pictures
            if value is None:
                value = mod.view
        spec = ReferenceSpec(slot, role, kind, description, instruction, sound, audio_description,
                             refmod=mod is not None)
        assets.append(MediaReference(spec, value, original))
    pairs = refmods.halves((one.spec.slot, one.handed) for one in assets if one.spec.refmod)
    return tuple(replace(one, spec=replace(one.spec, related_slots=(pairs[one.spec.slot],)))
                 if one.spec.slot in pairs else one for one in assets)


def _kind(value):
    from .references import kind_of

    return kind_of(value)


def _checkpoint(cancelled):
    from .api_client import _cancelled

    _cancelled(cancelled)


def _av():
    try:
        import av
    except ImportError:
        raise ContractError("Audio/video copies require PyAV (av), declared in this pack's dependencies. Install dependencies and restart ComfyUI.") from None
    return av


@contextlib.contextmanager
def _container(video):
    """Open only a connected video's local file/BytesIO, with nested protocols disabled."""
    source = None
    try:
        getter = getattr(video, "get_stream_source", None)
        if callable(getter):
            source = getter()
        if source is None:
            source = getattr(video, "_VideoFromFile__file", None)
    except Exception:
        raise ContractError("The connected VIDEO source could not be read.") from None
    with contextlib.ExitStack() as stack:
        if isinstance(source, str):
            if "://" in source or source.startswith(("\\\\", "//")) or not os.path.isabs(source) or not os.path.isfile(source):
                raise ContractError("VIDEO requires an existing local file source; remote URLs are not accepted.")
            try:
                if os.path.getsize(source) > MAX_SOURCE_BYTES:
                    raise ContractError("VIDEO exceeds the 2 GiB source limit. Trim it upstream.")
                source = stack.enter_context(open(source, "rb"))
            except OSError:
                raise ContractError("The connected VIDEO file could not be opened.") from None
        elif isinstance(source, io.BytesIO):
            if source.getbuffer().nbytes > MAX_SOURCE_BYTES:
                raise ContractError("VIDEO exceeds the 2 GiB source limit. Trim it upstream.")
            position = source.tell()
            stack.callback(source.seek, position)
            source.seek(0)
        else:
            raise ContractError("VIDEO has no supported local stream source or image components.")
        try:
            container = _av().open(source, mode="r", options={
                "protocol_whitelist": "", "format_whitelist": FORMATS})
        except Exception:
            raise ContractError("The connected VIDEO could not be decoded as a supported local container.") from None
        stack.callback(container.close)
        yield container


def _components(video):
    if hasattr(video, "_VideoFromFile__file"):
        return None
    if type(video).__name__ == "VideoFromList":
        raise ContractError("Accumulated VIDEO inputs must be saved and reloaded as a local clip, or used in descriptions mode with clip sound off. This avoids materializing the entire concatenation.")
    getter = getattr(video, "get_components", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            raise ContractError("The connected VIDEO components could not be read.") from None
    return None


async def resolve_clip_sounds_async(assets, *, cancelled=None):
    """Probe enabled tracks before assigning labels, including on cached reuse.

    No waveform or visual frame is decoded here. This also lets an image-only
    profile accept a silent clip with an idle sound switch, while rejecting
    an actual sound track before any audio conversion.
    """
    resolved = []
    for asset in assets:
        _checkpoint(cancelled)
        if asset.spec.clip_audio:
            components = _components(asset.value)
            if components is not None:
                present = getattr(components, "audio", None) is not None
            else:
                with _container(asset.value) as container:
                    present = bool(container.streams.audio)
            if not present:
                note = asset.spec.slot + ": sound was enabled, but this clip has no audio stream; no Audio label was assigned."
                asset = replace(asset, spec=replace(asset.spec, clip_audio=False), notes=asset.notes + (note,))
        resolved.append(asset)
        await asyncio.sleep(0)
    return tuple(resolved)


def _trim(video):
    getter = getattr(video, "get_active_trim_window", None)
    try:
        start, duration = getter() if callable(getter) else (0.0, 0.0)
    except Exception:
        raise ContractError("The VIDEO trim window could not be read.") from None
    if any(not finite_number(one) or one < 0 for one in (start, duration)):
        raise ContractError("Invalid VIDEO trim window.")
    return float(start), float(duration)


def _window(video, seconds, source_seconds):
    start, trimmed = _trim(video)
    duration = min(seconds, trimmed) if trimmed else seconds
    if source_seconds > 0:
        duration = min(duration, max(0.0, source_seconds - start))
    if duration <= 0:
        raise ContractError("The VIDEO trim window contains no frames.")
    return start, duration


def _targets(seconds, interval, maximum):
    count = max(1, int(math.ceil(seconds / interval - 1e-9)))
    if count <= maximum:
        return [index * interval for index in range(count)]
    if maximum == 1:
        return [0.0]
    return [index * ((count - 1) * interval) / (maximum - 1) for index in range(maximum)]


def _shape(value):
    shape = getattr(value, "shape", None)
    if shape is None or len(shape) != 4 or shape[-1] not in (1, 3, 4) or any(int(n) <= 0 for n in shape):
        raise ContractError("Each visual reference must contain a nonempty IMAGE batch.")
    if int(shape[1]) * int(shape[2]) > MAX_SOURCE_PIXELS:
        raise ContractError("A reference image exceeds the source pixel limit. Resize it upstream.")
    return tuple(map(int, shape))


async def _batch_frames(asset, value, *, seconds, interval, maximum, fps, cancelled):
    count, _height, _width, _channels = _shape(value)
    mod = asset.handed if asset.spec.refmod else None
    if mod is not None and mod.kind == "video":
        value, times = refmods.encoder_frames(mod) if mod.pictures is not None else (value, [])
        count = int(value.shape[0])
        if not times:
            raise ContractError(asset.spec.slot + ": decoded RefMod video needs pictures with timestamps.")
        if any(not finite_number(t) or t < 0 for t in times) or any(b <= a for a, b in zip(times, times[1:])):
            raise ContractError("Invalid RefMod frame timestamps.")
        source_seconds = times[-1] + (times[-1] - times[-2] if len(times) > 1 else 0.5)
    else:
        times = None
        source_seconds = count / fps
    window = min(seconds, source_seconds)
    indices = []
    position = 0
    for target in _targets(window, interval, maximum):
        if times is None:
            position = min(count - 1, int(math.floor(target * fps + 1e-9)))
        else:
            while position + 1 < count and times[position + 1] <= target + 1e-9:
                position += 1
        if not indices or indices[-1] != position:
            indices.append(position)
    output = []
    for position in indices:
        _checkpoint(cancelled)
        at = position / fps if times is None else times[position]
        output.append((value[position:position + 1], at, at))
        await asyncio.sleep(0)
    return output, source_seconds, window


async def _file_frames(video, *, seconds, interval, maximum, max_side, byte_limit, cancelled):
    import numpy as np

    output = []
    encoded_bytes = 0
    with _container(video) as container:
        if not container.streams.video:
            raise ContractError("The connected VIDEO has no video stream.")
        stream = container.streams.video[0]
        if stream.width * stream.height > MAX_SOURCE_PIXELS:
            raise ContractError("VIDEO frames exceed the source pixel limit. Resize upstream.")
        source_seconds = float(stream.duration * stream.time_base) if stream.duration else 0.0
        if not source_seconds and container.duration:
            source_seconds = float(container.duration / _av().time_base)
        start, window = _window(video, seconds, source_seconds)
        targets = [start + value for value in _targets(window, interval, maximum)]
        origin = float((stream.start_time or 0) * stream.time_base)
        if start > 0:
            try:
                container.seek(int((start + origin) / stream.time_base), stream=stream)
            except Exception:
                raise ContractError("The VIDEO could not seek to its trim start.") from None
        last = None
        frames = 0
        for frame in container.decode(stream):
            _checkpoint(cancelled)
            await asyncio.sleep(0)
            frames += 1
            if frames > MAX_DECODE_FRAMES:
                raise ContractError("VIDEO decoding exceeds the frame limit. Trim or reduce its frame rate upstream.")
            if frame.time is None:
                raise ContractError("VIDEO frames need presentation timestamps.")
            at = float(frame.time) - origin
            if at + 1e-9 < start:
                continue
            if at >= start + window - 1e-9:
                break
            if not targets:
                break
            if at + 1e-9 < targets[0]:
                continue
            if last is not None and at <= last:
                raise ContractError("VIDEO frame timestamps must increase.")
            array = frame.to_ndarray(format="rgb24")
            rotation = getattr(frame, "rotation", 0)
            if rotation:
                array = np.rot90(array, k=int(round(rotation // 90)) % 4).copy()
            crop = getattr(video, "_VideoFromFile__crop", None)
            if crop is not None:
                from comfy_api.latest._util.video_types import normalize_crop_rect

                rect = normalize_crop_rect(*crop, array.shape[1], array.shape[0])
                if rect:
                    x, y, width, height = rect
                    array = array[y:y + height, x:x + width]
            from PIL import Image

            height, width = array.shape[:2]
            image = Image.fromarray(array)
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            batch = np.asarray(image, dtype=np.float32)[None] / 255.0
            encoded = prepare_images((ImageReference(ReferenceSpec("ref_0", "Picture", "image"), batch),),
                                     max_side=max_side, byte_limit=byte_limit - encoded_bytes)[0]
            encoded_bytes += 4 * ((len(encoded.jpeg) + 2) // 3)
            output.append((replace(encoded, source_width=width, source_height=height), max(0.0, at - start), at))
            last = at
            while targets and targets[0] <= at + 1e-9:
                targets.pop(0)
            if not targets:
                break
    if not output:
        raise ContractError("No VIDEO frame was decoded inside its trim window.")
    return output, source_seconds, window


def _audio_budget(seconds, rate, byte_limit):
    if 4 * ((44 + math.ceil(seconds * rate) * 2 + 2) // 3) > byte_limit:
        raise ContractError("Audio copies exceed the profile's request byte limit. Reduce audio_max_seconds or raise request_bytes in the local profile.")


def _pcm_audio(slot, component, chunks, rate, source_rate, channels, start, source_seconds):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        for chunk in chunks:
            handle.writeframesraw(chunk)
    data = buffer.getvalue()
    samples = (len(data) - 44) // 2
    if samples <= 0:
        raise ContractError("No audio samples were available in the selected window.")
    return PreparedAudio(slot, component, samples, rate, source_rate, channels, start, source_seconds, data)


def _pcm(frame):
    import numpy as np

    array = frame.to_ndarray()
    if not np.isfinite(array).all():
        raise ContractError("Audio samples must be finite numbers.")
    return (np.clip(array, -1, 1) * 32767.0).round().astype("<i2").tobytes()


async def _audio_copy(asset, value, *, seconds, rate, byte_limit, cancelled):
    import numpy as np

    waveform, source_rate = media.audio_parts(value)
    shape = getattr(waveform, "shape", None)
    if (not positive_int(source_rate) or not 1000 <= source_rate <= 384000 or shape is None
            or len(shape) not in (1, 2, 3) or any(int(n) <= 0 for n in shape)
            or len(shape) == 3 and int(shape[0]) != 1):
        raise ContractError("AUDIO requires one waveform batch and an integer sample rate; audio batches are not silently truncated.")
    channels = int(shape[-2]) if len(shape) > 1 else 1
    if channels > 8:
        raise ContractError("AUDIO supports at most eight input channels.")
    source_seconds = int(shape[-1]) / source_rate
    window = min(seconds, source_seconds)
    _audio_budget(window, rate, byte_limit)
    limit = min(int(shape[-1]), max(1, int(math.floor(window * source_rate))))
    wanted = max(1, int(round(limit * rate / source_rate)))
    av = _av()
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
    chunks, used = [], 0
    for offset in range(0, limit, max(1, source_rate // 2)):
        _checkpoint(cancelled)
        selected = waveform[..., offset:min(limit, offset + max(1, source_rate // 2))]
        array = selected.detach().cpu().numpy() if hasattr(selected, "detach") else np.asarray(selected)
        if array.dtype.kind not in "fiu" or not np.isfinite(array).all() or array.min() < -1 or array.max() > 1:
            raise ContractError("AUDIO samples must be finite numbers between minus one and one.")
        mono = np.ascontiguousarray(array.reshape(channels, -1).mean(axis=0, keepdims=True), dtype=np.float32)
        frame = av.AudioFrame.from_ndarray(mono, format="fltp", layout="mono")
        frame.sample_rate, frame.pts, frame.time_base = source_rate, offset, Fraction(1, source_rate)
        for out in resampler.resample(frame):
            data = _pcm(out)[:max(0, wanted - used) * 2]
            chunks.append(data)
            used += len(data) // 2
        await asyncio.sleep(0)
    for out in resampler.resample(None):
        data = _pcm(out)[:max(0, wanted - used) * 2]
        chunks.append(data)
        used += len(data) // 2
    return _pcm_audio(asset.spec.slot, "media", chunks, rate, source_rate, channels, 0.0, source_seconds)


async def _file_sound(asset, *, seconds, rate, byte_limit, cancelled, describe_only=False):
    av = _av()
    chunks, used = [], 0
    with _container(asset.value) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[-1]
        video_stream = container.streams.video[0] if container.streams.video else stream
        origin = float((video_stream.start_time or 0) * video_stream.time_base)
        source_seconds = float(video_stream.duration * video_stream.time_base) if video_stream.duration else 0.0
        if not source_seconds and container.duration:
            source_seconds = float(container.duration / av.time_base)
        start, window = _window(asset.value, seconds, source_seconds)
        if describe_only:
            return True
        _audio_budget(window, rate, byte_limit)
        source_rate, channels = int(stream.codec_context.sample_rate), len(stream.codec_context.layout.channels)
        wanted = max(1, int(round(window * rate)))
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
        if start > 0:
            container.seek(int((start + origin) / stream.time_base), stream=stream)
        position = 0
        for frame in container.decode(stream):
            _checkpoint(cancelled)
            await asyncio.sleep(0)
            position += 1
            if position > MAX_DECODE_FRAMES:
                raise ContractError("Clip audio decoding exceeds the frame limit. Trim upstream.")
            if frame.time is None:
                raise ContractError("Clip audio needs presentation timestamps.")
            at = float(frame.time) - origin
            if at >= start + window:
                break
            for out in resampler.resample(frame):
                begin = float(out.time) - origin
                left = max(0, int(round((start - begin) * rate)))
                right = min(out.samples, int(round((start + window - begin) * rate)))
                if right <= left:
                    continue
                offset = max(0, int(round((begin - start) * rate)) + left)
                if offset > used:
                    chunks.append(b"\0\0" * min(wanted - used, offset - used))
                    used = min(wanted, offset)
                data = _pcm(out)[left * 2:right * 2]
                overlap = max(0, used - offset)
                data = data[overlap * 2:][:max(0, wanted - used) * 2]
                chunks.append(data)
                used += len(data) // 2
            if used >= wanted:
                break
        for out in resampler.resample(None):
            if out.time is not None:
                begin = float(out.time) - origin
                left = max(0, int(round((start - begin) * rate)), int(round((start + used / rate - begin) * rate)))
                right = min(out.samples, int(round((start + window - begin) * rate)))
                data = _pcm(out)[left * 2:max(left, right) * 2][:max(0, wanted - used) * 2]
                chunks.append(data)
                used += len(data) // 2
    if used <= 0:
        raise ContractError("The clip sound track has no decodable samples in the selected window.")
    return _pcm_audio(asset.spec.slot, "sound", chunks, rate, source_rate, channels, start, source_seconds)


async def _prepare_references_async(assets, *, media_mode="native", max_side=1024, byte_limit=1 << 20,
                                   video_max_frames=8, video_sample_seconds=1.0, video_max_seconds=15.0,
                                   batch_video_fps=24.0, audio_max_seconds=30.0, audio_sample_rate=16000,
                                   target_seconds=15.0, cancelled=None):
    if media_mode not in ("native", "descriptions"):
        raise ContractError("Invalid media mode.")
    if (type(max_side) is not int or not 64 <= max_side <= 4096 or not positive_int(byte_limit)
            or type(video_max_frames) is not int or not 1 <= video_max_frames <= MAX_IMAGES
            or any(not finite_number(value) or value <= 0 for value in (
                video_sample_seconds, video_max_seconds, batch_video_fps, audio_max_seconds, target_seconds))
            or video_max_seconds > 120 or audio_max_seconds > 120 or not 1 <= batch_video_fps <= 240
            or audio_sample_rate not in (16000, 24000, 44100, 48000)):
        raise ContractError("Invalid audio/video transport limits.")
    images, audios, videos, resolved = [], [], [], []
    notes = [note for asset in assets for note in asset.notes]
    used = 0
    for asset in assets:
        _checkpoint(cancelled)
        spec, value = asset.spec, asset.value
        if media_mode == "native" and spec.refmod and value is None:
            port = "audio_vae" if spec.kind == "audio" else "vae"
            raise ContractError(spec.slot + ": RefMod has no decoded media. Connect the MiniMax-H3 " + port + " input on the RefMod loader, or explicitly use descriptions mode with a description.")
        components = _components(value) if spec.kind == "video" and not spec.refmod and (
            media_mode == "native" or spec.clip_audio) else None
        seconds = min(float(video_max_seconds), float(target_seconds), media.REFERENCE_MAX_SECONDS)
        sound = None
        if spec.clip_audio:
            if components is not None:
                sound = getattr(components, "audio", None)
                if sound is not None and media_mode == "native":
                    sound = await _audio_copy(asset, sound, seconds=seconds, rate=audio_sample_rate,
                                              byte_limit=byte_limit - used, cancelled=cancelled)
                    sound = replace(sound, component="sound")
            else:
                sound = await _file_sound(asset, seconds=seconds, rate=audio_sample_rate,
                                         byte_limit=byte_limit - used, cancelled=cancelled,
                                         describe_only=media_mode == "descriptions")
            if sound is None:
                spec = replace(spec, clip_audio=False)
                notes.append(spec.slot + ": sound was enabled, but this clip has no audio stream; no Audio label was assigned.")
        asset = replace(asset, spec=spec)
        resolved.append(asset)
        if media_mode == "descriptions":
            continue
        if sound is not None:
            audios.append(sound)
            used += 4 * ((len(sound.wav) + 2) // 3)
        if spec.role == "Video":
            if spec.kind == "image" or spec.refmod and getattr(value, "shape", None) is not None:
                sampled, source_seconds, window = await _batch_frames(asset, value, seconds=seconds,
                    interval=video_sample_seconds, maximum=video_max_frames, fps=batch_video_fps, cancelled=cancelled)
                source_frames = int(value.shape[0])
            elif components is not None:
                fps = float(components.frame_rate)
                if not math.isfinite(fps) or fps <= 0:
                    raise ContractError("VIDEO requires a positive frame rate.")
                sampled, source_seconds, window = await _batch_frames(asset, components.images, seconds=seconds,
                    interval=video_sample_seconds, maximum=video_max_frames, fps=fps, cancelled=cancelled)
                source_frames = int(components.images.shape[0])
            else:
                sampled, source_seconds, window = await _file_frames(value, seconds=seconds,
                    interval=video_sample_seconds, maximum=video_max_frames, max_side=max_side,
                    byte_limit=byte_limit - used, cancelled=cancelled)
                source_frames = None
            if len(images) + len(sampled) > MAX_IMAGES:
                raise ContractError("Media exceed the 64-image transport limit. Select fewer frames or references.")
            for index, (batch, at, source_at) in enumerate(sampled):
                _checkpoint(cancelled)
                one = batch if isinstance(batch, PreparedImage) else prepare_images((
                    ImageReference(ReferenceSpec(spec.slot, "Picture", "image"), batch),),
                    max_side=max_side, byte_limit=byte_limit - used)[0]
                images.append(PreparedVideoFrame(spec.slot, index + 1, len(sampled), one.source_width,
                    one.source_height, one.width, one.height, one.jpeg, at, source_at, window))
                used += 4 * ((len(one.jpeg) + 2) // 3)
                await asyncio.sleep(0)
            videos.append({"slot": spec.slot, "transport": "timestamped_jpeg_frames",
                           "source_frames": source_frames, "selected_frames": len(sampled),
                           "source_seconds": round(source_seconds, 6), "window_seconds": round(window, 6),
                           "requested_interval_seconds": video_sample_seconds, "max_frames": video_max_frames,
                           "sound_enabled": spec.clip_audio, "refmod_stack": bool(spec.refmod and asset.handed.stack)})
        elif spec.kind == "image":
            _shape(value)
            count = int(value.shape[0])
            if len(images) + count > MAX_IMAGES:
                raise ContractError("Media exceed the 64-image transport limit. Select fewer frames or references.")
            for index in range(count):
                _checkpoint(cancelled)
                one = prepare_images((ImageReference(spec, value[index:index + 1]),),
                                     max_side=max_side, byte_limit=byte_limit - used)[0]
                images.append(replace(one, frame=index + 1, frames=count))
                used += 4 * ((len(one.jpeg) + 2) // 3)
                await asyncio.sleep(0)
        else:
            audio = await _audio_copy(asset, value, seconds=audio_max_seconds, rate=audio_sample_rate,
                                      byte_limit=byte_limit - used, cancelled=cancelled)
            audios.append(audio)
            used += 4 * ((len(audio.wav) + 2) // 3)
        if len(images) > MAX_IMAGES:
            raise ContractError("Media exceed the 64-image transport limit. Select fewer frames or references.")
    _checkpoint(cancelled)
    return tuple(resolved), Transport(tuple(images), tuple(audios), tuple(notes), tuple(videos))


async def prepare_references_async(assets, **options):
    """Keep codec exceptions and local source names out of public error messages."""
    from .api_client import ApiError

    try:
        return await _prepare_references_async(assets, **options)
    except (ContractError, ApiError):
        raise
    except Exception:
        pass
    raise ContractError("The connected media could not be decoded. Check its format, dimensions and active trim.")
