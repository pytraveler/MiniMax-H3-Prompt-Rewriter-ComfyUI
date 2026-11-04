"""P-H3 image requests and ordered pictures, independent of ComfyUI's node API."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction

from . import aspect, ph3_frames, refmods

TASKS = ("Text2Image", "Image2Image")
MAX_IMAGES = 8
SIZES = {
    8: {"1:1": (2912, 2912), "4:3": (3360, 2496), "3:4": (2496, 3360),
        "3:2": (3552, 2368), "2:3": (2368, 3552), "16:9": (3872, 2176), "9:16": (2176, 3872)},
    4: {"1:1": (2048, 2048), "4:3": (2368, 1760), "3:4": (1760, 2368),
        "3:2": (2496, 1664), "2:3": (1664, 2496), "16:9": (2720, 1536), "9:16": (1536, 2720)},
}
PICTURE = re.compile(r"<Picture\s+(\d+)>", re.IGNORECASE)
OTHER_ASSET = re.compile(r"<(?:Video|Audio)\s+\d+>", re.IGNORECASE)
UNBOUND_IMAGE = re.compile(r"\b(?:reference|source|original|provided|attached)\s+(?:picture|image|photo(?:graph)?|frame)\b", re.IGNORECASE)


def object_of(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def ordered(supplied: dict | None, raw_layout: str) -> list[tuple[str, object]]:
    """Enabled sockets, once each, with newly connected sockets after the saved order."""
    supplied = {key: value for key, value in (supplied or {}).items() if value is not None}
    layout = object_of(raw_layout)
    off = layout.get("off")
    off = {name for name in off if isinstance(name, str)} if isinstance(off, list) else set()
    saved = layout.get("order")
    saved = [name for name in saved if isinstance(name, str)] if isinstance(saved, list) else []

    def number(name):
        found = re.search(r"(\d+)$", name)
        return (int(found.group(1)) if found else -1, name)

    seen = set()
    result = []
    for name in saved + sorted(supplied, key=number):
        if name in supplied and name not in off and name not in seen:
            seen.add(name)
            result.append((name, supplied[name]))
    return result


def selected_frames(count: int, limit: int) -> list[int]:
    """Evenly spaced representatives; a one-picture selection keeps the first."""
    limit = min(count, max(1, int(limit)))
    if limit <= 1:
        return [0] if count else []
    return [round(index * (count - 1) / (limit - 1)) for index in range(limit)]


@dataclass(frozen=True)
class Picture:
    slot: str
    image: object
    instruction: str = ""
    origin: str = ""


def prepare(task: str, supplied=None, layout="", instructions="", vae=None, refmod_images=3,
            video_images=3, frame_selection="", node=None, links=None,
            use_ref_description=False) -> list[Picture]:
    """Expand batches and visual RefMods without handing a video latent to P-H3."""
    if task not in TASKS:
        raise ValueError(f"Unknown P-H3 task {task!r}.")
    describing = task == TASKS[0] and use_ref_description
    if task == TASKS[0] and not describing:
        return []
    entries = ordered(supplied, layout)
    if describing:
        entries = entries[:1]
        if not entries:
            return []
    notes = object_of(instructions)
    try:
        selections = json.loads(frame_selection or "{}")
        if not isinstance(selections, dict):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("P-H3 frame_selection must be a JSON object of socket names and frame indices.") from None
    sources = []
    for slot, value in entries:
        try:
            source = ph3_frames.source_of(value, vae, node, slot)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{slot}: {error}") from error
        if describing:
            automatic = [0]
        elif source.kind == "image" and source.count <= MAX_IMAGES:
            automatic = list(range(source.count))
        else:
            automatic = selected_frames(source.count, refmod_images if source.kind == "refmod" else video_images)
        ph3_frames.remember(node, slot, source, automatic, (links or {}).get(slot))
        sources.append((slot, value, source, automatic))
    ph3_frames.announce(node)
    result = []
    for slot, value, source, automatic in sources:
        note = notes.get(slot, "")
        note = note.get("text", "") if isinstance(note, dict) else note
        note = note.strip() if isinstance(note, str) else ""
        origin = f"RefMod '{value.name}'" if source.kind == "refmod" else "VIDEO" if source.kind == "video" else ""
        indices = selections.get(slot, automatic)
        if (not isinstance(indices, list) or not indices
                or any(type(index) is not int or index < 0 or index >= source.count for index in indices)):
            raise ValueError(f"{slot}: selected frames no longer fit this reference (1..{source.count}). Open frames and select them again.")
        indices = sorted(set(indices))
        if describing:
            indices = indices[:1]
        if len(result) + len(indices) > MAX_IMAGES:
            raise ValueError(
                f"P-H3 supports {MAX_IMAGES} pictures in this writer, including the base image. "
                "Switch references off, select fewer frames, or reduce refmod_images/video_images; no pictures were silently dropped."
            )
        for index, picture in zip(indices, source.read(indices)):
            name = slot if source.count == 1 else f"{slot}[{index + 1}]"
            result.append(Picture(name, picture, note, origin))
    if not result:
        raise ValueError("Image2Image needs at least one enabled image, VIDEO or visual RefMod.")
    return result


def description_request(picture: Picture) -> str:
    """Read the actual source before asking for a different scene."""
    parts = [
        "Describe what is visibly present in the attached image in factual detail. "
        "Identify the subjects and their appearance, clothing, materials, colors, "
        "poses, setting, composition and lighting. Include legible text verbatim. "
        "Describe the image as it is; do not invent a different subject or propose edits. "
        "Return only the visual description in English, with no preamble or reference labels.",
    ]
    if picture.instruction:
        parts.append(f"Focus on these details from the picture: {picture.instruction}")
    return "\n".join(parts)


def inspiration_request(prompt: str, ratio: str, description: str, guidance="") -> str:
    """Turn a visual description and a user request into a text-only prompt."""
    parts = [
        f"Aspect ratio: {ratio}",
        "Request: Write a complete, standalone text-to-image prompt for a NEW image. "
        "Combine the visual description below with the user request. It is descriptive "
        "context, not an image available to the generator. Describe the subjects, appearance, clothing, "
        "setting, composition, colors and lighting needed to draw the new image. "
        "The user request takes priority wherever it differs from the picture. "
        "Return only the finished image description in English. Keep requested visible text "
        "verbatim. State all necessary details explicitly, with no preamble, reference labels, "
        "phrases such as 'the reference image', or instructions to edit, preserve or keep an existing image.",
        f"Visual description: {description.strip()}",
        f"User request: {(prompt or '').strip() or 'Create the scene described above.'}",
    ]
    if guidance:
        parts.append(f"What to take from the visual description: {guidance}")
    return "\n".join(parts)


def preview_items(pictures, supplied=None):
    """Selected frames under the original socket names the reference strip reads."""
    import torch

    grouped = {}
    for picture in pictures:
        slot = picture.slot.partition("[")[0]
        grouped.setdefault(slot, []).append(picture.image)
    for slot, frames in grouped.items():
        yield slot, frames[0] if len(frames) == 1 else torch.cat(frames, dim=0)
    for slot, value in (supplied or {}).items():
        if slot not in grouped:
            yield slot, value.pictures if refmods.is_refmod(value) else None if ph3_frames.is_video(value) else value


def canvas(resolution: str, megapixels: int, pictures=(), match_image_ratio=True,
           aspect_ratio=None) -> tuple[int, int, str]:
    """A canvas and upsampler ratio; an explicit ratio wins over the base image."""
    if megapixels not in SIZES or resolution not in SIZES[megapixels]:
        raise ValueError("Choose a P-H3 aspect ratio and either 4 or 8 megapixels.")
    ratio = aspect.resolve(aspect_ratio, "")
    if ratio:
        if ratio in SIZES[megapixels]:
            return *SIZES[megapixels][ratio], ratio
        width, height = map(float, ratio.split(":"))
    elif pictures and match_image_ratio:
        height, width = map(int, pictures[0].image.shape[1:3])
        fraction = Fraction(width, height).limit_denominator(100)
        ratio = f"{fraction.numerator}:{fraction.denominator}"
    else:
        return *SIZES[megapixels][resolution], resolution
    scale = math.sqrt(megapixels * 1024 * 1024 / (width * height))
    return (max(32, round(width * scale / 32) * 32),
            max(32, round(height * scale / 32) * 32), ratio)


def request(prompt: str, ratio: str, pictures=(), keep="") -> str:
    """The official upsampler input, including one label per presented picture."""
    if not (prompt or "").strip():
        raise ValueError("Write a request for P-H3 before running the writer.")
    parts = [f"Aspect ratio: {ratio}"]
    parts.extend(f"<Picture {index}>" for index in range(1, len(pictures) + 1))
    parts.append(f"Request: {prompt.strip()}")
    for index, picture in enumerate(pictures, 1):
        if picture.instruction:
            parts.append(f"For <Picture {index}>: {picture.instruction}")
    if pictures and (keep or "").strip():
        parts.append(f"Keep unchanged: {keep.strip()}")
    return "\n".join(parts)


def review(text: str, task: str, having=None):
    """Image checks only; the official edit brief is not a video prompt."""
    from .checks import Issue, WARN, _loop

    if not (text or "").strip():
        return [Issue(WARN, "the answer is empty", "empty")]
    issues = _loop(text)
    if OTHER_ASSET.search(text):
        issues.append(Issue(WARN, "P-H3 image prompts cannot reference video or audio assets", "tags"))
    if task == TASKS[0] and UNBOUND_IMAGE.search(text):
        issues.append(Issue(WARN, "the Text2Image prompt refers to an image that is not passed to the generator", "tags"))
    count = None if having is None else sum(kind == "image" for kind in having)
    for number in sorted({int(value) for value in PICTURE.findall(text)}):
        if number < 1 or task == TASKS[0] or (count is not None and number > count):
            issues.append(Issue(WARN, f"<Picture {number}> has no corresponding P-H3 image", "tags"))
    return issues


def generate_from_reference(clip, prompt, ratio, picture, max_length=2048, thinking=True, progress=None):
    """Describe one source, then write with its text and no visual conditioning."""
    if progress:
        progress("Describing the first active reference")
    description, caption_reasoning = generate(clip, description_request(picture), [picture], max_length, thinking)
    if progress:
        progress("Writing the P-H3 image prompt")
    request_text = inspiration_request(prompt, ratio, description, picture.instruction)
    text, reasoning = generate(clip, request_text, [], max_length, thinking)
    if caption_reasoning:
        reasoning = "Reference description thinking:\n" + caption_reasoning + "\n\nPrompt thinking:\n" + reasoning
    return text, reasoning, description


def generate(clip, prompt, pictures, max_length=2048, thinking=True):
    """Keep the checkpoint's own template and native generation implementation."""
    tokenizer = type(getattr(clip, "tokenizer", None)).__name__
    if tokenizer.startswith(("PH3ImageTokenizer", "MiniMaxH3Tokenizer")):
        raise ValueError(
            "upsampler is a MiniMax text encoder, not a prompt writer. Connect "
            "p-h3-image_prompt_upsampler through Load CLIP, type minimax."
        )
    try:
        from comfy_extras.nodes_textgen import TextGenerateV2
    except ImportError as error:
        raise RuntimeError("P-H3 Prompt Writer needs ComfyUI 0.44.0 or newer (Generate Text).") from error
    import math
    import torch.nn.functional as functional

    images = {}
    for index, picture in enumerate(pictures, 1):
        image = picture.image
        height, width = image.shape[1:3]
        if height * width > 1_000_000:
            scale = math.sqrt(1_000_000 / (height * width))
            size = [max(32, int(side * scale / 32) * 32) for side in (height, width)]
            image = functional.interpolate(image.movedim(-1, 1), size=size, mode="bilinear", align_corners=False).movedim(1, -1)
        images[f"image{index}"] = image
    answer = TextGenerateV2.execute(
        clip=clip, prompt=prompt, max_length=max_length,
        sampling_mode={"sampling_mode": "off"}, thinking=thinking,
        use_default_template=True, mtp="auto",
        images=images,
    )
    text, reasoning = answer.result
    if not text.strip():
        raise RuntimeError(
            "The P-H3 upsampler returned no image prompt. It may have used max_length on "
            "thinking alone: increase max_length and run again. Check that upsampler is "
            "the P-H3 prompt upsampler, not P-H3's 4B text encoder."
        )
    return text, reasoning

