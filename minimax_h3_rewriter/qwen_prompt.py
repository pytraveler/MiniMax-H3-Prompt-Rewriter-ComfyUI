"""LightX2V's training request contracts, with ordered ComfyUI references."""

from __future__ import annotations

from dataclasses import replace
import re

from . import guide_prompt
from .api_contract import ContractError, reference_manifest
from .api_multimedia import Transport, prepare_references_async

FL_PREFIX = (
    "You are a professional prompt enhancement engineer for video and audio generation, "
    "with expertise in visual storytelling, shot planning, camera movement, and sound design. "
    "Before enhancing the prompt, carefully interpret the user's intended scene, subjects, actions, and audiovisual requirements. "
    "Preserve the core meaning, event sequence, and explicit constraints. "
    "Add relevant visual and sound details that enrich the intended scene without changing its premise or introducing unrelated events. "
    "Rewrite the user's prompt according to the following guide. "
    "Write the entire enhanced prompt in English, regardless of the original prompt's language. "
    "Apply only the specified task type and return only the final prompt.\n\n"
)
REF_PREFIX = (
    "You enhance prompts for reference-conditioned video and audio generation (Ref2VA). "
    "Infer reference roles from the user's prompt and supplied media. "
    "Preserve intent and explicit constraints while adding relevant visual and sound details. "
    "Follow the guide below and return only its six sections in order, "
    "with plain-text headers on separate lines and no Markdown fences.\n\n"
)
FRAME_ROLES = {"T2VA": (), "I2VA": ("first",), "L2VA": ("last",), "FL2VA": ("first", "last")}


def validate_family(family, task, assets):
    if task not in guide_prompt.ALL_MODES:
        raise ContractError("Unknown H3 task.")
    if (family == "Ref2VA") != (task == "Ref2VA"):
        raise ContractError("Choose the Ref2VA checkpoint for Ref2VA, or the FL2VA checkpoint for T2VA/I2VA/L2VA/FL2VA.")
    reference_manifest(task, tuple(one.spec for one in assets))
    if task in FRAME_ROLES and any(one.spec.role != "Picture" or one.spec.kind != "image" for one in assets):
        raise ContractError(task + " accepts exact Picture frames only. Use Ref2VA for Subject, Video or Audio references.")


async def prepare(assets, settings, duration, cancelled=None):
    """Decode visual copies only; these checkpoints have no audio encoder."""
    missing = [(one.spec, "Sound description") for one in assets
               if one.spec.kind == "audio" and not one.spec.description.strip()]
    missing.extend((one.spec, "Clip sound description") for one in assets
                   if one.spec.clip_audio and not one.spec.audio_description.strip())
    if missing:
        labels = {one.slot: one.label for one in reference_manifest("Ref2VA", tuple(one.spec for one in assets))
                  if one.component == "media"}
        instructions = []
        for spec, field in missing:
            label = f"{labels[spec.slot]} ({spec.slot})"
            if spec.clip_audio:
                alternative = "If you do not need the sound, click this chip's top label to turn off sound and keep the video."
                reason = "Sound is enabled, but its description is empty. "
            else:
                alternative = "If you do not need this audio, disable its input checkbox."
                reason = "An explicit sound description is required. "
            instructions.append(f"{label}: {reason}Open + instr on this chip, fill in '{field}', "
                                f"then click 'Save instructions'. {alternative}")
        raise ContractError("Qwen cannot hear reference audio. It uses your text description of the voice, words or sounds.\n\n"
                            + "\n\n".join(instructions))
    visual = tuple(replace(one, spec=replace(one.spec, clip_audio=False)) for one in assets if one.spec.kind != "audio")
    resolved, transport = await prepare_references_async(
        visual, max_side=settings.image_max_side, byte_limit=64 << 20,
        video_max_frames=settings.video_max_frames, video_sample_seconds=settings.video_sample_seconds,
        video_max_seconds=settings.video_max_seconds, batch_video_fps=settings.batch_video_fps,
        target_seconds=duration, cancelled=cancelled)
    notes = list(transport.notes)
    if any(one.spec.kind == "audio" or one.spec.clip_audio for one in assets):
        notes.append("Audio contents were supplied as explicit descriptions. This Qwen checkpoint receives no waveform; originals remain in references.")
    return Transport(transport.images, (), tuple(notes), transport.videos)


