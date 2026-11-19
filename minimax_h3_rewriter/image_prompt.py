"""Image edit order, canvas sizing and native CLIP prompt expansion for H3 Image."""

from __future__ import annotations

import json
import math
import re
from fractions import Fraction

from .api_config import parse_json
from .api_contract import ContractError, ReferenceSpec
from .api_media import ImageReference
from .ph3_prompt import PICTURE, object_of, ordered, vision_copy

MODEL = "MiniMax-H3-Image"
MAX_IMAGES = 16
MAX_PROMPT = 32_000
AREA_SIDES = {"1K": 1024, "2K": 2048, "4K": 4096}
LOCAL = "Local CLIP"
CONTEXT_IR = "MiniMax Context IR"
API_WRITER = "API Writer"
LOCAL_GGUF = "Local GGUF"
BACKENDS = (LOCAL, CONTEXT_IR, API_WRITER, LOCAL_GGUF)

SYSTEM_PROMPT = (
    "Write a precise prompt for MiniMax-H3-Image describing one finished still image. "
    "Follow the user's subject, composition, style and other constraints. Describe the visible "
    "details, spatial relationships, framing, lighting, colors and materials in natural English. "
    "Retain the exact wording and language of text the image should contain. Clarify underspecified "
    "details without changing the intent. Output a JSON object containing only the requested fields. "
    "Put one unformatted English paragraph in expanded_caption, with no commentary or headings. "
    "Express the aspect ratio as integer W:H between 1:3 and 3:1."
)
EDIT_RULES = (
    "Write an edit prompt that makes the requested changes to the supplied images. "
    "Describe the finished result after applying the instruction. Identify the editable base "
    "and each source used for a change by its <Picture N> label. Explicitly name what changes "
    "and what the output retains. For example, if asked to recolor a blue bicycle in "
    "<Picture 1> with the yellow paint in <Picture 2>, describe the same bicycle and scene "
    "from <Picture 1> with a yellow finish sampled from <Picture 2>."
)
QWEN_IMAGE_TOKEN = "<|vision_start|><|image_pad|><|vision_end|>"


def pictures(supplied, layout, instructions):
    if set(supplied or {}) - {f"ref_{index}" for index in range(MAX_IMAGES)}:
        raise ContractError("H3 Image accepts up to 16 reference image sockets.")
    notes = object_of(instructions)
    result = []
    for slot, image in ordered(supplied, layout):
        shape = getattr(image, "shape", ())
        if len(shape) != 4 or min(shape) < 1 or shape[-1] not in (3, 4):
            raise ContractError(f"{slot}: connect a nonempty RGB or RGBA IMAGE.")
        note = notes.get(slot, "")
        note = note.get("text", "") if isinstance(note, dict) else note
        if not isinstance(note, str):
            raise ContractError(f"{slot}: reference instructions must be text.")
        spec = ReferenceSpec(slot, "Picture", "image", instruction=note.strip())
        result.append(ImageReference(spec, image[:1, :, :, :3]))
    return tuple(result)


def instruction(prompt, references):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ContractError("Enter an image request or editing instruction before running the writer.")
    parts = [prompt.strip()]
    parts.extend(f"For <Picture {index}>: {one.spec.instruction}" for index, one in enumerate(references, 1)
                 if one.spec.instruction)
    text = "\n".join(parts)
    if len(text) > MAX_PROMPT:
        raise ContractError("H3 Image accepts up to 32,000 characters including reference instructions.")
    return text


def read_ratio(value):
    match = re.fullmatch(r"([1-9][0-9]{0,2})\s*:\s*([1-9][0-9]{0,2})", value.strip()) if isinstance(value, str) else None
    if match:
        width, height = map(int, match.groups())
        if max(width, height) <= 300 and width <= 3 * height and height <= 3 * width:
            return f"{width}:{height}"
    raise ContractError("Enter an aspect ratio like 16:9, with sides from 1 to 300 and a ratio from 1:3 to 3:1.")


def canvas_selection(aspect_ratio, size, area):
    if area not in AREA_SIDES:
        raise ContractError("Select 1K, 2K or 4K canvas area.")
    ratio = "auto" if aspect_ratio.strip() in ("", "auto") else read_ratio(aspect_ratio)
    size = size.strip().lower()
    if size in ("", "auto"):
        return ratio, None
    if ratio != "auto":
        raise ContractError("Set aspect_ratio to auto when entering an exact size.")
    match = re.fullmatch(r"([1-9][0-9]{0,4})x([1-9][0-9]{0,4})", size)
    if not match:
        raise ContractError("Enter size as WIDTHxHEIGHT, for example 2720x1536.")
    width, height = map(int, match.groups())
    if (width % 32 or height % 32 or not 655_360 <= width * height <= 20_000_000
            or max(width, height) > 3 * min(width, height)):
        raise ContractError("Use canvas edges divisible by 32, 655,360 to 20,000,000 pixels and a ratio from 1:3 to 3:1.")
    return _ratio_text(Fraction(width, height)), (width, height)


