"""Data exchanged by the API writer, independent of ComfyUI and HTTP.

References arrive here in strip order, after disabled slots and absent clip
sound have been removed. Original media stay with the caller. This contract
contains metadata only; it never reads a path, downloads a guide or opens media.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from . import checks, guide_prompt
from .references import turn_order

VERSION = 1
ROLES = {"Picture": "image", "Subject": "image", "Video": "video", "Audio": "audio"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


class ContractError(ValueError):
    """Invalid configuration or job; messages never include supplied values."""


def identifier(value) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def model_id(value) -> bool:
    """Public model IDs/aliases, never a URL or an absolute local model path."""
    return (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+/:@-]{0,255}", value) is not None
            and "://" not in value and re.match(r"^[A-Za-z]:/", value) is None
            and all(part not in (".", "..") for part in value.split("/")))


def object_fields(value, allowed, required=()) -> dict:
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ContractError("Invalid or unsupported contract fields.")
    return value


def positive_int(value) -> bool:
    return type(value) is int and value > 0


def finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class ModelCapabilities:
    """Admin-declared model support, not inferred from a model's name."""

    modalities: frozenset[str] = frozenset({"text"})
    formats: frozenset[str] = frozenset({"text"})
    parameters: frozenset[str] = frozenset({"max_tokens", "temperature"})

    def __post_init__(self):
        allowed = {
            "modalities": {"text", "image", "audio", "video"},
            "formats": {"text", "json_object", "json_schema"},
            "parameters": {"max_tokens", "temperature", "seed"},
        }
        for name, choices in allowed.items():
            value = getattr(self, name)
            if not isinstance(value, (list, tuple, set, frozenset)):
                raise ContractError("Invalid model capabilities.")
            if any(not isinstance(item, str) for item in value) or not set(value) <= choices:
                raise ContractError("Invalid model capabilities.")
            object.__setattr__(self, name, frozenset(value))
        if "text" not in self.modalities or "text" not in self.formats or "max_tokens" not in self.parameters:
            raise ContractError("The writer requires text input, text output and an output token limit.")

    def to_dict(self) -> dict:
        return {name: sorted(getattr(self, name)) for name in ("modalities", "formats", "parameters")}

    @classmethod
    def from_dict(cls, data):
        return cls(**object_fields(data, ("modalities", "formats", "parameters")))


@dataclass(frozen=True)
class Generation:
    max_tokens: int = 4096
    temperature: float | None = None
    seed: int | None = None

    def __post_init__(self):
        if not positive_int(self.max_tokens):
            raise ContractError("Invalid output token limit.")
        if self.temperature is not None and (
            not finite_number(self.temperature) or not 0 <= self.temperature <= 2
        ):
            raise ContractError("Invalid temperature.")
        if self.seed is not None and (type(self.seed) is not int or not 0 <= self.seed < 2**63):
            raise ContractError("Invalid seed.")

    def to_dict(self) -> dict:
        return {name: value for name, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class ReferenceSpec:
    """One enabled slot; clip_audio means a sound track actually exists.

    A decoded RefMod may retain its saved description and related slot IDs.
    Neither its original conditioning nor its decoded media are serialized here.
    """

    slot: str
    role: str
    kind: str
    description: str = ""
    instruction: str = ""
    clip_audio: bool = False
    audio_description: str = ""
    refmod: bool = False
    related_slots: tuple[str, ...] = ()

    def __post_init__(self):
        if not identifier(self.slot) or not isinstance(self.role, str) or self.role not in ROLES:
            raise ContractError("Invalid reference slot or role.")
        kinds = {"image", "video"} if self.role == "Video" else {ROLES[self.role]}
        if not isinstance(self.kind, str) or self.kind not in kinds:
            raise ContractError("The reference kind does not match its role.")
        if any(not isinstance(value, str) for value in (self.description, self.instruction, self.audio_description)):
            raise ContractError("Reference descriptions and instructions must be text.")
        if type(self.clip_audio) is not bool or type(self.refmod) is not bool:
            raise ContractError("Invalid reference flags.")
        if self.clip_audio and (self.kind != "video" or self.role != "Video" or self.refmod):
            raise ContractError("Only a connected video can have a separate clip sound track.")
        if not isinstance(self.related_slots, (list, tuple)) or any(not identifier(one) for one in self.related_slots):
            raise ContractError("Invalid related reference slots.")
        object.__setattr__(self, "related_slots", tuple(self.related_slots))


@dataclass(frozen=True)
class ManifestEntry:
    slot: str
    component: str
    role: str
    index: int
    kind: str
    description: str
    instruction: str
    refmod: bool
    related_slots: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.role} {self.index}"

    def to_dict(self) -> dict:
        return dict(asdict(self), label=self.label)


