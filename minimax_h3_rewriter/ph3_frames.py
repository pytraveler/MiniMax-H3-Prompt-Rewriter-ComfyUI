"""Exact reference frames and a bounded, weak cache for the P-H3 frame picker."""

from __future__ import annotations

import base64
import io
import threading
import uuid
import weakref
from collections import OrderedDict

from . import refmods

EVENT = "minimax_h3_rewriter.ph3_frames"
LIMIT = 64
SOURCES = OrderedDict()
LOCK = threading.RLock()


def is_video(value):
    return callable(getattr(value, "get_components", None))


class FrameSource:
    """Keep metadata and small JPEGs, without pinning the upstream frame tensor."""

    def __init__(self, value, vae=None):
        self.value = weakref.ref(value)
        self.token = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.thumbs = OrderedDict()
        self.times = ()
        self.file = type(value).__name__ == "VideoFromFile"
        self.kind = "refmod" if refmods.is_refmod(value) else "video" if is_video(value) else "image"
        if self.kind == "refmod":
            if value.kind not in refmods.VISUAL_KINDS:
                raise ValueError("P-H3 needs pictures; an audio RefMod cannot be used here.")
            if value.pictures is None:
                if vae is None:
                    raise ValueError(
                        f"RefMod '{value.name}' has no stored pictures. Connect the original "
                        "MiniMax-H3 video VAE to refmod_vae, or decode it on its RefMod loader."
                    )
                refmods.decode_pictures(value, vae)
        if self.file:
            self.times = tuple(time for time, _frame in self._video(value))
            self.count = len(self.times)
        else:
            self.count = int(self._batch(value).shape[0])
        if not self.count:
            raise ValueError("The reference contains no frames.")

    def alive(self):
        value = self.value()
        if value is None:
            raise LookupError("These frames left ComfyUI's cache. Run the input branch again with bypass.")
        return value

    def _batch(self, value):
        batch = value.pictures if self.kind == "refmod" else value.get_components().images if self.kind == "video" else value
        shape = getattr(batch, "shape", ())
        if len(shape) != 4 or shape[-1] not in (3, 4) or min(shape[:3]) <= 0:
            raise ValueError("Connect an IMAGE batch, VIDEO or visual RefMod.")
        return batch

    def _video(self, value):
        """Decode in presentation order; trim by PTS, including variable frame rates."""
        import av

        source = value.get_stream_source()
        start, duration = value.get_active_trim_window()
        if hasattr(source, "seek"):
            source.seek(0)
        with av.open(source) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            rate = float(stream.average_rate or 1)
            for index, frame in enumerate(container.decode(stream)):
                time = float(frame.pts * stream.time_base) if frame.pts is not None else index / rate
                if time < start:
                    continue
                if duration > 0 and time >= start + duration:
                    break
                yield time - start, frame

    def read(self, indices):
        """Return only the requested full-resolution frames, in the requested order."""
        found = dict(self._read(sorted(set(indices))))
        return [found[index] for index in indices]

    def _read(self, indices):
        """Yield frames individually so a contact sheet never holds a full-size batch."""
        import numpy as np
        import torch

        if not indices or any(type(index) is not int or index < 0 or index >= self.count for index in indices):
            raise ValueError(f"Select frame numbers from 1 to {self.count}.")
        with self.lock:
            value = self.alive()
            if not self.file:
                batch = self._batch(value)
                for index in indices:
                    frame = batch[index:index + 1, ..., :3]
                    if self.kind == "refmod":
                        _b, _c, _t, height, width = value.latent_shape
                        frame = refmods.soften_pictures(frame, value.strength, height, width)
                    yield index, frame
                return
            wanted, found = set(indices), set()
            for index, (_time, frame) in enumerate(self._video(value)):
                if index in wanted:
                    pixels = frame.to_ndarray(format="rgb24")
                    rotation = int(round(getattr(frame, "rotation", 0) // 90)) % 4
                    if rotation:
                        pixels = np.rot90(pixels, k=rotation).copy()
                    crop = getattr(value, "_VideoFromFile__crop", None)
                    if crop:
                        from comfy_api.latest._util import normalize_crop_rect
                        rect = normalize_crop_rect(*crop, pixels.shape[1], pixels.shape[0])
                        if rect:
                            x, y, width, height = rect
                            pixels = pixels[y:y + height, x:x + width]
                    yield index, torch.from_numpy(pixels.copy()).float().div_(255).unsqueeze(0)
                    found.add(index)
                    if len(found) == len(wanted):
                        break
            if len(found) != len(wanted):
                raise ValueError("The video changed since it was indexed. Run the input branch again.")

    def thumbnails(self, indices):
        """Bounded, aspect-preserving previews, shared by the filmstrip and large view."""
        import numpy as np
        from PIL import Image

        with self.lock:
            missing = [index for index in indices if index not in self.thumbs]
            if missing:
                for index, frame in self._read(sorted(set(missing))):
                    pixels = frame[0].detach().float().cpu().clamp(0, 1).numpy()
                    picture = Image.fromarray((pixels * 255).round().astype(np.uint8))
                    picture.thumbnail((512, 384), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    picture.save(buffer, format="JPEG", quality=85)
                    self.thumbs[index] = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
            result = [{"index": index, "thumb": self.thumbs[index],
                       "time": round(self.times[index], 4) if self.times else None} for index in indices]
            while len(self.thumbs) > 96:
                self.thumbs.popitem(last=False)
            return result


def source_of(value, vae=None, node=None, slot=None):
    """Reuse an index while the exact upstream object stays cached."""
    key = (str(node), slot)
    with LOCK:
        held = SOURCES.get(key)
        if held and held[0].value() is value:
            SOURCES.move_to_end(key)
            return held[0]
    return FrameSource(value, vae)


def remember(node, slot, source, automatic, link=None):
    if node is None:
        return
    with LOCK:
        key = (str(node), slot)
        SOURCES[key] = (source, list(automatic), link)
        SOURCES.move_to_end(key)
        while len(SOURCES) > LIMIT:
            SOURCES.popitem(last=False)


def metadata(node):
    with LOCK:
        return {slot: {"token": source.token, "count": source.count, "kind": source.kind,
                       "automatic": automatic, "link": link}
                for (owner, slot), (source, automatic, link) in SOURCES.items()
                if owner == str(node) and source.value() is not None}


def thumbnails(node, slot, token, indices):
    with LOCK:
        held = SOURCES.get((str(node), slot))
    if not held or held[0].token != token:
        raise LookupError("This preview has expired. Run the input branch again with bypass.")
    if not 1 <= len(indices) <= 24:
        raise ValueError("Request between 1 and 24 frame previews.")
    return held[0].thumbnails(indices)


def announce(node):
    if node is None:
        return
    try:
        from server import PromptServer
        PromptServer.instance.send_sync(EVENT, {"node": str(node), "slots": metadata(node)})
    except (ImportError, AttributeError):
        pass
