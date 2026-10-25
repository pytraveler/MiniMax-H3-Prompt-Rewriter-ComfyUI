"""Saved references -- RefMods -- read for the writers and for the generator.

A RefMod is a reference that was encoded once and kept: a ``.safetensors`` file
in ``models/refmods`` holding the H3 VAE's latent of a picture, a clip, a stack
of photos or a voice, with a JSON header saying which. The format is
ComfyUI-MiniMaxH3Mod's. The files shared on Hugging Face, that pack's
single-file bundles and the ones its forks write are all read here; nothing
here writes one.

Two things make a RefMod unlike every other reference in the pack:

- **The generator is given the latent, not pixels.** MiniMaxH3ReferenceToVideo
  encodes what it is handed, and a RefMod is already encoded -- that is the
  point of one. So it goes to the DiT as a reference block of its own, and only
  'MiniMax-H3 Reference Encode' puts it there.
- **H3's text encoder still has to be shown it.** Qwen reads pixels. Some files
  carry the pictures it is shown; every other one is decoded with the video VAE
  once, and the pictures stay on the reference, for the writer that describes
  it and the generator that presents it alike.

The writers read a RefMod as what it decodes to -- a picture, a clip or a sound
-- and hand on the RefMod itself, so the number the prompt gives it is the place
it takes in front of the generator.

Everything that reads a file is here, and none of it needs ComfyUI: the header
is parsed by hand, so listing a folder of RefMods never imports torch. Loading
and decoding do, and import it when they run.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import struct
from dataclasses import dataclass, field

from . import paths

log = logging.getLogger(__name__)

TYPE = "H3_REWRITER_REFMOD"

FOLDER = "refmods"

NONE_FOUND = "(no RefMods in models/refmods)"

VISUAL_KINDS = ("image", "video")
AUDIO_KIND = "audio"

HEADER_LIMIT = 32 << 20
BUNDLE_LIMIT = 256

SKIP_DIRS = {"graph_presets", "__pycache__"}

PAIR_SUFFIX = {"_visual": "visual", "_Video": "visual", "_audio": "audio", "_Audio": "audio"}

PREVIEWS = (".png", ".jpg", ".jpeg", ".webp")

VIDEO_CHANNELS = 24
AUDIO_CHANNELS = 32

DECODED_FPS = 24.0
ENCODER_FPS = 2
STACK_STEP = 4
BLUR = 8

TEXT_LIMIT = 300
NAME_LIMIT = 40

AUDIO_LATENTS_PER_SECOND = 40

CONCEPTS = {
    "identity": "the identity of a person or character",
    "pose_motion": "a pose or a motion",
    "clothing": "an outfit",
    "background": "a place",
    "style": "a visual style",
    "voice": "a voice",
    "singing": "a singing voice",
    "music_style": "a style of music",
    "sound_fx": "a sound effect",
    "ambience": "an ambience",
}

SOUND_WORDS = {
    "voice": "voice",
    "singing": "singing voice",
    "music_style": "music",
    "sound_fx": "sound effect",
    "ambience": "ambience",
}
VOICES = ("voice", "singing voice")


def read_header(path: str) -> tuple[dict | None, dict]:
    """``(RefMod metadata, tensor table)`` of one ``.safetensors``, tensors unread.

    The metadata is JSON inside the file's own header, under ``refmod_meta`` --
    or ``audio_refmod_meta`` in some voice files. A file that is not a RefMod,
    or is not a safetensors file at all, is ``(None, {})`` rather than an error:
    a models folder holds whatever people put in it.
    """
    try:
        with open(path, "rb") as handle:
            size = struct.unpack("<Q", handle.read(8))[0]
            if size > HEADER_LIMIT:
                return None, {}
            header = json.loads(handle.read(size))
    except (OSError, ValueError, struct.error):
        return None, {}
    if not isinstance(header, dict):
        return None, {}
    raw = header.pop("__metadata__", None) or {}
    meta = None
    if isinstance(raw, dict):
        for key in ("refmod_meta", "audio_refmod_meta"):
            if key in raw:
                try:
                    meta = json.loads(raw[key])
                except (TypeError, ValueError):
                    meta = None
                break
    if meta is None:
        meta = _sidecar(path)
    return (meta if isinstance(meta, dict) else None), header


def _sidecar(path: str) -> dict | None:
    """The metadata an older file keeps beside itself as ``<name>.json``."""
    try:
        with open(path[: -len(".safetensors")] + ".json", encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _shape(tensors: dict, key: str) -> list[int] | None:
    entry = tensors.get(key)
    shape = entry.get("shape") if isinstance(entry, dict) else None
    if not isinstance(shape, list) or not all(isinstance(n, int) for n in shape):
        return None
    return shape


def tokens_of(kind: str, shape: list[int]) -> int:
    """What one reference costs the DiT, in tokens, at every sampling step.

    A visual latent is patched two by two, rounded up the way the model rounds
    its own grid; a voice is two rows per latent frame, one per stereo channel.
    """
    if kind == AUDIO_KIND:
        return 2 * int(shape[-1])
    _batch, _channels, frames, height, width = shape
    return int(frames) * ((int(height) + 1) // 2) * ((int(width) + 1) // 2)


def _fits(kind: str, shape: list[int] | None) -> bool:
    """Whether a latent has the layout its kind says: ``[1,24,T,H,W]`` or ``[1,32,2,T]``."""
    if shape is None:
        return False
    if kind == AUDIO_KIND:
        return len(shape) == 4 and shape[1] == AUDIO_CHANNELS and shape[2] == 2 and shape[3] > 0
    if len(shape) != 5 or shape[1] != VIDEO_CHANNELS or min(shape[2:]) <= 0:
        return False
    return kind == "video" or shape[2] == 1


@dataclass(frozen=True)
class Part:
    """One reference inside a listed entry: which file, which bundle member, what it is.

    ``stored`` says the file carries the pictures H3's text encoder is shown,
    so no VAE is needed to show it them.
    """

    path: str
    member: int | None
    kind: str
    tokens: int
    stored: bool = False


@dataclass(frozen=True)
class Item:
    """One entry of the dropdown: a look, a voice, or both.

    Both is the ordinary case. A bundle carries a look and a voice in one file,
    and a pair is two files beside each other, ``hero_visual`` and
    ``hero_audio``; either is one entry with two outputs.
    """

    name: str
    visual: Part | None = None
    audio: Part | None = None

    def parts(self) -> list[tuple[str, Part]]:
        return [(channel, part) for channel, part in (("visual", self.visual), ("audio", self.audio)) if part]


def split_pair(stem: str) -> tuple[str, str | None]:
    """``('hero', 'visual')`` for ``hero_visual``; ``(stem, None)`` for anything else."""
    for suffix, channel in PAIR_SUFFIX.items():
        if stem.endswith(suffix) and len(stem) > len(suffix):
            return stem[: -len(suffix)], channel
    return stem, None


def _channel(kind: str) -> str:
    return "audio" if kind == AUDIO_KIND else "visual"


def parts_of(path: str, meta: dict, tensors: dict) -> dict[str, Part]:
    """The look and the voice one file holds, by channel.

    A bundle is ComfyUI-MiniMaxH3Mod's single file of several references, its
    members' metadata in order and member ``i`` in tensor ``ref_i``. The first
    look and the first voice in it are what an entry offers; a bundle holding
    more is rare, and the rest are left where they are.
    """
    found: dict[str, Part] = {}
    if meta.get("kind") == "bundle":
        members = meta.get("members")
        if not isinstance(members, list):
            return found
        for index, member in enumerate(members[:BUNDLE_LIMIT]):
            if not isinstance(member, dict):
                continue
            kind = member.get("kind")
            shape = _shape(tensors, f"ref_{index}")
            if kind not in VISUAL_KINDS + (AUDIO_KIND,) or not _fits(kind, shape):
                continue
            found.setdefault(_channel(kind), Part(path, index, kind, tokens_of(kind, shape)))
        return found

    kind = meta.get("kind")
    shape = _shape(tensors, "latent")
    if kind not in VISUAL_KINDS + (AUDIO_KIND,) or not _fits(kind, shape):
        return found
    stored = (
        kind in VISUAL_KINDS
        and bool(meta.get("enc_times"))
        and all(f"enc_{index}" in tensors for index in range(len(meta.get("enc_times") or [])))
    )
    found[_channel(kind)] = Part(path, None, kind, tokens_of(kind, shape), stored)
    return found


_PARTS: dict[str, tuple[tuple, dict[str, Part], bool]] = {}


def _file_parts(path: str, stamp: tuple) -> tuple[dict[str, Part], bool]:
    """``parts_of`` for one file, read again only when the file has changed."""
    held = _PARTS.get(path)
    if held is not None and held[0] == stamp:
        return held[1], held[2]
    meta, tensors = read_header(path)
    found = parts_of(path, meta, tensors) if meta else {}
    bundle = bool(meta) and meta.get("kind") == "bundle"
    _PARTS[path] = (stamp, found, bundle)
    return found, bundle


def scan(directories) -> list[Item]:
    """Every RefMod under these folders, a pair's two files as one entry.

    Folders are searched in order and the first one to hold a name keeps it,
    the rule ComfyUI's own model folders follow. Subfolders become part of the
    name -- ``characters/hero`` -- so two RefMods called the same in different
    folders stay two entries. A file whose name another entry already took is
    listed under its own file name instead, never dropped.
    """
    groups: dict[str, dict[str, Part]] = {}
    taken: set[str] = set()
    for root in directories:
        if not root or not os.path.isdir(root):
            continue
        for folder, subfolders, names in os.walk(root):
            subfolders[:] = sorted(
                name for name in subfolders if name not in SKIP_DIRS and not name.startswith(".")
            )
            rel_dir = os.path.relpath(folder, root).replace("\\", "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            for file in sorted(names):
                if not file.endswith(".safetensors"):
                    continue
                stem = file[: -len(".safetensors")]
                own = f"{rel_dir}/{stem}" if rel_dir else stem
                if own.casefold() in taken:
                    continue
                taken.add(own.casefold())
                path = os.path.join(folder, file)
                try:
                    status = os.stat(path)
                except OSError:
                    continue
                found, bundle = _file_parts(path, (status.st_size, status.st_mtime_ns))
                if not found:
                    continue

                base, channel = split_pair(stem)
                paired = (
                    not bundle and channel is not None and list(found) == [channel]
                )
                name = (f"{rel_dir}/{base}" if rel_dir else base) if paired else own
                held = groups.get(name)
                if held is None:
                    groups[name] = dict(found)
                elif paired and not set(found) & set(held):
                    held.update(found)
                elif own not in groups:
                    groups[own] = dict(found)
                else:
                    log.info("[minimax_h3_rewriter.refmods] '%s' is listed already, skipping %s", own, path)
    return [
        Item(name, groups[name].get("visual"), groups[name].get("audio"))
        for name in sorted(groups, key=str.casefold)
    ]


def roots() -> list[str]:
    """Every folder ComfyUI knows as ``refmods``, ``models/refmods`` among them.

    Registered under that name the way the other RefMod packs register it, so
    an ``extra_model_paths.yaml`` line reaches all of them and every pack
    installed lists the same files.
    """
    try:
        import folder_paths
    except ImportError:
        return []
    default = os.path.join(folder_paths.models_dir, FOLDER)
    try:
        registered = list(folder_paths.get_folder_paths(FOLDER))
    except KeyError:
        registered = []
    if not registered:
        try:
            folder_paths.add_model_folder_path(FOLDER, default)
        except Exception:
            log.debug("[minimax_h3_rewriter.refmods] could not register %s", FOLDER)
    found: list[str] = []
    for path in registered + [default]:
        if not any(paths.same_path(path, one) for one in found):
            found.append(path)
    return found


def listing() -> list[Item]:
    return scan(roots())


def choices() -> list[str]:
    names = [item.name for item in listing()]
    return names or [NONE_FOUND]


def find(name: str) -> Item | None:
    """The entry a dropdown value names, or None.

    A name is only ever looked up in the listing, never joined onto a folder:
    a value that arrived through the API cannot reach a file the listing did
    not find inside a RefMod folder.
    """
    return next((item for item in listing() if item.name == name), None)


def stamp(name: str) -> str:
    """What changes when the files behind an entry do, for ComfyUI's cache."""
    item = find(name)
    if item is None:
        return f"missing:{name}"
    marks = []
    for _channel_name, part in item.parts():
        try:
            status = os.stat(part.path)
            marks.append([part.path, part.member, status.st_size, status.st_mtime_ns])
        except OSError:
            marks.append([part.path, part.member, None, None])
    return json.dumps(marks)


