"""API writing: sections, H3 findings and one explicitly enabled repair."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field, replace

from . import api_config, checks, guide_prompt, repair
from .api_client import ApiClient, ApiError
from .api_contract import ApiResult, ContractError, WriterJob
from .fields import ALL_FIELDS, split_sections


class AnswerError(ValueError):
    """A safe format error without any part of the provider response."""


@dataclass(frozen=True)
class Reading:
    text: str = field(repr=False)
    sections: dict[str, str] = field(repr=False)
    issues: tuple[checks.Issue, ...] = ()


def read_answer(job: WriterJob, text: str) -> Reading:
    if not isinstance(text, str) or not text.strip():
        raise AnswerError("The model returned no usable final text.")
    if job.validation == "custom":
        return Reading(text, {name: "" for name in ALL_FIELDS})
    names = guide_prompt.FIELDS_FOR_MODE[job.task]
    if job.response_format == "text":
        _head, sections = split_sections(text, names, fallback=guide_prompt.BODY_FIELD[job.task])
    else:
        parsed = None
        try:
            candidate = api_config.parse_json(text)
            expected = set(job.output_schema()["required"])
            if isinstance(candidate, dict) and set(candidate) == expected and all(
                isinstance(value, str) and value.strip() for value in candidate.values()
            ):
                parsed = candidate
        except (ValueError, TypeError, RecursionError, OverflowError):
            pass
        if parsed is None:
            raise AnswerError("The JSON answer must contain exactly the requested nonempty string fields.")
        sections = {name: parsed[name] for name in names}
        head = parsed.get("alignment", "")
        text = ((head + "\n\n") if head else "") + "\n\n".join(
            f"{name}: {sections[name]}" for name in names
        )
    having = [checks.TAG_KIND[one.role] for one in job.manifest if one.role in checks.TAG_KIND]
    issues = checks.review(text, sections, names, task=job.task, duration=job.duration, having=having)
    return Reading(text, sections, tuple(issues))


@dataclass(frozen=True)
class Written:
    reading: Reading = field(repr=False)
    result: ApiResult = field(repr=False)
    report: str


def _report(job, results, reading, notes, attempts, elapsed):
    picked = results[0]
    usage = {}
    for name in picked.usage.__dataclass_fields__:
        values = [getattr(one.usage, name) for one in results]
        usage[name] = sum(values) if len(results) == attempts and all(value is not None for value in values) else None
    raw = {"profile_id": picked.profile_id, "model": picked.model, "task": job.task,
           "validation": job.validation, "response_format": job.response_format,
           "requests": attempts, "completed_requests": len(results), "elapsed_seconds": round(elapsed, 3),
           "usage": usage, "references": [one.to_dict() for one in picked.manifest],
           "findings": [asdict(one) for one in reading.issues],
           "notes": list(job.notices) + notes}
    return json.dumps(raw, ensure_ascii=False, indent=2)


def write(job: WriterJob, client: ApiClient, *, repair_once=False, cancelled=None) -> Written:
    """Synchronous CLI/helper entry point; ComfyUI awaits write_async()."""
    running = False
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        pass
    if running:
        raise ContractError("Await write_async() from an asynchronous host.")
    return asyncio.run(write_async(job, client, repair_once=repair_once, cancelled=cancelled))


async def write_async(job: WriterJob, client: ApiClient, *, repair_once=False, cancelled=None) -> Written:
    """One request normally; at most one additional paid call when opted in.

    Transport errors, refusal, truncation and invalid JSON never trigger repair.
    H3 findings are advisory, following the existing pack's checks semantics.
    """
    if not isinstance(job, WriterJob):
        raise ContractError("The API writer requires a validated job.")
    if type(repair_once) is not bool:
        raise ContractError("Invalid repair option.")
    start = time.monotonic()
    attempts = 1
    result = await client.complete_async(job, cancelled=cancelled)
    reading = read_answer(job, result.text)
    results = [result]
    notes = []
    if job.validation == "custom":
        notes.append("Custom validation: final text is passed through; H3 sections and checks are disabled.")
    elif repair_once and repair.fixable(reading.issues):
        names = guide_prompt.FIELDS_FOR_MODE[job.task]
        if repair.hopeless(reading.issues, reading.sections, names):
            notes.append("Repair was skipped: the answer is too far from the H3 format.")
        else:
            second_job = replace(job, prompt=job.prompt + repair.instruct(reading.issues))
            try:
                attempts += 1
                second = await client.complete_async(second_job, cancelled=cancelled)
                results.append(second)
                after = read_answer(job, second.text)
                if repair.better(reading.issues, after.issues):
                    result, reading = second, after
                    notes.append("One repair request improved the findings; its answer was selected.")
                else:
                    notes.append("One repair request made no improvement; the first answer was kept.")
            except ApiError as error:
                if error.code == "cancelled":
                    raise
                notes.append("The single repair request failed (" + error.code + "); the first answer was kept.")
            except AnswerError:
                notes.append("The single repair returned invalid JSON; the first answer was kept.")
    return Written(reading, result, _report(job, results, reading, notes, attempts, time.monotonic() - start))