def reference_manifest(task: str, references: tuple[ReferenceSpec, ...]) -> tuple[ManifestEntry, ...]:
    if task == "T2VA":
        return ()
    counts: Counter = Counter()
    entries = []
    if task != "Ref2VA" and any(one.clip_audio for one in references):
        raise ContractError("Separate clip sound labels require Ref2VA.")
    for position, sound in turn_order((one.role, one.clip_audio) for one in references):
        reference = references[position]
        component = "sound" if sound else "media"
        role = "Audio" if sound else reference.role
        kind = "audio" if sound else reference.kind
        description = reference.audio_description if sound else reference.description
        counts[role] += 1
        entries.append(ManifestEntry(reference.slot, component, role, counts[role], kind,
                                     description, reference.instruction, reference.refmod,
                                     reference.related_slots))
    if checks.over_capacity(task, counts):
        raise ContractError("The reference set exceeds H3 capacity.")
    if task == "Ref2VA" and not entries:
        raise ContractError("Ref2VA needs at least one reference.")
    needed = {"I2VA": 1, "FL2VA": 2, "L2VA": 1}.get(task)
    if needed is not None and counts["Picture"] != needed:
        raise ContractError("The picture count does not match the selected H3 task.")
    return tuple(entries)


@dataclass(frozen=True)
class WriterJob:
    """A workflow selects a profile ID, never an endpoint or credential source."""

    profile_id: str
    task: str
    prompt: str = field(repr=False)
    system_prompt: str = field(repr=False)
    duration: float = 5.0
    aspect_ratio: str = "16:9"
    references: tuple[ReferenceSpec, ...] = ()
    previous_context: str = field(default="", repr=False)
    media_mode: str = "native"
    validation: str = "h3"
    response_format: str = "text"
    generation: Generation = field(default_factory=Generation)
    version: int = VERSION
    model: str = ""

    def __post_init__(self):
        if type(self.version) is not int or self.version != VERSION or not identifier(self.profile_id):
            raise ContractError("Unsupported job version or profile ID.")
        if self.model != "" and not model_id(self.model):
            raise ContractError("Invalid selected model ID.")
        if self.task not in guide_prompt.ALL_MODES:
            raise ContractError("Unknown H3 task.")
        if not all(isinstance(text, str) for text in (self.prompt, self.system_prompt, self.previous_context)):
            raise ContractError("Prompts and previous context must be text.")
        if not self.prompt.strip() or not self.system_prompt.strip():
            raise ContractError("The original prompt and effective system prompt must not be empty.")
        if not finite_number(self.duration) or self.duration <= 0:
            raise ContractError("Invalid duration.")
        if not isinstance(self.aspect_ratio, str) or not re.fullmatch(r"[1-9][0-9]{0,3}:[1-9][0-9]{0,3}", self.aspect_ratio):
            raise ContractError("Invalid aspect ratio.")
        if self.media_mode not in ("native", "descriptions") or self.validation not in ("h3", "custom"):
            raise ContractError("Invalid media or validation mode.")
        if self.response_format not in ("text", "json_object", "json_schema"):
            raise ContractError("Unsupported response format.")
        if self.validation == "custom" and self.response_format != "text":
            raise ContractError("Custom validation uses the plain text contract.")
        if not isinstance(self.generation, Generation):
            raise ContractError("Invalid generation options.")
        if not isinstance(self.references, (list, tuple)) or len(self.references) > 12:
            raise ContractError("Invalid reference list.")
        if any(not isinstance(one, ReferenceSpec) for one in self.references):
            raise ContractError("Invalid reference metadata.")
        object.__setattr__(self, "references", tuple(self.references))
        slots = {one.slot for one in self.references}
        if len(slots) != len(self.references):
            raise ContractError("Duplicate reference slots.")
        if any(set(one.related_slots) - slots for one in self.references):
            raise ContractError("A related reference is not in this job.")
        reference_manifest(self.task, self.references)

    @property
    def manifest(self) -> tuple[ManifestEntry, ...]:
        return reference_manifest(self.task, self.references)

    @property
    def notices(self) -> tuple[str, ...]:
        ignored = self.references or self.previous_context
        return ("T2VA ignores connected references and previous reference context.",) if self.task == "T2VA" and ignored else ()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        allowed = set(cls.__dataclass_fields__)
        raw = dict(object_fields(data, allowed, ("profile_id", "task", "prompt", "system_prompt")))
        refs = raw.get("references", [])
        if not isinstance(refs, (list, tuple)):
            raise ContractError("Invalid reference list.")
        raw["references"] = tuple(
            ReferenceSpec(**object_fields(one, ReferenceSpec.__dataclass_fields__, ("slot", "role", "kind")))
            for one in refs
        )
        raw["generation"] = Generation(**object_fields(raw.get("generation", {}), Generation.__dataclass_fields__))
        return cls(**raw)

    def output_schema(self) -> dict:
        names = list(guide_prompt.FIELDS_FOR_MODE[self.task])
        if self.task in guide_prompt.ALIGNMENT_LINE:
            names.insert(0, "alignment")
        return {"type": "object", "properties": {name: {"type": "string", "minLength": 1} for name in names},
                "required": names, "additionalProperties": False}

    def messages(self) -> list[dict]:
        lines = [f"task: {self.task}", f"resolution: {self.aspect_ratio}",
                 f"duration: {guide_prompt.seconds(self.duration)}"]
        if self.previous_context and self.task != "T2VA":
            lines.append("previous_context (text only, no media attached):\n" + self.previous_context)
        if self.manifest:
            lines.append("reference_assets:\n" + json.dumps([one.to_dict() for one in self.manifest], ensure_ascii=False))
            lines.append("reference_transport: " + (
                "descriptions only; no images, video or audio are attached. Use the supplied descriptions as text evidence."
                if self.media_mode == "descriptions" else
                "attachments follow below. Each Video is a sampled sequence of images with timestamps under one label, not a complete video. Only labelled Audio attachments contain sound."))
        if self.response_format != "text":
            lines.append("Return a JSON object matching this schema instead of labelled text:\n" +
                         json.dumps(self.output_schema()))
        lines.append("original_prompt:\n" + self.prompt)
        return [{"role": "system", "content": self.system_prompt},
                {"role": "user", "content": "\n".join(lines)}]