def _text(value):
    return {"type": "text", "text": value}


def build(job, transport):
    """Image placeholders and returned images have exactly the same order."""
    manifest = job.manifest
    images, parts = [], []
    if job.task != "Ref2VA":
        roles = FRAME_ROLES[job.task]
        if len(transport.images) != len(roles):
            raise ContractError(job.task + " requires one image per exact frame; an IMAGE batch is not a single frame.")
        instruction = {
            "T2VA": "",
            "I2VA": "Inspect the attached <Picture 1>, which is the video's first frame, and enhance the prompt following the guide's I2VA rules.\n",
            "L2VA": "Inspect the attached <Picture 1>, which is the video's last frame, and enhance the prompt following the guide's L2VA rules.\n",
            "FL2VA": "Inspect the attached <Picture 1> (the video's first frame) and <Picture 2> (the video's last frame), and enhance the prompt following the guide's FL2VA rules.\n",
        }[job.task]
        parts.append(_text(f"Task type: {job.task}.\nVideo duration: {job.duration:g} seconds.\n"
                           + instruction + "Choose shots that best express the original intent, using separate shots for distinct subjects, details, reactions, or meaningful changes in scene, viewpoint, or time.\n"
                           "Allocate shot durations according to content and pacing.\n"
                           "Respect explicit shot requests and follow the guide's task-specific, formatting, and transition rules.\n"
                           f"Target aspect ratio: {job.aspect_ratio}.\nOriginal prompt: {job.prompt}\n"))
        for number, (role, image, ref) in enumerate(zip(roles, transport.images, manifest), 1):
            at = 0.0 if role == "first" else job.duration
            parts.append(_text(f"<Picture {number}>: the video's {role} frame at {at:.2f} seconds.\n"
                               + ref.instruction + ("\nDescription: " + ref.description if ref.description else "")))
            parts.append({"type": "image"})
            images.append(image)
    else:
        prompt = re.sub(r"<(Picture|Video|Audio)\s+(\d+)>",
                        lambda match: {"Picture": "image", "Video": "video", "Audio": "audio"}[match[1]] + " " + match[2], job.prompt)
        parts.append(_text(f"Target video duration: {job.duration:.6f} seconds.\nTarget aspect ratio: {job.aspect_ratio}.\n"
                           "Use the supplied references in their labelled roles, following their individual instructions. "
                           "Do not infer unheard audio contents.\nOriginal prompt:\n" + prompt + "\n"))
        for ref in manifest:
            label = {"Picture": "image", "Subject": "Subject", "Video": "video", "Audio": "audio"}[ref.role] + f" {ref.index}"
            parts.append(_text(f"{label}:\n" + (ref.instruction + "\n" if ref.instruction else "")
                               + ("Description: " + ref.description + "\n" if ref.description else "")))
            if ref.role == "Audio":
                continue
            frames = [one for one in transport.images if one.slot == ref.slot]
            if not frames:
                raise ContractError(ref.slot + ": the visual reference has no decoded images.")
            for frame in frames:
                if ref.role == "Video":
                    parts.append(_text(f"{label}, frame {frame.frame}/{frame.frames}, source timestamp {frame.source_timestamp_seconds:.6f} seconds:\n"))
                parts.append({"type": "image"})
                images.append(frame)
    return [{"role": "system", "content": job.system_prompt}, {"role": "user", "content": parts}], images


def system(guide, task):
    return (REF_PREFIX if task == "Ref2VA" else FL_PREFIX) + guide


def split_reasoning(raw, thinking=False):
    if "</think>" in raw:
        thought, text = raw.rsplit("</think>", 1)
        return text.strip(), thought.removeprefix("<think>").strip()
    if "<think>" in raw:
        text, thought = raw.split("<think>", 1)
        return text.strip(), thought.strip()
    if thinking:
        return "", raw.strip()
    return raw.strip(), ""
