"""Probe local llama.cpp help once per executable revision, without loading a model."""

from __future__ import annotations

import functools
import logging
import os
import re
import subprocess
import threading
import time

from . import runner

log = logging.getLogger(__name__)

AUTO, OFF, ON = "auto", "off", "on"
THINKING = (OFF, AUTO, ON)
HELP_SECONDS = 10.0
POLL_SECONDS = 0.1
_LOCK = threading.Lock()
_FLAG = re.compile(r"(?m)^[ \t]*(?:-(?!-)[\w-]+,[ \t]*)?(--[a-z][a-z0-9-]*)(?=[\s,=]|$)")


def thinking_mode(value: str) -> str:
    if value not in THINKING:
        raise ValueError("captioner_thinking must be 'off', 'auto' or 'on'.")
    return value


def check_interrupt() -> None:
    if runner.interrupted():
        import comfy.model_management as mm

        raise mm.InterruptProcessingException()


@functools.lru_cache(maxsize=32)
def _probe(binary: str, modified: int, size: int) -> frozenset[str]:
    """The stamp invalidates cached help when an executable is replaced in place."""
    process = None
    try:
        check_interrupt()
        process = runner.spawn([binary, "--help"], binary)
        deadline = time.monotonic() + HELP_SECONDS
        while time.monotonic() < deadline:
            check_interrupt()
            try:
                output, errors = process.communicate(timeout=POLL_SECONDS)
            except subprocess.TimeoutExpired:
                continue
            if process.returncode == 0:
                help_text = (output + b"\n" + errors).decode("utf-8", errors="replace")
                return frozenset(_FLAG.findall(help_text))
            break
        log.info("[minimax_h3_rewriter.llama_flags] could not read %s --help", binary)
        return frozenset()
    except OSError:
        log.info("[minimax_h3_rewriter.llama_flags] could not run %s --help", binary)
        return frozenset()
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            finally:
                runner._LIVE.discard(process)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


def supported(binary: str) -> frozenset[str]:
    try:
        path = os.path.realpath(os.path.abspath(binary))
        stamp = os.stat(path)
    except OSError:
        return frozenset()
    with _LOCK:
        check_interrupt()
        return _probe(path, stamp.st_mtime_ns, stamp.st_size)


def reasoning_arguments(binary: str, thinking: str = AUTO) -> list[str]:
    mode = thinking_mode(thinking)
    if mode == AUTO:
        return []
    flags = supported(binary)
    if "--reasoning" not in flags:
        log.warning(
            "[minimax_h3_rewriter.llama_flags] %s cannot set captioner_thinking=%s: "
            "--reasoning is absent from its help. Using the model template default. "
            "Use a supported llama-server beside the captioner, or choose 'auto' "
            "to keep the original behaviour.", os.path.basename(binary), mode,
        )
        return []
    return (["--jinja"] if "--jinja" in flags else []) + ["--reasoning", mode]
