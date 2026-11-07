"""Additional API writer node. Existing nodes and their widgets are independent."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

from . import api_config, checks, guide_prompt, guides, library, memory, previews, snapshot
from .api_client import ApiClient, ApiError, prepare_payload, validate_job
from .api_contract import ContractError, Generation, make_job, reference_manifest
from .api_media import MAX_REFERENCES
from .api_multimedia import (REFERENCE_TYPES, arrange_references, prepare_references_async,
                             resolve_clip_sounds_async)
from .api_options import (OPTIONS_TYPE, MiniMaxH3ApiWriterOptions, resolve_options)
from .api_models import effective_profile
from .api_secrets import SecretResolver
from .api_text import write_async
from .constants import RESOLUTIONS, duration_widget
from .fields import ALL_FIELDS, split_sections
from .progress import NodeProgress, announce
from .references import SLOTS_OUTPUT_TOOLTIP, SLOTS_TYPE, slot_bundle

NODE = "MiniMaxH3ApiUniversalWriter"
TEXT_OUTPUTS = ("prompt",) + ALL_FIELDS + ("reasoning", "findings", "report")


def _announce_previews(node_id, assets, transport):
    """Use the bounded JPEG copies, preserving Video kinds for RefMod stacks."""
    if node_id is None:
        return
    import io

    from PIL import Image

    from .media import frame_indices

    slots = {}
    for asset in assets:
        spec = asset.spec
        frames = [one for one in transport.images if one.slot == spec.slot]
        if not frames:
            continue
        entry = {"kind": spec.kind}
        try:
            shots = []
            for index in frame_indices(len(frames), previews.FRAMES):
                with Image.open(io.BytesIO(frames[index].jpeg)) as image:
                    shots.append(previews._jpeg(image))
            first = frames[0]
            entry.update(thumb=shots[0], width=first.source_width, height=first.source_height)
            if len(shots) > 1:
                entry.update(frames=shots, count=first.frames)
            seconds = getattr(first, "window_seconds", 0)
            if seconds:
                entry["seconds"] = seconds
        except Exception:
            previews.log.info("[minimax_h3_rewriter.previews] %s could not be previewed", spec.slot)
        slots[spec.slot] = entry
    previews.publish(node_id, slots)


def _snapshots(assets, transport, media_mode):
    """Record only prepared thumbnails; never decode a clip again after completion."""
    import io

    from PIL import Image

    records = []
    for asset in assets:
        spec = asset.spec
        record = {"label": f"ref{len(records) + 1}-{spec.kind}", "slot": spec.slot, "kind": spec.kind}
        counted = {"Picture": "image", "Subject": "", "Video": "video", "Audio": "audio"}[spec.role]
        if counted != spec.kind:
            record["as"] = counted
        if media_mode == "native":
            first = next((one for one in transport.images if one.slot == spec.slot), None)
            if first is not None:
                with Image.open(io.BytesIO(first.jpeg)) as image:
                    record.update(width=first.source_width, height=first.source_height, thumb=snapshot._square(image))
            audio = next((one for one in transport.audios if one.slot == spec.slot and one.component == "media"), None)
            if audio is not None:
                record.update(rate=audio.source_sample_rate, channels=audio.source_channels, seconds=audio.source_seconds)
        records.append(record)
    return records


def _reused(outputs, task, duration, validation, assets, source):
    text = outputs[0]
    manifest = reference_manifest(task, tuple(one.spec for one in assets))
    sections, issues = {}, ()
    if validation == "h3":
        names = guide_prompt.FIELDS_FOR_MODE[task]
        _head, sections = split_sections(text, names, fallback=guide_prompt.BODY_FIELD[task])
        issues = checks.review(text, sections, names, task=task, duration=duration,
                              having=[checks.TAG_KIND[one.role] for one in manifest if one.role in checks.TAG_KIND])
    provenance = {}
    if len(outputs) >= len(TEXT_OUTPUTS):
        try:
            original = json.loads(outputs[len(TEXT_OUTPUTS) - 1])
            provenance = {name: original[name] for name in ("profile_id", "model") if isinstance(original.get(name), str)}
        except (ValueError, TypeError, AttributeError):
            pass
    report = json.dumps({**provenance, "source": source, "task": task, "validation": validation,
                         "requests": 0, "completed_requests": 0,
                         "references": [one.to_dict() for one in manifest],
                         "notes": ["A kept prompt was returned without an API request. Current references are passed through; they were not sent to a model."] +
                                  [note for asset in assets for note in asset.notes]}, indent=2)
    return (text,) + tuple(sections.get(name, "") for name in ALL_FIELDS) + ("", checks.describe(issues), report)


def _cancelled():
    import comfy.model_management

    return comfy.model_management.processing_interrupted()


class MiniMaxH3ApiUniversalWriter:
    """API writer with shared writer controls and optional separate settings."""

    @classmethod
    def INPUT_TYPES(cls):
        ids = []
        try:
            ids = [one.id for one in api_config.load_profiles().profiles]
        except api_config.ConfigError:
            pass
        return {"required": {
            "profile_id": (ids or ["configure-a-profile"], {
                "tooltip": "Server-side connection ID. Use the API providers window beside the model selector, or the local api_admin CLI. Keys are never node inputs.",
            }),
            "task": (list(guide_prompt.ALL_MODES), {"default": "T2VA", "tooltip": "T2VA ignores references. I2VA starts at Picture 1; L2VA ends there; FL2VA starts at Picture 1 and ends at Picture 2. Ref2VA accepts pictures, subjects, clips and sounds."}),
            "prompt": ("STRING", {"default": "", "multiline": True, "tooltip": "The raw request, sent to the model without trimming or rewriting."}),
            "resolution": (list(RESOLUTIONS), {"default": "16:9"}),
            "duration": (duration_widget()[0], {**duration_widget()[1], "display": "slider"}),
            "max_tokens": ("INT", {"default": 4096, "min": 1, "max": 131072, "tooltip": "Output budget per request, bounded by the selected profile. Some models count reasoning against this budget too."}),
        }, "optional": {
            "system_prompt": ("STRING", {"default": "", "multiline": True, "tooltip": "Replaces the full guide/system message. Leave empty to use the MiniMax-H3 guide."}),
            "validation": (["h3", "custom"], {"default": "h3", "tooltip": "h3 splits and checks the H3 sections. custom returns plain final text without H3 checks."}),
            "response_format": (["text", "json_object", "json_schema"], {"default": "text", "tooltip": "Must be supported by the profile. Custom validation requires text."}),
            "temperature": ("FLOAT", {"default": -1.0, "min": -1.0, "max": 2.0, "step": 0.1, "tooltip": "-1 omits the parameter and uses the server default."}),
            "seed": ("INT", {"default": -1, "min": -1, "max": 0xFFFFFFFF, "control_after_generate": "fixed", "tooltip": "-1 with fixed control omits seed. Only use a positive or randomized seed when the profile supports it. A cloud seed does not guarantee identical answers."}),
            "timeout_seconds": ("FLOAT", {"default": 180.0, "min": 1.0, "max": 3600.0, "step": 1.0, "tooltip": "Total deadline per HTTP request, including response reading. Cancellation closes our connection; the server may still process the request."}),
            "repair_once": ("BOOLEAN", {"default": False, "tooltip": "Allow at most ONE additional, potentially paid request for repairable H3 findings. Off by default; no transport retries."}),
            "auto_download_guide": ("BOOLEAN", {"default": True, "tooltip": "Fetch the MiniMax guide if missing. A custom system prompt skips guide access entirely."}),
            "reference_layout": ("STRING", {"default": "{}", "tooltip": "Reference strip order, disabled slots and Picture/Subject roles. Stored with the workflow."}),
            "reference_instructions": ("STRING", {"default": "{}", "tooltip": "JSON object with usage instructions by ref_0...ref_11. Instructions accompany the image; no local captioner runs."}),
            "image_max_side": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64, "tooltip": "Maximum side of API image copies. Preserves aspect ratio, never enlarges. All batch frames are sent under the same reference label. Original outputs are unchanged."}),
            "repeat_last": ("BOOLEAN", {"default": False, "tooltip": "Return this node's last prompt, or the prompt chosen in its library, without an API request. With nothing kept yet, run once. No server or local model is stopped."}),
            "library_pick": ("STRING", {"default": "", "tooltip": library.PICK_TOOLTIP}),
            **{f"ref_{index}": (REFERENCE_TYPES, {"tooltip": "IMAGE, VIDEO, AUDIO or a decoded RefMod. Images can be Picture, Subject or Video. Click a VIDEO label to include its sound. Originals pass through references."}) for index in range(MAX_REFERENCES)},
            "media_mode": (["native", "descriptions"], {"default": "native", "tooltip": "native sends images, timestamped video frames and enabled WAV audio. descriptions sends only explicit text/saved RefMod descriptions; no automatic fallback or local captioner."}),
            "reference_descriptions": ("STRING", {"default": "{}", "multiline": True, "tooltip": "Descriptions by ref_0...ref_11: a string or {text: description, sound: clip sound description}. Required for each enabled component in descriptions mode. Also accepts a wired JSON string."}),
            "video_max_frames": ("INT", {"default": 8, "min": 1, "max": 64, "tooltip": "Maximum sampled frames per Video reference; all media share a 64-image limit. Sampling spreads across the selected window when this cap is reached."}),
            "video_sample_seconds": ("FLOAT", {"default": 1.0, "min": 0.05, "max": 120.0, "step": 0.05, "tooltip": "Requested video sampling interval. Actual timestamps and the selected count appear in the report."}),
            "video_max_seconds": ("FLOAT", {"default": 15.0, "min": 0.1, "max": 120.0, "step": 0.1, "tooltip": "Read from the clip's active trim start, for at most this limit, the target duration and H3's 15-second reference limit."}),
            "batch_video_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 1.0, "tooltip": "Clock for an IMAGE batch assigned the Video role. VIDEO objects use their own timestamps. RefMods use saved frame times."}),
            "audio_max_seconds": ("FLOAT", {"default": 30.0, "min": 0.1, "max": 120.0, "step": 0.1, "tooltip": "Maximum standalone audio/voice window sent to the API. The report shows cropping. Clip sound follows the same window as its video."}),
            "audio_sample_rate": ([16000, 24000, 44100, 48000], {"default": 16000, "tooltip": "API copies are mono 16-bit PCM WAV at this rate. Original audio remains unchanged."}),
            "bypass": ("BOOLEAN", {"default": False, "tooltip": "Pass the original prompt and enabled original references through without an API request, guide access or media conversion."}),
            "options": (OPTIONS_TYPE, {"tooltip": "Connect MiniMax-H3 API Writer Options to set generation and media-copy parameters. Connected options override the legacy settings retained by old workflows."}),
            "model": ("STRING", {"default": "", "tooltip": "Empty uses the profile default. An explicit model ID must be allowed locally by this connection profile. Refresh models reads /models; it does not load or switch server processes."}),
        }, "hidden": {"unique_id": "UNIQUE_ID"}}

    RETURN_TYPES = ("STRING",) * len(TEXT_OUTPUTS) + (SLOTS_TYPE,)
    RETURN_NAMES = TEXT_OUTPUTS + ("references",)
    FUNCTION = "execute"
    CATEGORY = "MiniMax-H3"
    DESCRIPTION = (
        "Writes an H3 prompt through an already running local LLM or a configured remote API. "
        "A connection ID and optional model ID are stored in the workflow; API keys are configured locally. "
        "The guide or a complete custom system prompt and the raw request reach the selected model. "
        "Enabled media are sent as JPEG images, timestamped video frames and WAV audio, with their roles and instructions. "
        "Original references reach Reference Slots or Reference Encode in the same numbered order. "
        "RefMods require decoded media from their loader, or explicit descriptions mode. No local model is loaded or stopped."
    )

    @classmethod
    def IS_CHANGED(cls, library_pick="", repeat_last=False, unique_id=None, **_inputs):
        try:
            return repr(api_config.config_stamp()) + library.stamp(library_pick, repeat_last) + memory.stamp(unique_id, repeat_last)
        except api_config.ConfigError:
            return float("nan")

    async def execute(self, profile_id, task, prompt, resolution, duration, max_tokens=4096,
                system_prompt="", validation="h3", response_format="text", temperature=-1.0,
                seed=-1, timeout_seconds=180.0, repair_once=False, auto_download_guide=True,
                unique_id=None, reference_layout="{}", reference_instructions="{}", image_max_side=1024,
                repeat_last=False, library_pick="", media_mode="native", reference_descriptions="{}",
                video_max_frames=8, video_sample_seconds=1.0, video_max_seconds=15.0, batch_video_fps=24.0,
                audio_max_seconds=30.0, audio_sample_rate=16000, bypass=False, options=None, model="", **references):
        if task not in guide_prompt.ALL_MODES:
            raise ContractError("Unknown H3 task.")
        if any(name not in {f"ref_{index}" for index in range(MAX_REFERENCES)} for name in references):
            raise ContractError("Unknown API writer input.")
        if type(repeat_last) is not bool or type(bypass) is not bool or not isinstance(library_pick, str):
            raise ContractError("Invalid bypass, repeat or library options.")
        assets = () if task == "T2VA" else arrange_references(references, reference_layout, reference_instructions,
                                                           reference_descriptions)
        if bypass:
            if not isinstance(prompt, str):
                raise ContractError("The original prompt must be text.")
            handed_on = slot_bundle((one.spec.slot, one.spec.role, one.handed,
                                    one.spec.clip_audio and task == "Ref2VA") for one in assets)
            report = json.dumps({"source": "bypass", "task": task, "requests": 0, "completed_requests": 0,
                                 "notes": ["The original prompt and references were passed through without an API request or media preparation."]})
            NodeProgress(unique_id).finish("bypassed")
            return (prompt.strip(),) + ("",) * (len(TEXT_OUTPUTS) - 2) + (report, handed_on)
        settings = resolve_options(options, max_tokens=max_tokens, response_format=response_format,
            temperature=temperature, timeout_seconds=timeout_seconds, auto_download_guide=auto_download_guide,
            video_sample_seconds=video_sample_seconds, video_max_seconds=video_max_seconds,
            batch_video_fps=batch_video_fps, audio_sample_rate=audio_sample_rate, repair_once=repair_once)
        max_tokens, response_format, temperature = settings.max_tokens, settings.response_format, settings.temperature
        timeout_seconds, auto_download_guide = settings.timeout_seconds, settings.auto_download_guide
        video_sample_seconds, video_max_seconds = settings.video_sample_seconds, settings.video_max_seconds
        batch_video_fps, audio_sample_rate = settings.batch_video_fps, settings.audio_sample_rate
        repair_once = settings.repair_once
        try:
            assets = await resolve_clip_sounds_async(assets, cancelled=_cancelled)
        except ApiError as error:
            if error.code == "cancelled":
                import comfy.model_management

                comfy.model_management.throw_exception_if_processing_interrupted()
            raise
        handed_on = slot_bundle((one.spec.slot, one.spec.role, one.handed, one.spec.clip_audio) for one in assets)
        given = {"task": task, "duration": duration, "resolution": resolution, "profile_id": profile_id,
                 "model": model,
                 "validation": validation, "response_format": response_format,
                 "image_max_side": image_max_side, "max_tokens": max_tokens, "media_mode": media_mode,
                 "input_digest": hashlib.sha256(str((prompt, system_prompt, reference_layout,
                                                      reference_instructions, reference_descriptions)).encode("utf-8", "replace")).hexdigest()}
        chosen, saved = library.picked(library_pick, repeat_last, NODE, len(TEXT_OUTPUTS), unique_id,
                                      having=[one.spec.kind for one in assets])
        if saved is not None:
            outputs = chosen or (saved,)
            return _reused(outputs, task, duration, validation, assets, "library") + (handed_on,)
        kept = memory.repeat(unique_id, NODE, repeat_last, given)
        if kept is not None:
            record = memory.recall(unique_id)
            return _reused(kept, record.task, record.about.get("duration", duration),
                           record.about.get("validation", "h3"), assets, "session") + (handed_on,)
        if type(temperature) not in (float, int) or temperature != -1 and not 0 <= temperature <= 2:
            raise ContractError("Invalid API temperature.")
        if type(seed) is not int or seed < -1:
            raise ContractError("Invalid API seed.")
        if not isinstance(system_prompt, str) or not isinstance(prompt, str) or not prompt.strip():
            raise ContractError("The system prompt must be text and the original prompt must not be empty.")
        if type(auto_download_guide) is not bool or type(repair_once) is not bool:
            raise ContractError("Invalid API writer options.")
        profiles = api_config.load_profiles()
        profile = effective_profile(profiles.resolve(profile_id), model)
        given["model"] = profile.model
        job = make_job(profile_id=profile_id, task=task, prompt=prompt, guide="Guide pending local retrieval.",
                       model=model,
                       system_prompt=system_prompt, validation=validation,
                       aspect_ratio=resolution, duration=duration, response_format=response_format,
                       references=tuple(one.spec for one in assets), media_mode=media_mode,
                       generation=Generation(max_tokens, None if temperature == -1 else temperature,
                                             None if seed == -1 else seed))
        validate_job(job, profile, check_descriptions=False)
        progress = NodeProgress(unique_id)
        try:
            progress.text("Preparing references for the selected API profile", force=True)
            preparation_started = time.monotonic()
            assets, transport = await prepare_references_async(assets, media_mode=media_mode, max_side=image_max_side,
                byte_limit=profile.limits.request_bytes, target_seconds=duration, video_max_frames=video_max_frames,
                video_sample_seconds=video_sample_seconds, video_max_seconds=video_max_seconds,
                batch_video_fps=batch_video_fps, audio_max_seconds=audio_max_seconds,
                audio_sample_rate=audio_sample_rate, cancelled=_cancelled)
            preparation_seconds = time.monotonic() - preparation_started
            job = replace(job, references=tuple(one.spec for one in assets))
            handed_on = slot_bundle((one.spec.slot, one.spec.role, one.handed, one.spec.clip_audio) for one in assets)
            client = ApiClient(profiles, authorize=SecretResolver(), deadline=timeout_seconds,
                               timeout=(5.0, timeout_seconds), images=transport.images, audios=transport.audios)
            prepare_payload(job, profile, transport.images, transport.audios)
            if media_mode == "native":
                _announce_previews(unique_id, assets, transport)
            if not system_prompt.strip():
                guide = guides.text(guide_prompt.GUIDE_FOR_MODE[task], auto_download_guide, progress)
                job = replace(job, system_prompt=guide_prompt.system_prompt(guide, task, duration))
            progress.text("Writing through the selected API profile", force=True)
            written = await write_async(job, client, repair_once=repair_once, cancelled=_cancelled)
        except ApiError as error:
            if error.code != "cancelled":
                raise
            import comfy.model_management

            comfy.model_management.throw_exception_if_processing_interrupted()
            raise
        reading = written.reading
        if reading.issues:
            announce(unique_id, reading.issues, kind="check")
        findings = checks.describe(reading.issues)
        progress.finish((findings + "\n\n" if findings else "") + reading.text[-2000:])
        report = json.loads(written.report)
        report["media_mode"] = media_mode
        report["preparation_seconds"] = round(preparation_seconds, 3)
        report["media"] = transport.metadata()
        report["videos"] = list(transport.videos)
        report["notes"].extend(transport.notes)
        if media_mode == "descriptions" and assets:
            report["notes"].append("Descriptions mode was explicitly selected; only text descriptions were sent, with no media attachments or local captioning.")
        if task == "T2VA" and any(value is not None for value in references.values()):
            report["notes"].append("T2VA ignores connected references; no images were prepared or sent.")
        outputs = (reading.text,) + tuple(reading.sections.get(name, "") for name in ALL_FIELDS) + (
            written.result.reasoning, findings, json.dumps(report, ensure_ascii=False, indent=2),
        )
        memory.keep(unique_id, NODE, outputs, given,
                    references=_snapshots(assets, transport, media_mode),
                    fields=ALL_FIELDS if validation == "h3" else (), plain_text=validation == "custom")
        return outputs + (handed_on,)


NODE_CLASS_MAPPINGS = {"MiniMaxH3ApiUniversalWriter": MiniMaxH3ApiUniversalWriter,
                       "MiniMaxH3ApiWriterOptions": MiniMaxH3ApiWriterOptions}
NODE_DISPLAY_NAME_MAPPINGS = {NODE: "MiniMax-H3 API Universal Writer",
                              "MiniMaxH3ApiWriterOptions": "MiniMax-H3 API Writer Options"}
