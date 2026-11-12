"""One reference-aware node for LightX2V's full Qwen SFT checkpoints."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from fractions import Fraction
import hashlib
import json
import time

from . import aspect, checks, guide_prompt, guides, library, memory, qwen_engine, qwen_models, qwen_prompt, repair, resident
from .api_contract import ContractError, Generation, make_job, reference_manifest
from .api_multimedia import REFERENCE_TYPES, Transport, arrange_references
from .api_text import read_answer
from .api_writer import _announce_previews, _snapshots
from .constants import RESOLUTIONS, duration_widget
from .fields import ALL_FIELDS
from .progress import NodeProgress, announce
from .qwen_options import OPTIONS_TYPE, MiniMaxH3QwenRewriterOptions, resolve_options
from .references import SLOTS_TYPE, slot_bundle

NODE = qwen_models.NODE
TEXT_OUTPUTS = ("prompt",) + ALL_FIELDS + ("reference_assets", "reasoning", "findings", "report")


def _cancelled():
    from .runner import interrupted
    return interrupted()


async def _worker(function):
    """Cancellation never leaves a model-loading/generation worker behind."""
    operation = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        import comfy.model_management as mm
        mm.interrupt_current_processing(True)
        try:
            await operation
        except BaseException:
            pass
        raise


def _ratio(value, chosen):
    ratio = aspect.resolve(value, chosen)
    left, right = ratio.split(":")
    fraction = (Fraction(left) / Fraction(right)).limit_denominator(1000)
    return f"{fraction.numerator}:{fraction.denominator}"


class MiniMaxH3QwenRewriter:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "reference_layout": ("STRING", {"default": "{}"}),
            "task": (list(guide_prompt.ALL_MODES), {"default": "T2VA"}),
            "resolution": (list(RESOLUTIONS), {"default": "16:9", "tooltip": aspect.PICKER_TOOLTIP}),
            "duration": (duration_widget()[0], {**duration_widget()[1], "display": "slider"}),
            "model": (qwen_models.model_choices(), {"tooltip": "Full LightX2V SFT or its GGUF conversion. FL2VA serves T2VA/I2VA/L2VA/FL2VA; Ref2VA serves Ref2VA."}),
            "prompt": ("STRING", {"default": "", "multiline": True, "dynamicPrompts": False}),
            "system_prompt": ("STRING", {"default": "", "multiline": True, "tooltip": "Empty uses LightX2V's inference prefix and the official task guide. A nonempty value replaces that entire system prompt."}),
            "greedy": ("BOOLEAN", {"default": False}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 2**32 - 1, "control_after_generate": "fixed"}),
            "keep_model_loaded": ("BOOLEAN", {"default": False, "tooltip": "Keep this SFT model or the node's owned GGUF server. Unload model and ComfyUI Unload Models release it."}),
            "bypass": ("BOOLEAN", {"default": False, "tooltip": "Pass the original prompt and active references through; no download, media decoding or inference."}),
            "repeat_last": ("BOOLEAN", {"default": False, "tooltip": "Reuse this node's last prompt or a library selection without loading a model."}),
            "reference_instructions": ("STRING", {"default": "{}", "multiline": True}),
            "reference_descriptions": ("STRING", {"default": "{}", "multiline": True}),
            "library_pick": ("STRING", {"default": "", "tooltip": library.PICK_TOOLTIP}),
        }, "optional": {
            **{f"ref_{index}": (REFERENCE_TYPES, {"lazy": True, "tooltip": "IMAGE, VIDEO, AUDIO or decoded RefMod. Disable without disconnecting with the input checkbox. Audio requires an explicit description."}) for index in range(12)},
            "options": (OPTIONS_TYPE,),
            "aspect_ratio": ("STRING", {"forceInput": True, "tooltip": aspect.TOOLTIP}),
        }, "hidden": {"unique_id": "UNIQUE_ID"}}

    RETURN_TYPES = ("STRING",) * len(TEXT_OUTPUTS) + (SLOTS_TYPE,)
    RETURN_NAMES = TEXT_OUTPUTS + ("references",)
    FUNCTION = "execute"
    CATEGORY = "MiniMax-H3"
    DESCRIPTION = (
        "Direct multimodal rewriting with the full LightX2V Qwen3.5 9B / Qwen3.8 27B "
        "SFT checkpoints or their GGUF conversions. No separate captioner or LoRA is required. "
        "Ordered references and instructions reach the writer; original conditioning passes through references. "
        "Video uses timestamped samples. Audio uses explicit descriptions because these checkpoints have no audio encoder."
    )

    @classmethod
    def IS_CHANGED(cls, library_pick="", repeat_last=False, unique_id=None, **_inputs):
        return library.stamp(library_pick, repeat_last) + memory.stamp(unique_id, repeat_last)

    def check_lazy_status(self, task, reference_layout="{}", **inputs):
        if task == "T2VA":
            return []
        from .api_media import _mapping
        layout = _mapping(reference_layout, "reference layout")
        off = layout.get("off", [])
        if not isinstance(off, list):
            raise ValueError("Invalid disabled reference slots.")
        return [name for name, value in inputs.items()
                if name.startswith("ref_") and value is None and name not in off]

    @classmethod
    def VALIDATE_INPUTS(cls, model, bypass=False, repeat_last=False):
        if bypass or repeat_last:
            return True
        try:
            qwen_models.resolve(model)
        except ValueError as error:
            return str(error)
        return True

    async def execute(self, reference_layout, task, resolution, duration, model, prompt, system_prompt="",
                      greedy=False, seed=0, keep_model_loaded=False, bypass=False, repeat_last=False,
                      reference_instructions="{}", reference_descriptions="{}", library_pick="",
                      options=None, aspect_ratio=None, unique_id=None, **references):
        if task not in guide_prompt.ALL_MODES:
            raise ValueError("Unknown H3 task.")
        if any(type(value) is not bool for value in (greedy, keep_model_loaded, bypass, repeat_last)):
            raise ValueError("Invalid Qwen writer switches.")
        assets = () if task == "T2VA" else arrange_references(references, reference_layout,
                                                              reference_instructions, reference_descriptions)
        handed = slot_bundle((one.spec.slot, one.spec.role, one.handed, one.spec.clip_audio) for one in assets)
        manifest = reference_manifest(task, tuple(one.spec for one in assets))
        reference_text = json.dumps([one.to_dict() for one in manifest], ensure_ascii=False, indent=2)
        progress = NodeProgress(unique_id)
        if bypass:
            if not isinstance(prompt, str):
                raise ValueError("The original prompt must be text.")
            progress.finish("Bypassed")
            return (prompt,) + ("",) * len(ALL_FIELDS) + (reference_text, "", "",
                    json.dumps({"source": "bypass", "generations": 0, "task": task}), handed)
        settings = resolve_options(options)
        ratio = _ratio(aspect_ratio, resolution)
        given = {"model": model, "task": task, "duration": duration, "resolution": ratio,
                 "prompt_file": settings.prompt_file, "options": asdict(settings),
                 "input_digest": hashlib.sha256(str((prompt, system_prompt, reference_layout,
                                reference_instructions, reference_descriptions)).encode("utf-8", "replace")).hexdigest()}
        chosen, saved = library.picked(library_pick, repeat_last, NODE, len(TEXT_OUTPUTS), unique_id,
                                      having=[one.spec.kind for one in assets], keep_loaded=keep_model_loaded)
        kept = None if saved is not None else memory.repeat(unique_id, NODE, repeat_last, given)
        if saved is not None or kept is not None:
            outputs = chosen or kept or (saved,)
            text = outputs[0]
            job = make_job(profile_id="qwen", task=task, validation="h3", prompt=prompt or "Reuse a kept prompt.",
                           system_prompt="Reusing a kept prompt.", duration=duration, aspect_ratio=ratio,
                           references=tuple(one.spec for one in assets))
            reading = read_answer(job, text)
            resident.settle(keep_model_loaded)
            report = json.dumps({"source": "library" if saved is not None else "session", "task": task,
                                 "generations": 0, "references": [one.to_dict() for one in manifest]}, indent=2)
            progress.finish("Reused a kept prompt")
            return (reading.text,) + tuple(reading.sections.get(name, "") for name in ALL_FIELDS) + (
                reference_text, "", checks.describe(reading.issues), report, handed)
        choice = qwen_models.resolve(model)
        qwen_prompt.validate_family(choice.family, task, assets)
        job = make_job(profile_id="qwen", task=task, validation="h3", prompt=prompt, system_prompt=system_prompt or "Guide pending retrieval.",
                       duration=duration, aspect_ratio=ratio, references=tuple(one.spec for one in assets),
                       generation=Generation(settings.max_new_tokens, 0.0 if greedy else settings.temperature, seed))
        qwen_engine.check_support(choice, settings)
        progress.text("Preparing Qwen references", force=True)
        try:
            transport = await qwen_prompt.prepare(assets, settings, duration, _cancelled)
        except ContractError as error:
            progress.finish(str(error))
            announce(unique_id, [("warn", str(error))], kind="refusal")
            raise
        _announce_previews(unique_id, assets, transport)
        if not system_prompt.strip():
            guide = await _worker(lambda: guides.text(guide_prompt.GUIDE_FOR_MODE[task], settings.auto_download, progress))
            job = replace(job, system_prompt=qwen_prompt.system(guide, task))
        messages, images = qwen_prompt.build(job, transport)
        files = await _worker(lambda: qwen_models.ensure(choice, settings, progress))
        started = time.monotonic()

        async def generate(request):
            return await _worker(lambda: qwen_engine.generate(choice, files, request, images, settings,
                                 seed, greedy, keep_model_loaded, progress))

        text, reasoning, usage = await generate(messages)
        runtime = usage.pop("runtime", {})
        if not text.strip():
            raise RuntimeError("Qwen returned no final prompt. Its output budget may have been consumed by thinking; increase max_new_tokens or disable thinking in Qwen Rewriter Options.")
        reading = read_answer(job, text)
        attempts, repaired = 1, False
        retry = repair.instruct(reading.issues)
        if settings.repair_once and retry and not repair.hopeless(reading.issues, reading.sections, guide_prompt.FIELDS_FOR_MODE[task]):
            amended = replace(job, prompt=job.prompt + retry)
            request, _ = qwen_prompt.build(amended, transport)
            another, thought, another_usage = await generate(request)
            another_usage.pop("runtime", None)
            attempts += 1
            if another.strip():
                candidate = read_answer(job, another)
                if repair.better(reading.issues, candidate.issues):
                    reading, reasoning, repaired = candidate, thought, True
            usage = {key: usage[key] + another_usage[key] for key in usage.keys() & another_usage.keys()
                     if isinstance(usage[key], (int, float)) and isinstance(another_usage[key], (int, float))}
        findings = checks.describe(reading.issues)
        if settings.self_check != "none" and reading.issues:
            announce(unique_id, reading.issues, kind="check")
        report = {"model": model, "source": choice.reference, "format": choice.fmt, "task": task,
                  "runtime": runtime,
                  "generations": attempts, "repaired": repaired, "elapsed_seconds": round(time.monotonic() - started, 3),
                  "generation": {"greedy": greedy, "seed": seed, **asdict(settings)}, "usage": usage,
                  "references": [one.to_dict() for one in manifest], "media": transport.metadata(),
                  "videos": list(transport.videos), "notes": list(transport.notes),
                  "findings": [asdict(one) for one in reading.issues]}
        outputs = (reading.text,) + tuple(reading.sections.get(name, "") for name in ALL_FIELDS) + (
            reference_text, reasoning, findings, json.dumps(report, ensure_ascii=False, indent=2))
        memory.keep(unique_id, NODE, outputs, given, references=_snapshots(assets, transport, "native"), fields=ALL_FIELDS)
        progress.finish((findings + "\n\n" if findings else "") + reading.text[-2000:])
        return outputs + (handed,)


NODE_CLASS_MAPPINGS = {"MiniMaxH3QwenRewriter": MiniMaxH3QwenRewriter, "MiniMaxH3QwenRewriterOptions": MiniMaxH3QwenRewriterOptions}
NODE_DISPLAY_NAME_MAPPINGS = {NODE: "MiniMax-H3 Qwen Rewriter", "MiniMaxH3QwenRewriterOptions": "MiniMax-H3 Qwen Rewriter Options"}
