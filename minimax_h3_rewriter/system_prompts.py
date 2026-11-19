"""Named system messages shared by the guided writers."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from . import guide_prompt, guides

FIELD = "system_prompt_file"
MAX_BYTES = 256 * 1024
SUFFIX = ".md"
NAME = re.compile(r"\w[\w .-]{0,79}")
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    prefix + digit for prefix in ("COM", "LPT") for digit in "0123456789\u00b9\u00b2\u00b3"
}
_WRITE_LOCK = Lock()


class PromptFileError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def input_spec():
    # A string retains the chosen name when the workflow moves to another server.
    return ("STRING", {"default": "", "tooltip": (
        "System prompt used by the connected guided writer. A nonempty system_prompt on "
        "the writer itself takes precedence. Default guide uses the task's MiniMax guide."
    )})


def root() -> Path:
    return Path(guides.root()).with_name("system_prompts")


def _valid_name(name) -> bool:
    return (isinstance(name, str) and bool(NAME.fullmatch(name))
            and name.rstrip(". ") == name and len(name.encode("utf-8")) <= 240
            and name.partition(".")[0].rstrip().upper() not in RESERVED
            and name.casefold() != "default guide")


def _path(name: str) -> Path:
    if not _valid_name(name):
        raise PromptFileError("Use a name of up to 80 letters, digits, spaces, dots, underscores or hyphens.")
    return root() / (name + SUFFIX)


def files() -> list[str]:
    try:
        return sorted(
            (entry.stem for entry in root().iterdir()
             if entry.suffix == SUFFIX and _valid_name(entry.stem)
             and not entry.is_symlink() and entry.is_file()),
            key=str.casefold,
        )
    except FileNotFoundError:
        return []


def read(name: str) -> tuple[str, str]:
    path = _path(name)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise PromptFileError(f"System prompt file '{name}' must be a regular text file.")
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
    except FileNotFoundError:
        raise PromptFileError(
            f"System prompt file '{name}' is missing. Import it in System prompts or select Default guide.",
            404,
        ) from None
    except OSError:
        raise PromptFileError(f"Could not read system prompt file '{name}'.", 500) from None
    if len(data) > MAX_BYTES:
        raise PromptFileError(f"System prompt file '{name}' is larger than 256 KiB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise PromptFileError(f"System prompt file '{name}' must use UTF-8.") from None
    if not text.strip() or "\0" in text:
        raise PromptFileError(f"System prompt file '{name}' must contain nonempty text.")
    return text, hashlib.sha256(data).hexdigest()


def stamp(system_prompt_file: str | None = "", **_options):
    """Invalidate ComfyUI's upstream Options output when this file changes."""
    if system_prompt_file is None:
        # Linked names are unknown at fingerprint time.
        return float("nan")
    if system_prompt_file:
        try:
            return read(system_prompt_file)[1]
        except PromptFileError:
            return None
    return None


def resolve(inline: str, name: str, node_id=None) -> str:
    if (inline or "").strip():
        return inline
    if not name:
        return ""
    try:
        return read(name)[0]
    except PromptFileError as error:
        from .progress import announce

        announce(node_id, [("warn", str(error))], kind="refusal")
        raise


def save(name: str, text: str, revision: str = "") -> str:
    target = _path(name)
    if not isinstance(text, str) or not text.strip() or "\0" in text:
        raise PromptFileError("Enter a nonempty system prompt.")
    try:
        data = text.encode("utf-8")
    except UnicodeError:
        raise PromptFileError("The system prompt must use UTF-8.") from None
    if len(data) > MAX_BYTES:
        raise PromptFileError("The system prompt is larger than 256 KiB.")
    new_revision = hashlib.sha256(data).hexdigest()
    with _WRITE_LOCK:
        try:
            current = read(name)[1]
        except PromptFileError as error:
            if error.status != 404:
                raise
            current = ""
        if current != revision:
            raise PromptFileError(
                f"'{name}' already exists or has changed. Reopen it before saving, or choose a new name.",
                409,
            )
        if current == new_revision:
            return current
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=target.parent, prefix=".prompt-", delete=False) as draft:
                temporary = Path(draft.name)
                draft.write(data)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return new_revision


def template(task: str, qwen: bool, auto_download: bool) -> str:
    if task not in guide_prompt.ALL_MODES:
        raise PromptFileError("Choose a MiniMax-H3 task for the default guide.")
    guide = guides.text(guide_prompt.GUIDE_FOR_MODE[task], auto_download)
    if qwen:
        from . import qwen_prompt

        return qwen_prompt.system(guide, task)
    return guide_prompt.system_prompt(guide, task, duration=None)
