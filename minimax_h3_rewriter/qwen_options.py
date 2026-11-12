"""Options for the full Qwen rewriters, independent of the older LoRA nodes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

from . import devices, library

OPTIONS_TYPE = "H3_QWEN_REWRITER_OPTIONS"


@dataclass(frozen=True)
class QwenOptions:
    max_new_tokens: int = 16384
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    repetition_penalty: float = 1.0
    thinking: bool = True
    context_size: int = 32768
    quantization: str = "bfloat16"
    attn_implementation: str = "sdpa"
    device: str = "auto"
    gpu_layers: int = -1
    llama_backend: str = "auto"
    auto_download: bool = True
    downloader: str = "built-in"
    image_max_side: int = 1024
    video_max_frames: int = 8
    video_sample_seconds: float = 1.0
    video_max_seconds: float = 15.0
    batch_video_fps: float = 24.0
    self_check: str = "all"
    repair_once: bool = False
    prompt_file: str = "global"

    def __post_init__(self):
        for name, low, high in (("max_new_tokens", 1, 131072), ("context_size", 1024, 262144),
                                ("top_k", 0, 1000), ("gpu_layers", -1, 999),
                                ("image_max_side", 64, 4096), ("video_max_frames", 1, 64)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid Qwen option: {name}.")
        for name, low, high in (("temperature", 0, 2), ("top_p", 0, 1),
                                ("repetition_penalty", 0.1, 2), ("video_sample_seconds", 0.05, 120),
                                ("video_max_seconds", 0.1, 120), ("batch_video_fps", 1, 240)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid Qwen option: {name}.")
        for name in ("thinking", "auto_download", "repair_once"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Invalid Qwen option: {name}.")
        if self.quantization not in ("bfloat16", "float16", "nf4", "int8"):
            raise ValueError("Choose bfloat16, float16, nf4 or int8 for SFT weights.")
        if self.attn_implementation not in ("sdpa", "eager", "flash_attention_2"):
            raise ValueError("Invalid Qwen attention implementation.")
        if self.self_check not in ("all", "none") or not isinstance(self.prompt_file, str):
            raise ValueError("Invalid Qwen prompt checking or library options.")
        if self.max_new_tokens >= self.context_size:
            raise ValueError("context_size must leave room for the input as well as max_new_tokens.")
        if self.llama_backend not in ("auto", "cuda", "vulkan", "cpu") or self.downloader not in ("built-in", "huggingface_hub"):
            raise ValueError("Invalid Qwen runtime or downloader.")
        if not isinstance(self.device, str):
            raise ValueError("Invalid Qwen device.")


def resolve_options(value=None) -> QwenOptions:
    if value is None:
        return QwenOptions()
    if isinstance(value, QwenOptions):
        return value
    raise ValueError("Connect MiniMax-H3 Qwen Rewriter Options to options.")


class MiniMaxH3QwenRewriterOptions:
    @classmethod
    def INPUT_TYPES(cls):
        defaults = asdict(QwenOptions())
        specs = {
            "max_new_tokens": ("INT", {"min": 1, "max": 131072, "tooltip": "Includes thinking tokens. LightX2V uses 16384; an unfinished thought has no final prompt."}),
            "temperature": ("FLOAT", {"min": 0.0, "max": 2.0, "step": 0.05}),
            "top_p": ("FLOAT", {"min": 0.0, "max": 1.0, "step": 0.01}),
            "top_k": ("INT", {"min": 0, "max": 1000}),
            "repetition_penalty": ("FLOAT", {"min": 0.1, "max": 2.0, "step": 0.01}),
            "thinking": ("BOOLEAN", {"tooltip": "Uses this writer's own Qwen thinking template. Applies to both SFT and GGUF."}),
            "context_size": ("INT", {"min": 1024, "max": 262144, "step": 1024, "tooltip": "Input plus output budget. A request that exceeds it stops before generation."}),
            "quantization": (["bfloat16", "float16", "nf4", "int8"], {"tooltip": "SFT only. GGUF precision is determined by the selected file."}),
            "attn_implementation": (["sdpa", "eager", "flash_attention_2"], {"tooltip": "SFT only. Optional attention backends must already be installed."}),
            "device": (devices.choices(), {}),
            "gpu_layers": ("INT", {"min": -1, "max": 999, "tooltip": "GGUF only; -1 offloads all layers."}),
            "llama_backend": (["auto", "cuda", "vulkan", "cpu"], {"tooltip": "GGUF runtime backend."}),
            "auto_download": ("BOOLEAN", {"tooltip": "Download missing model files, runtime and official guides. Existing files are reused."}),
            "downloader": (["built-in", "huggingface_hub"], {}),
            "image_max_side": ("INT", {"min": 64, "max": 4096, "step": 64}),
            "video_max_frames": ("INT", {"min": 1, "max": 64}),
            "video_sample_seconds": ("FLOAT", {"min": 0.05, "max": 120.0, "step": 0.05}),
            "video_max_seconds": ("FLOAT", {"min": 0.1, "max": 120.0, "step": 0.1}),
            "batch_video_fps": ("FLOAT", {"min": 1.0, "max": 240.0, "step": 1.0}),
            "self_check": (["all", "none"], {}),
            "repair_once": ("BOOLEAN", {"tooltip": "Allow one additional generation to repair findings. Both attempts appear in the report."}),
            "prompt_file": (library.files(), {"tooltip": "Saved prompt collection used by this writer's library."}),
        }
        return {"required": {name: (kind, {**options, "default": defaults[name]})
                             for name, (kind, options) in specs.items()}}

    RETURN_TYPES = (OPTIONS_TYPE,)
    RETURN_NAMES = ("options",)
    FUNCTION = "execute"
    CATEGORY = "MiniMax-H3"

    def execute(self, **values):
        return (QwenOptions(**values),)