def preview_file(item: Item) -> str:
    """A picture saved beside the entry's files, or ``""``.

    The libraries that make RefMods keep one as ``<name>.png`` or similar, named
    after the pair's base or after one of its files.
    """
    stems = []
    for _channel_name, part in item.parts():
        stem = part.path[: -len(".safetensors")]
        base, channel = split_pair(os.path.basename(stem))
        if channel:
            stems.append(os.path.join(os.path.dirname(stem), base))
        stems.append(stem)
    for stem in stems:
        for extension in PREVIEWS:
            if os.path.isfile(stem + extension):
                return stem + extension
    return ""


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _stored_picture(part: Part) -> bytes:
    """The first picture a file stores for the text encoder, as the JPEG it is kept as.

    Read straight out of the file by its offsets: the tensor is the JPEG's own
    bytes, so nothing has to be decoded, and torch is not needed to find it.
    """
    with open(part.path, "rb") as handle:
        size = struct.unpack("<Q", handle.read(8))[0]
        header = json.loads(handle.read(size))
        start, end = header["enc_0"]["data_offsets"]
        handle.seek(8 + size + int(start))
        return handle.read(int(end) - int(start))


def preview_bytes(item: Item) -> tuple[bytes, str] | None:
    """``(image, media type)`` to draw an entry's square with, or None for none."""
    found = preview_file(item)
    if found:
        with open(found, "rb") as handle:
            return handle.read(), IMAGE_TYPES[os.path.splitext(found)[1].lower()]
    if item.visual and item.visual.stored:
        return _stored_picture(item.visual), "image/jpeg"
    return None