def _ratio_text(ratio):
    ratio = min(Fraction(3), max(Fraction(1, 3), ratio)).limit_denominator(100)
    return f"{ratio.numerator}:{ratio.denominator}"


def fallback_ratio(references):
    if not references:
        return "1:1"
    height, width = references[0].value.shape[1:3]
    return _ratio_text(Fraction(width, height))


def canvas(ratio, size, area):
    if size:
        return size
    width, height = map(int, ratio.split(":"))
    side = AREA_SIDES[area]
    return (round(side * math.sqrt(width / height) / 32) * 32,
            round(side * math.sqrt(height / width) / 32) * 32)


def request_parts(text, references, ratio, *, attachments=()):
    parts = []
    ratio_field = "aspect_ratio"
    if references:
        ratio_field = "target_aspect_ratio"
        parts.extend((EDIT_RULES, f"Reference images in order: {len(references)}. "
                      "Use <Picture 1> as the base image unless the user specifies otherwise."))
        for index, one in enumerate(references, 1):
            height, width = one.value.shape[1:3]
            source = Fraction(width, height)
            parts.append(f"<Picture {index}> has source aspect ratio {source.numerator}:{source.denominator}.")
            if attachments:
                parts.append(attachments[index - 1])
        named = sorted({1} | {int(number) for number in PICTURE.findall(text) if 1 <= int(number) <= len(references)})
        labels = ", ".join(f"<Picture {index}>" for index in named)
        parts.append("Use these literal labels in expanded_caption to identify the base and any named sources: " + labels + ".")
    parts.append(f"Target aspect ratio: {ratio}. Choose a suitable ratio when it is auto.")
    parts.append("User instruction (JSON string): " + json.dumps(text, ensure_ascii=False))
    parts.append(f"Return expanded_caption and {ratio_field} as strings in a JSON object.")
    return parts, ratio_field


def chat_request(text, references, ratio, *, attachments=()):
    parts, field = request_parts(text, references, ratio, attachments=attachments)
    if attachments:
        user = [{"type": "text", "text": part + "\n"} if isinstance(part, str) else part for part in parts]
    else:
        user = "\n".join(parts)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}], field


def read_expansion(content, ratio, field, *, budget="max_length"):
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content.strip(), re.DOTALL | re.IGNORECASE)
    try:
        result = parse_json(fenced[1] if fenced else content)
    except (ValueError, RecursionError):
        result = None
    caption = result.get("expanded_caption") if isinstance(result, dict) else None
    if not isinstance(caption, str) or not caption.strip():
        raise ContractError(f"The prompt model did not return a JSON expanded_caption. Check the model or increase {budget}.")
    if ratio == "auto":
        try:
            ratio = read_ratio(result.get(field) or result.get("aspect_ratio"))
        except ContractError:
            raise ContractError(f"The prompt model did not choose a valid aspect ratio. Set aspect_ratio to a fixed ratio or increase {budget}.") from None
    return caption.strip(), ratio


def expand_local(clip, text, references, ratio, max_length, thinking, seed):
    tokenizer = type(getattr(clip, "tokenizer", None)).__name__
    if clip is None or tokenizer.startswith(("PH3ImageTokenizer", "MiniMaxH3Tokenizer")):
        raise ContractError("Connect a prompt model to upsampler, such as Qwen3.5 9B from the H3 Image ComfyUI template.")
    try:
        from comfy_extras.nodes_textgen import TextGenerateV2
    except ImportError:
        raise ContractError("Local CLIP needs ComfyUI with the Generate Text node (TextGenerateV2).") from None
    interleave = bool(references) and tokenizer.startswith("Qwen35")
    attachments = (QWEN_IMAGE_TOKEN,) * len(references) if interleave else ()
    parts, field = request_parts(text, references, ratio, attachments=attachments)
    prompt = "\n".join(parts)
    if interleave:
        think_prefix = "<think>\n" if thinking else "<think>\n\n</think>\n\n"
        prompt = (f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
                  f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n{think_prefix}")
    answer = TextGenerateV2.execute(
        clip=clip, prompt=prompt, system_prompt=SYSTEM_PROMPT, max_length=max_length,
        sampling_mode={"sampling_mode": "on", "temperature": 1.0, "top_k": 20, "top_p": 0.95, "seed": seed},
        thinking=thinking, use_default_template=True, mtp="auto",
        images={f"image{index}": vision_copy(one.value) for index, one in enumerate(references, 1)},
    )
    content, reasoning = answer.result
    caption, ratio = read_expansion(content, ratio, field)
    return caption, reasoning, ratio