def make_job(*, profile_id: str, prompt: str, task: str = "T2VA", guide: str = "",
             system_prompt: str = "", validation: str | None = None, **options) -> WriterJob:
    """Supply guide text explicitly; a custom system prompt replaces it whole."""
    if not isinstance(system_prompt, str):
        raise ContractError("The system prompt must be text.")
    if validation is not None and validation not in ("h3", "custom"):
        raise ContractError("Invalid validation mode.")
    if system_prompt.strip() and validation is None:
        raise ContractError("Choose h3 or custom validation for a custom system prompt.")
    if task not in guide_prompt.ALL_MODES:
        raise ContractError("Unknown H3 task.")
    duration = options.get("duration", 5.0)
    if not finite_number(duration) or duration <= 0:
        raise ContractError("Invalid duration.")
    if not system_prompt.strip() and (not isinstance(guide, str) or not guide.strip()):
        raise ContractError("Supply the writing guide text or a custom system prompt.")
    effective = system_prompt if system_prompt.strip() else guide_prompt.system_prompt(
        guide, task, duration
    )
    return WriterJob(profile_id=profile_id, prompt=prompt, task=task, system_prompt=effective,
                     validation=validation or "h3", **options)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ApiResult:
    """A completed transport answer; api_text performs H3 parsing and checks."""

    profile_id: str
    model: str
    text: str = field(repr=False)
    reasoning: str = field(repr=False)
    usage: TokenUsage
    elapsed_seconds: float
    manifest: tuple[ManifestEntry, ...]
    notices: tuple[str, ...] = ()
    finish_reason: str = "stop"