def entry_record(item: Item) -> dict:
    """What the strip in the browser is told about one entry."""
    record = {"name": item.name}
    if item.visual:
        record["visual"] = {
            "kind": item.visual.kind, "tokens": item.visual.tokens, "stored": item.visual.stored,
        }
    if item.audio:
        record["audio"] = {"kind": AUDIO_KIND, "tokens": item.audio.tokens}
    record["preview"] = bool(item.visual and (item.visual.stored or preview_file(item)))
    return record


def _text(value, limit: int = TEXT_LIMIT) -> str:
    """Saved wording as one line, no closing full stop, within ``limit``."""
    text = " ".join(str(value or "").split()).rstrip(" .")
    return text[:limit].rstrip()


@dataclass(eq=False)
class RefMod:
    """One saved reference, loaded: the latent, and what the file says about it.

    ``pictures`` are what H3's text encoder is shown and what a writer reads,
    ``times`` their timestamps: stored in the file, or decoded from the latent,
    which ``decoded`` says -- a clip's two a second either way, a stack's one
    per photo. ``sound`` is a voice decoded with the audio VAE. ``view`` is the
    ordinary ComfyUI value a writer reads in its place -- an IMAGE, a VIDEO or
    an AUDIO -- or None when there is nothing to read yet.
    """

    name: str
    kind: str
    latent: object
    strength: float = 1.0
    source: str = ""
    subject_name: str = ""
    appearance: str = ""
    voice: str = ""
    concept: str = ""
    pictures: object = None
    decoded: bool = False
    times: list = field(default_factory=list)
    sound: object = None
    view: object = None

    @property
    def channel(self) -> str:
        return _channel(self.kind)

    @property
    def latent_shape(self) -> list[int]:
        return [int(n) for n in self.latent.shape]

    @property
    def tokens(self) -> int:
        return tokens_of(self.kind, self.latent_shape)

    @property
    def stack(self) -> bool:
        return self.kind == "video" and self.source == "stack"


