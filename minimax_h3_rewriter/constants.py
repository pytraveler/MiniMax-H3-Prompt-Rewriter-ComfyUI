"""Repository coordinates and fixed choices shared by the rewriter nodes."""

from __future__ import annotations

import sys


BASE_MODEL_REPO = "Qwen/Qwen3.6-27B"
ADAPTER_REPO = "lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA"

ADAPTER_DIR_NAME = "MiniMax-H3-Prompt-Rewriter-LoRA"

ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")
BASE_SKIP_SUFFIXES = (".md", ".gitattributes")

RESOLUTIONS = ("48:9", "32:9", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

DURATION_TYPE = "FLOAT,INT"
DURATION_MIN = 0.1
DURATION_DEFAULT = 10.0
DURATION_STEP = 0.1
DURATION_CEILING = 600.0
DURATION_PROPERTY = "max_duration"
DURATION_PROPERTY_DEFAULT = 30.0

DURATION_LEAD = "Target clip length in seconds; drives shot count and pacing."

DURATION_MENU = (
    "Right-click the node for 'duration': the default value back, or a new upper end for "
    "the widget. It offers 30 seconds until you change it and the server takes up to 600, "
    "because a widget's range is fixed when the node is declared and one number cannot suit "
    "every graph -- MiniMax's own guide is written around clips of a few seconds, while the "
    "stretched pipelines the community has built run well past that. What you set is "
    "remembered with the workflow."
)


def duration_tooltip(lead: str = DURATION_LEAD) -> str:
    """One node's own sentence about duration, with the menu explained after it."""
    return f"{lead}\n\n{DURATION_MENU}"


DURATION_TOOLTIP = duration_tooltip()


def duration_options(tooltip: str = DURATION_TOOLTIP) -> dict:
    """The duration widget's numbers, shared by both node schemas."""
    return {
        "default": DURATION_DEFAULT,
        "min": DURATION_MIN,
        "max": DURATION_CEILING,
        "step": DURATION_STEP,
        "round": DURATION_STEP,
        "tooltip": tooltip,
    }


def duration_widget(tooltip: str = DURATION_TOOLTIP) -> tuple:
    """The v1 declaration: a float widget that an INT link may drive as readily.

    'widgetType' is what tells the frontend which of the two types to draw,
    since the socket carries both.
    """
    return (DURATION_TYPE, {"widgetType": "FLOAT", **duration_options(tooltip)})


QUANTIZATIONS = ("nf4", "int8", "bfloat16", "float16")
ATTN_IMPLEMENTATIONS = ("sdpa", "eager", "flash_attention_2")

MERGE_AUTO = "auto"
MERGE_ON = "on"
MERGE_OFF = "off"
MERGE_LORA = (MERGE_AUTO, MERGE_ON, MERGE_OFF)

RUNTIME_AUTO = "auto"
RUNTIME_WHEEL = "llama-cpp-python"
RUNTIME_BINARY = "llama.cpp"
GGUF_RUNTIMES = (RUNTIME_AUTO, RUNTIME_WHEEL, RUNTIME_BINARY)

OUTPUT_FIELDS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)

REF_OUTPUT_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

MODELS_SUBDIR = "LLM"

SEED_MODULUS = 2 ** 32


def normalize_seed(seed) -> int:
    return int(seed) % SEED_MODULUS


THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

NO_REASONING = "Do not think out loud: give the answer only, with no reasoning and no preamble."


def answer_only(text: str) -> str:
    """The answer without the model's reasoning in front of it.

    Asking for no reasoning is not the same as getting none. Gemma-4's own
    decoder rewrites its thought channel into ``<think>``/``</think>``, and an
    E4B build here writes a full analysis into it even though the prompt primes
    that channel closed -- ComfyUI's own 'Generate Text' node shows the same
    thing on the same checkpoint, so it is the model, not the wiring. A caption
    is one line of a reference block, so the reasoning is cut rather than
    shipped: everything up to the last close tag goes, and an unclosed block --
    which is what a truncated answer leaves behind -- goes with it.
    """
    if THINK_CLOSE in text:
        text = text.rsplit(THINK_CLOSE, 1)[-1]
    elif THINK_OPEN in text:
        text = text.split(THINK_OPEN, 1)[0]
    return text.strip()


def install_command(package: str) -> str:
    """The pip line for *this* interpreter, ready to paste into a terminal.

    "pip install X" is not advice a ComfyUI user can follow: the portable build
    runs an embedded Python that is not on PATH and has no pip launcher, a
    manual install has a venv, and the desktop app has its own environment
    again. Typing the bare command installs the package into whichever Python
    the shell happens to find, and the node keeps reporting it as missing. So
    every "package X is missing" message names sys.executable instead.
    """
    executable = sys.executable or "python"
    if " " in executable:
        executable = f'"{executable}"'
    return f"{executable} -m pip install {package}"
