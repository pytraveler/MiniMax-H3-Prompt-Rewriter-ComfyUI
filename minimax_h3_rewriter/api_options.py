"""Typed, non-secret generation and transport options for the API writer."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass

from .api_contract import ContractError

OPTIONS_TYPE = "H3_API_WRITER_OPTIONS"
NODE = "MiniMaxH3ApiWriterOptions"

OPTION_INPUTS = {
    "max_tokens": ("INT", {"default": 4096, "min": 1, "max": 131072, "tooltip": "Output budget per request, bounded by the selected profile. Some models count reasoning against this budget too."}),
    "response_format": (["text", "json_object", "json_schema"], {"default": "text", "tooltip": "Must be supported by the profile. Custom validation requires text."}),
    "temperature": ("FLOAT", {"default": -1.0, "min": -1.0, "max": 2.0, "step": 0.1, "tooltip": "-1 omits the parameter and uses the server default."}),
    "timeout_seconds": ("FLOAT", {"default": 180.0, "min": 1.0, "max": 3600.0, "step": 1.0, "tooltip": "Total deadline per request, including response reading. Cancellation closes our connection; upstream processing may stop later."}),
    "auto_download_guide": ("BOOLEAN", {"default": True, "tooltip": "Fetch the MiniMax guide if missing. A complete custom system prompt skips guide access."}),
    "video_sample_seconds": ("FLOAT", {"default": 1.0, "min": 0.05, "max": 120.0, "step": 0.05, "tooltip": "Requested interval between video samples. The report gives actual timestamps."}),
    "video_max_seconds": ("FLOAT", {"default": 15.0, "min": 0.1, "max": 120.0, "step": 0.1, "tooltip": "Video window from its active trim start, also bounded by target duration and H3's 15-second limit."}),
    "batch_video_fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 1.0, "tooltip": "Clock for an IMAGE batch in the Video role. VIDEO objects and RefMods use their own timestamps."}),
    "audio_sample_rate": ([16000, 24000, 44100, 48000], {"default": 16000, "tooltip": "Rate of mono PCM16 WAV API copies. Original audio is unchanged."}),
    "repair_once": ("BOOLEAN", {"default": False, "tooltip": "Allow at most ONE additional, potentially paid request for repairable H3 findings. Off by default; no transport retries."}),
}


@dataclass(frozen=True)
class ApiWriterOptions:
    max_tokens: int = 4096
    response_format: str = "text"
    temperature: float = -1.0
    timeout_seconds: float = 180.0
    auto_download_guide: bool = True
    video_sample_seconds: float = 1.0
    video_max_seconds: float = 15.0
    batch_video_fps: float = 24.0
    audio_sample_rate: int = 16000
    repair_once: bool = False

    def __post_init__(self):
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 131072:
            raise ContractError("Invalid API max_tokens.")
        if type(self.response_format) is not str or self.response_format not in OPTION_INPUTS["response_format"][0]:
            raise ContractError("Invalid API response format.")
        for name, lower, upper in (("temperature", -1, 2), ("timeout_seconds", 1, 3600),
                                   ("video_sample_seconds", 0.05, 120), ("video_max_seconds", 0.1, 120),
                                   ("batch_video_fps", 1, 240)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
                raise ContractError("Invalid API " + name + ".")
        if self.temperature != -1 and self.temperature < 0:
            raise ContractError("Invalid API temperature.")
        if type(self.auto_download_guide) is not bool:
            raise ContractError("Invalid API guide option.")
        if type(self.repair_once) is not bool:
            raise ContractError("Invalid API repair option.")
        if type(self.audio_sample_rate) is not int or self.audio_sample_rate not in OPTION_INPUTS["audio_sample_rate"][0]:
            raise ContractError("Invalid API audio sample rate.")


def resolve_options(options, **legacy):
    """A connected options value takes precedence over the old scalar inputs."""
    if options is None:
        return ApiWriterOptions(**legacy)
    if type(options) is not ApiWriterOptions:
        raise ContractError("Connect MiniMax-H3 API Writer Options to the options input.")
    return options


class MiniMaxH3ApiWriterOptions:
    @classmethod
    def INPUT_TYPES(cls):
        settings = deepcopy(OPTION_INPUTS)
        repair = settings.pop("repair_once")
        return {"required": settings, "optional": {"repair_once": repair}}

    RETURN_TYPES = (OPTIONS_TYPE,)
    RETURN_NAMES = ("options",)
    FUNCTION = "execute"
    CATEGORY = "MiniMax-H3"
    DESCRIPTION = (
        "Generation and media-copy settings for API Universal Writer. Connect options to the writer; "
        "these settings then come from this node. Endpoints, models and API keys stay in local profiles."
    )

    def execute(self, **settings):
        if set(settings) - OPTION_INPUTS.keys():
            raise ContractError("Unknown API writer option.")
        return (ApiWriterOptions(**settings),)