def is_refmod(value) -> bool:
    return isinstance(value, RefMod)


def seen(value):
    """What a writer reads in place of a RefMod; anything else as it is."""
    return value.view if is_refmod(value) else value


def _unpack(tensors) -> object:
    """Stored JPEG pictures back into one IMAGE batch, ``[N, H, W, 3]`` from 0 to 1."""
    import numpy
    import torch
    from PIL import Image

    frames = []
    for data in tensors:
        with Image.open(io.BytesIO(data.numpy().tobytes())) as picture:
            frames.append(numpy.asarray(picture.convert("RGB"), dtype=numpy.float32) / 255.0)
    if len({frame.shape for frame in frames}) != 1:
        raise ValueError("the pictures stored in this RefMod are not all one size")
    return torch.from_numpy(numpy.stack(frames))


def load_part(name: str, part: Part, strength: float = 1.0) -> RefMod:
    """Read one reference of an entry into memory, latent and stored pictures."""
    from safetensors import safe_open

    meta, _tensors = read_header(part.path)
    if not meta:
        raise ValueError(f"'{name}' has no RefMod header any more -- the file changed.")
    if part.member is not None:
        members = meta.get("members") or []
        meta = members[part.member] if part.member < len(members) else {}
    key = "latent" if part.member is None else f"ref_{part.member}"

    with safe_open(part.path, framework="pt", device="cpu") as handle:
        latent = handle.get_tensor(key).float()
        times = [float(t) for t in (meta.get("enc_times") or [])] if part.stored else []
        pictures = _unpack([handle.get_tensor(f"enc_{index}") for index in range(len(times))]) if times else None

    if not _fits(part.kind, [int(n) for n in latent.shape]):
        raise ValueError(
            f"'{name}' holds a latent of shape {tuple(latent.shape)}, which is not an H3 "
            f"{part.kind} reference."
        )
    return RefMod(
        name=name,
        kind=part.kind,
        latent=latent,
        strength=max(0.0, min(1.0, float(strength))),
        source=str(meta.get("source") or ""),
        subject_name=_text(meta.get("subject_name"), NAME_LIMIT),
        appearance=_text(meta.get("appearance")),
        voice=_text(meta.get("voice_description")),
        concept=str(meta.get("concept_type") or ""),
        pictures=pictures,
        times=times,
    )


