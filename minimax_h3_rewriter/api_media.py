"""In-memory image transport. Original references never become URLs or paths."""

from __future__ import annotations

import asyncio
import base64
import io
import re
from dataclasses import dataclass, field

from . import api_config
from .api_contract import ContractError, ReferenceSpec, positive_int

MAX_REFERENCES = 12
MAX_IMAGES = 64
MAX_SOURCE_PIXELS = 100_000_000
SLOT = re.compile(r"ref_(?:[0-9]|1[01])\Z")


@dataclass(frozen=True)
class ImageReference:
    spec: ReferenceSpec
    value: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class PreparedImage:
    slot: str
    frame: int
    frames: int
    source_width: int
    source_height: int
    width: int
    height: int
    jpeg: bytes = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.slot, str) or SLOT.fullmatch(self.slot) is None:
            raise ContractError("Invalid image slot.")
        if any(not positive_int(value) for value in (
            self.frame, self.frames, self.source_width, self.source_height, self.width, self.height
        )) or not self.frame <= self.frames <= MAX_IMAGES:
            raise ContractError("Invalid image dimensions or batch position.")
        if not isinstance(self.jpeg, bytes) or not self.jpeg.startswith(b"\xff\xd8\xff"):
            raise ContractError("The image transport requires encoded JPEG bytes.")

    def content(self) -> dict:
        return {"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode("ascii"),
        }}

    def metadata(self) -> dict:
        return {"slot": self.slot, "frame": self.frame, "frames": self.frames,
                "source_width": self.source_width, "source_height": self.source_height,
                "width": self.width, "height": self.height, "format": "jpeg",
                "bytes": len(self.jpeg), "resized": (self.width, self.height) !=
                (self.source_width, self.source_height)}


@dataclass(frozen=True)
class PreparedVideoFrame(PreparedImage):
    """One sampled frame; all frames retain a single Video label."""

    timestamp_seconds: float = 0.0
    source_timestamp_seconds: float = 0.0
    window_seconds: float = 0.0

    def __post_init__(self):
        super().__post_init__()
        from .api_contract import finite_number

        if any(not finite_number(value) or value < 0 for value in (
            self.timestamp_seconds, self.source_timestamp_seconds, self.window_seconds
        )):
            raise ContractError("Invalid video frame timestamps.")

    def metadata(self) -> dict:
        return dict(super().metadata(), kind="video_frame",
                    timestamp_seconds=round(self.timestamp_seconds, 6),
                    source_timestamp_seconds=round(self.source_timestamp_seconds, 6),
                    window_seconds=round(self.window_seconds, 6))


def _mapping(raw: str, label: str) -> dict:
    parsed = None
    if isinstance(raw, str):
        try:
            parsed = api_config.parse_json(raw or "{}")
        except (ValueError, RecursionError, OverflowError):
            pass
    if not isinstance(parsed, dict):
        raise ContractError("Invalid " + label + " JSON object.")
    return parsed


def arrange_images(supplied: dict, raw_layout: str, raw_instructions: str) -> tuple[ImageReference, ...]:
    """The strip's ordering and flags, restricted to this stage's image roles."""
    if not isinstance(supplied, dict) or any(not isinstance(key, str) or SLOT.fullmatch(key) is None
                                              for key in supplied):
        raise ContractError("Invalid image reference inputs.")
    layout, instructions = _mapping(raw_layout, "reference layout"), _mapping(raw_instructions, "reference instructions")
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
        raise ContractError("Invalid image reference roles.")
    connected = {key: value for key, value in supplied.items() if value is not None}
    order = list(layout.get("order", []))
    order += sorted(set(connected) - set(order), key=lambda name: int(name[4:]))
    found = []
    for name in order:
        if name not in connected or name in layout.get("off", []):
            continue
        role = roles.get(name, "Picture")
        if role == "Video" or name in layout.get("sound", []):
            raise ContractError("Video roles and sound transport are not available yet. Use Picture or Subject for images.")
        value = connected[name]
        shape = getattr(value, "shape", None)
        if shape is None or len(shape) != 4 or shape[-1] not in (1, 3, 4) or any(
            not positive_int(int(one)) for one in shape
        ):
            raise ContractError("Each enabled reference must be an IMAGE batch. Audio, video and RefMod transport are not available yet.")
        instruction = instructions.get(name, "")
        if isinstance(instruction, dict) and set(instruction) <= {"text", "add"}:
            if type(instruction.get("add", True)) is not bool:
                raise ContractError("Invalid reference instruction mode.")
            instruction = instruction.get("text", "")
        if not isinstance(instruction, str):
            raise ContractError("Reference instructions must be text.")
        spec = ReferenceSpec(name, role, "image", instruction=instruction)
        found.append(ImageReference(spec, value))
    return tuple(found)


def _image_copies(references: tuple[ImageReference, ...], *, max_side: int = 1024,
                  byte_limit: int = 1 << 20, cancelled=None):
    """All frames of every enabled batch, without cropping or disk copies."""
    if type(max_side) is not int or not 64 <= max_side <= 4096 or not positive_int(byte_limit):
        raise ContractError("Invalid image transport limits.")
    counts = [int(one.value.shape[0]) for one in references]
    if sum(counts) > MAX_IMAGES:
        raise ContractError("Image batches exceed the 64-image transport limit. Select fewer frames before the API writer.")
    from PIL import Image
    import numpy as np

    from .api_client import _cancelled

    used = 0
    for reference in references:
        batch = reference.value
        frames, height, width, _channels = map(int, batch.shape)
        if width * height > MAX_SOURCE_PIXELS:
            raise ContractError("A reference image exceeds the source pixel limit. Resize it before the API writer.")
        for index in range(frames):
            _cancelled(cancelled)
            frame = batch[index]
            array = frame.detach().cpu().numpy() if hasattr(frame, "detach") else np.asarray(frame)
            if array.dtype.kind not in "fiu" or not np.isfinite(array).all() or array.min() < 0 or array.max() > 1:
                raise ContractError("IMAGE values must be finite numbers between zero and one.")
            pixels = np.floor(array * 255.0 + 0.5).astype(np.uint8)
            image = Image.fromarray(pixels[..., 0] if pixels.shape[-1] == 1 else pixels)
            if image.mode == "RGBA":
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            data = buffer.getvalue()
            used += 4 * ((len(data) + 2) // 3)
            if used > byte_limit:
                raise ContractError("Image copies exceed the profile's request byte limit. Reduce image_max_side or raise request_bytes in the local profile.")
            yield PreparedImage(reference.spec.slot, index + 1, frames, width, height,
                                image.width, image.height, data)
    _cancelled(cancelled)


def prepare_images(references: tuple[ImageReference, ...], **options) -> tuple[PreparedImage, ...]:
    return tuple(_image_copies(references, **options))


async def prepare_images_async(references: tuple[ImageReference, ...], **options) -> tuple[PreparedImage, ...]:
    output = []
    for image in _image_copies(references, **options):
        output.append(image)
        await asyncio.sleep(0)
    return tuple(output)