def load(item: Item, strength: float = 1.0) -> dict[str, RefMod]:
    """Every reference of an entry, by channel."""
    return {channel: load_part(item.name, part, strength) for channel, part in item.parts()}


def _is_h3_vae(vae, name: str) -> bool:
    return type(getattr(vae, "first_stage_model", None)).__name__ == name


def two_a_second(count: int, rate: float = DECODED_FPS) -> tuple[list[int], list[float]]:
    """``(frames, timestamps)`` a clip of ``count`` frames at ``rate`` is shown at.

    Two a second, from the first frame, timed by the clock rather than by
    stepping a fixed count -- the native sampling of a reference clip.
    """
    times = [index / ENCODER_FPS for index in range(max(1, math.ceil(count * ENCODER_FPS / rate)))]
    return [min(round(t * rate), count - 1) for t in times], times


def _decoded(vae, latent, name: str):
    """``vae.decode`` as ``[N, H, W, 3]`` from 0 to 1 on the CPU, whatever it returned."""
    pixels = vae.decode(latent)
    if pixels.ndim == 5:
        pixels = pixels.reshape(-1, *pixels.shape[-3:])
    if pixels.ndim != 4 or pixels.shape[-1] < 3 or pixels.shape[0] < 1:
        raise ValueError(f"the video VAE decoded '{name}' to {tuple(pixels.shape)}, not frames")
    return pixels[..., :3].detach().float().clamp(0.0, 1.0).cpu()


def decode_pictures(mod: RefMod, vae) -> None:
    """Decode a look into the pictures it shows, once, and keep them on it.

    A stack of photos is decoded one latent frame at a time. Each photo was
    encoded on its own and the frames laid side by side in time, so each frame
    is a picture by itself; decoded together as one clip, the VAE's temporal
    decoder would run across photos that were never one shot. A clip is
    decoded whole, as one, and only the two frames a second the encoder takes
    of it are kept.
    """
    if mod.pictures is not None or mod.kind not in VISUAL_KINDS:
        return
    if not _is_h3_vae(vae, "MiniMaxH3VideoVAE"):
        raise ValueError(
            f"'{mod.name}' has to be decoded with the MiniMax-H3 video VAE, and the VAE "
            f"connected is another one."
        )
    import torch

    frames = int(mod.latent.shape[2])
    if mod.kind == "image":
        mod.pictures, mod.times = _decoded(vae, mod.latent, mod.name)[:1], [0.0]
    elif mod.stack:
        mod.pictures = torch.cat(
            [_decoded(vae, mod.latent[:, :, index:index + 1], mod.name)[:1] for index in range(frames)]
        )
        mod.times = [index / ENCODER_FPS for index in range(frames)]
    else:
        pixels = _decoded(vae, mod.latent, mod.name)
        picked, mod.times = two_a_second(int(pixels.shape[0]))
        mod.pictures = pixels[picked].clone()
        del pixels
    mod.decoded = True


def decode_sound(mod: RefMod, audio_vae) -> None:
    """Decode a voice into an AUDIO a writer can listen to, once, and keep it."""
    if mod.sound is not None or mod.kind != AUDIO_KIND:
        return
    if not _is_h3_vae(audio_vae, "MiniMaxH3AudioVAE"):
        raise ValueError(
            f"'{mod.name}' has to be decoded with the MiniMax-H3 audio VAE, and the VAE "
            f"connected is another one."
        )
    from comfy_extras.nodes_audio import vae_decode_audio

    decoded = vae_decode_audio(audio_vae, {"samples": mod.latent})
    rate = getattr(audio_vae.first_stage_model, "output_sample_rate", 0) or decoded["sample_rate"]
    mod.sound = {"waveform": decoded["waveform"].detach().float().cpu(), "sample_rate": int(rate)}


def _low_pass(latent):
    """A heavily blurred copy of a latent: pooled down eightfold, then back up.

    Mixing toward this weakens a reference and keeps it a reference -- colour,
    layout, the broad shape -- where mixing toward noise reads as damage.
    """
    import torch.nn.functional as functional

    if latent.dim() == 4:
        batch, channels, sides, frames = latent.shape
        if frames <= 1:
            return latent
        flat = latent.reshape(batch * channels * sides, 1, frames)
        down = functional.adaptive_avg_pool1d(flat, max(1, frames // BLUR))
        return functional.interpolate(down, size=frames, mode="linear", align_corners=False).reshape(
            latent.shape
        )
    frames, height, width = latent.shape[2:]
    down = functional.adaptive_avg_pool3d(latent, (frames, max(1, height // BLUR), max(1, width // BLUR)))
    return functional.interpolate(down, size=(frames, height, width), mode="trilinear", align_corners=False)


def soften_latent(latent, strength: float):
    if strength >= 1.0:
        return latent
    return strength * latent + (1.0 - strength) * _low_pass(latent)


def soften_pictures(pictures, strength: float, latent_h: int, latent_w: int):
    """The pictures weakened as the latent is, the blur's cells measured in pixels."""
    if strength >= 1.0:
        return pictures
    import torch.nn.functional as functional

    images = pictures.movedim(-1, 1).float()
    down = functional.adaptive_avg_pool2d(images, (max(1, latent_h // BLUR), max(1, latent_w // BLUR)))
    up = functional.interpolate(down, size=images.shape[-2:], mode="bilinear", align_corners=False)
    return (strength * images + (1.0 - strength) * up).movedim(1, -1)


def block(mod: RefMod) -> dict:
    """The reference block the DiT reads, in the shape MiniMaxH3ReferenceToVideo builds."""
    latent = soften_latent(mod.latent, mod.strength)
    if mod.kind == AUDIO_KIND:
        return {"kind": AUDIO_KIND, "ref_audio_t": int(latent.shape[-1]), "audio_latent": latent}
    _batch, _channels, frames, height, width = mod.latent_shape
    found = {"kind": mod.kind, "latent_h": height, "latent_w": width, "latent": latent}
    if mod.kind == "video":
        found.update(latent_t=frames, ref_audio_t=0, audio_latent=None)
    return found


def encoder_frames(mod: RefMod) -> tuple[object, list[float]]:
    """The pictures H3's text encoder is shown of a look, with their timestamps.

    A clip's pictures are the two a second it takes already, decoded or
    stored. A stack's are one per photo, and every fourth is shown, two to a
    block: the default of the pack that stores them, and close to what two a
    second of the same latent decoded as one clip would show -- a latent frame
    is about four frames of video.
    """
    pictures = mod.pictures
    count = int(pictures.shape[0])
    if mod.kind == "image":
        return pictures[:1], [0.0]
    if mod.stack:
        picked = list(range(0, count, STACK_STEP))
        return pictures[picked], [index / ENCODER_FPS for index in range(len(picked))]
    times = mod.times if len(mod.times) == count else [index / ENCODER_FPS for index in range(count)]
    return pictures, list(times)


def encoder_item(mod: RefMod) -> dict:
    """What MiniMaxH3Tokenizer is given for one RefMod, as for any reference."""
    if mod.kind == AUDIO_KIND:
        return {"type": AUDIO_KIND}
    frames, times = encoder_frames(mod)
    _batch, _channels, _frames, height, width = mod.latent_shape
    frames = soften_pictures(frames, mod.strength, height, width).clone()
    if mod.kind == "image":
        return {"type": "image", "data": frames[:1]}
    return {"type": "video", "data": frames, "timestamps": times}


def kind_words(mod: RefMod) -> str:
    if mod.kind == AUDIO_KIND:
        return "voice"
    if mod.stack:
        return "stack of photos"
    return "picture" if mod.kind == "image" else "clip"


def summary(mod: RefMod) -> str:
    """One line about a loaded RefMod, for the node and the generator's summary."""
    parts = [f"{mod.channel}: {kind_words(mod)}"]
    if mod.kind == AUDIO_KIND:
        parts.append(f"{mod.latent_shape[-1] / AUDIO_LATENTS_PER_SECOND:.1f} s")
    else:
        _batch, _channels, frames, height, width = mod.latent_shape
        parts.append(f"{width * 16}x{height * 16}" + (f", {frames} latent frames" if frames > 1 else ""))
    parts.append(f"{mod.tokens:,} tokens")
    if mod.strength < 1.0:
        parts.append(f"strength {mod.strength:.2f}")
    if mod.subject_name:
        parts.append(f"subject '{mod.subject_name}'")
    line = ", ".join(parts)
    if mod.kind == AUDIO_KIND:
        return line + (" -- decoded, so the writers can hear it" if mod.sound is not None else "")
    if mod.pictures is None:
        return line + " -- no pictures stored and no VAE to decode it with"
    how = "decoded" if mod.decoded else "stored in the file"
    return line + f" -- {int(mod.pictures.shape[0])} picture(s) {how}"


def hint(mod: RefMod) -> str:
    """What the captioner is told about a RefMod, from what the file says of it.

    Added to the role's own question, never put in its place: the saved words
    are somebody's notes about the reference and the pictures are the
    reference, so the description still comes from what is shown.
    """
    said = []
    if mod.stack:
        said.append(
            "These frames come from separate photographs of one subject, stored together "
            "as a clip: describe the subject they share, not a sequence of events."
        )
    concept = CONCEPTS.get(mod.concept)
    if concept:
        said.append(f"It was saved as a reference for {concept}.")
    if mod.subject_name:
        if mod.kind == AUDIO_KIND:
            said.append(f"It is the voice of {mod.subject_name}.")
        else:
            said.append(f"The subject is called {mod.subject_name}; use that name.")
    saved = mod.voice if mod.kind == AUDIO_KIND else mod.appearance
    if saved:
        said.append(f"It was saved with this description, to keep to where it agrees: {saved}.")
    return " ".join(said)


def saved_caption(mod: RefMod) -> str:
    """A voice's saved description, as the caption of a voice nothing can hear."""
    if mod.kind != AUDIO_KIND or not mod.voice:
        return ""
    return f"{mod.voice}." if not mod.subject_name else f"The voice of {mod.subject_name}: {mod.voice}."


def halves(found) -> dict[str, str]:
    """The slot holding the other half of each RefMod's entry, where both are on the strip.

    ``found`` is ``(slot, mod)`` in strip order. A look and a voice loaded from
    one entry belong to each other; a second copy of either belongs to neither.
    """
    entries: dict[str, dict[str, str]] = {}
    for slot, mod in found:
        entries.setdefault(mod.name, {}).setdefault(mod.channel, slot)
    paired = {}
    for entry in entries.values():
        if len(entry) == 2:
            paired[entry["visual"]] = entry["audio"]
            paired[entry["audio"]] = entry["visual"]
    return paired


def _sound_word(mod: RefMod) -> str:
    return SOUND_WORDS.get(mod.concept) or ("voice" if mod.voice else "sound")


def writer_note(mod: RefMod, partner: RefMod | None = None, label: str = "") -> str:
    """What the writer is told of a RefMod ahead of its caption, or ``""``.

    The captioner reads a look and a voice apart, so neither caption says the
    two were saved as one entry -- and a voice saved over music is heard as
    music, and borrowed for the score. Both are facts of the file, not of what
    is shown, so they go to the writer as such, the way a clip's sound is told
    whose it is. ``label`` is what the writer calls ``partner``, the entry's
    other half.
    """
    said = []
    if mod.stack:
        said.append("photos of one subject, not footage")
    concept = CONCEPTS.get(mod.concept)
    if concept and SOUND_WORDS.get(mod.concept) in VOICES:
        said.append(f"saved as a reference for {concept}, the voice alone and not what plays behind it")
    elif concept:
        said.append(f"saved as a reference for {concept}")
    if partner is not None and label:
        if mod.kind != AUDIO_KIND:
            said.append(f"{label} is its {_sound_word(partner)}")
        elif _sound_word(mod) in VOICES:
            said.append(f"the {_sound_word(mod)} of the subject in {label}")
        else:
            said.append(f"saved together with {label}")
    return f"({'; '.join(said)}) " if said else ""


def unseen(slot: str, mod: RefMod, saved_ok: bool) -> str:
    """Why a writer cannot read this RefMod, or ``""`` when it can."""
    if mod.view is not None:
        return ""
    if mod.kind == AUDIO_KIND:
        if saved_ok and saved_caption(mod):
            return ""
        return (
            f"'{slot}' is the voice of RefMod '{mod.name}', and it has to be decoded before "
            f"anything can hear it. Connect the MiniMax-H3 audio VAE to that RefMod node's "
            f"'audio_vae'."
        )
    return (
        f"'{slot}' is RefMod '{mod.name}', which carries no pictures of itself, so there is "
        f"nothing to read it from. Connect the MiniMax-H3 video VAE to that RefMod node's 'vae'."
    )
