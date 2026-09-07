"""The ten MiniMax-H3 effect embeddings, and where a prompt has to carry one.

These are not keywords. Each file holds a single bf16 tensor of shape
``[N, 5120]`` under the key ``qwen3vl_32b`` -- fifty to a hundred and forty
positions of *already encoded* prompt, dropped straight into the sequence in
place of the token that names them. Nothing here runs a model or loads a
tensor: ComfyUI's own tokenizer does that when it meets ``embedding:name`` in
the text, and everything this module does is put that string in the right place
and say what it will cost.

Three things about that tokenizer decide the shape of this module, and all
three are read out of ``comfy/sd1_clip.py`` rather than assumed:

- The split is ``re.split(r'(?<=\\s)embedding:')``, so a token must either open
  the text or follow whitespace. Glued to the previous character it is not a
  token at all, it is four ordinary words, and nothing says so.
- The test is ``word.startswith("embedding:")`` -- case-sensitive. ``Embedding:``
  is dropped in silence, which is why ``reduce`` was taught not to capitalise
  one and why the self-check looks for it.
- Whatever follows a token, up to the next one, goes through
  ``' '.join(name.split())``. **Its newlines do not survive.** A token at the
  top of an H3 prompt flattens every field separator below it into a single
  space. That is not fatal, but it is silent, and it is the reason placement is
  a choice the user gets to see rather than a constant in here.

Support arrived in ComfyUI 0.33.0. Before that the token was ignored without a
word. Weights are not available at all -- H3 tokenizes with
``disable_weights=True``, so ``(embedding:x:0.8)`` is read as literal text.

Nothing here imports ComfyUI, so all of it can be tested on its own.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from .fields import ALL_FIELDS, body_offset

log = logging.getLogger(__name__)

REPO_ID = "Comfy-Org/MiniMax-H3"
FOLDER = "embeddings"
SUFFIX = ".safetensors"

KEY = "qwen3vl_32b"
WIDTH = 5120

IDENTIFIER = "embedding:"


@dataclass(frozen=True)
class Effect:
    name: str
    tokens: int
    size: int

    @property
    def title(self) -> str:
        return self.name.split("_", 1)[-1].replace("_", " ").capitalize()

    @property
    def file(self) -> str:
        return self.name + SUFFIX

    @property
    def repo_path(self) -> str:
        return f"{FOLDER}/{self.file}"

    @property
    def token(self) -> str:
        return IDENTIFIER + self.name


EFFECTS = (
    Effect("minimaxh3_art_is_explosion", 50, 512120),
    Effect("minimaxh3_blooming_flowers", 123, 1259640),
    Effect("minimaxh3_bullet_time", 94, 962680),
    Effect("minimaxh3_dark_magic", 59, 604280),
    Effect("minimaxh3_fire_breath", 118, 1208440),
    Effect("minimaxh3_four_seasons", 142, 1454200),
    Effect("minimaxh3_kiss_camera", 97, 993400),
    Effect("minimaxh3_spiral_ascent", 131, 1341560),
    Effect("minimaxh3_storm_magic", 137, 1403000),
    Effect("minimaxh3_truman_show", 90, 921720),
)

NAMES = tuple(effect.name for effect in EFFECTS)

# Stored in saved workflows, so fixed for good.
BODY = "start of the description"
TOP = "top of the prompt"
END = "end of the prompt"
PLACEMENTS = (BODY, TOP, END)

_TOKEN = re.compile(
    IDENTIFIER + "(?:" + "|".join(re.escape(name) for name in NAMES) + r")\b",
    re.IGNORECASE,
)

MAX_HEADER = 16 * 1024 * 1024


def by_name(name: str) -> Effect | None:
    for effect in EFFECTS:
        if effect.name == name:
            return effect
    return None


def path_for(directory: str, name: str) -> str:
    """Where one effect belongs -- flat in the embeddings folder.

    Flat is not a preference. ``load_embed`` joins the folder and the name from
    the prompt, so a file one directory down could only be reached by writing
    the directory into the token, and the token is what the pack writes.
    """
    return os.path.join(directory, name + SUFFIX)


def present(directory: str, effect: Effect) -> bool:
    path = path_for(directory, effect.name)
    if not os.path.isfile(path):
        return False
    size = os.path.getsize(path)
    return size == effect.size if effect.size else size > 0


def header(path: str) -> dict | None:
    """The JSON header of a safetensors file, without torch or safetensors.

    Eight bytes of little-endian length, then that much UTF-8 JSON. Reading it
    costs a kilobyte where loading the file costs the file, and it is the only
    way to say "94 tokens" about something the pack never opens otherwise.
    Anything malformed is None: this is a label on a node, not a loader.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            raw = handle.read(8)
            if len(raw) < 8:
                return None
            length = int.from_bytes(raw, "little")
            if length <= 0 or length > MAX_HEADER or 8 + length > size:
                return None
            data = json.loads(handle.read(length).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def shape(path: str) -> tuple[int, ...] | None:
    """The shape of the tensor the H3 encoder would load from this file.

    ``KEY`` first, then the only other tensor in the file -- which is what
    ``load_embed`` falls back to, so this agrees with what actually happens
    rather than with what the file was supposed to contain.
    """
    data = header(path)
    if not data:
        return None

    entry = data.get(KEY)
    if not isinstance(entry, dict):
        others = [
            value for name, value in data.items()
            if name != "__metadata__" and isinstance(value, dict)
        ]
        entry = others[0] if len(others) == 1 else None
    if not isinstance(entry, dict):
        return None

    dims = entry.get("shape")
    if not isinstance(dims, list) or not dims:
        return None
    try:
        return tuple(int(dim) for dim in dims)
    except (TypeError, ValueError):
        return None


def token_count(path: str) -> int | None:
    """How many positions of the prompt this file would take up, or None.

    A one-dimensional tensor is a single position, which is the old
    textual-inversion shape; the H3 files are all two-dimensional.
    """
    dims = shape(path)
    if not dims:
        return None
    if len(dims) == 1:
        return 1 if dims[0] == WIDTH else None
    return dims[0] if dims[-1] == WIDTH else None


def state(directory: str) -> list[dict]:
    """What the dialog draws: the ten, whether each is here, and what it costs.

    ``tokens`` is the file's own count once the file exists and the nominal one
    until then, so the grid says something useful before the download and the
    truth after it. ``foreign`` marks a file that is here under the right name
    but is not an H3 embedding -- a shape this encoder cannot use.
    """
    rows = []
    for effect in EFFECTS:
        path = path_for(directory, effect.name)
        here = os.path.isfile(path)
        counted = token_count(path) if here else None
        rows.append({
            "name": effect.name,
            "title": effect.title,
            "token": effect.token,
            "tokens": counted or effect.tokens,
            "size": effect.size,
            "present": present(directory, effect),
            "foreign": bool(here and counted is None),
        })
    return rows


def missing(directory: str) -> list[Effect]:
    return [effect for effect in EFFECTS if not present(directory, effect)]


def chosen(value) -> tuple[str, ...]:
    """The effects a workflow has selected, in the order this module lists them.

    The widget holds ``{name: true}`` and drops what is off, which is the
    opposite of the two other JSON widgets in this pack -- they store
    ``{name: false}`` because their entries are on by default. Effects are off
    by default, and an empty object has to mean "none" rather than "all ten".

    A list of names is read too. Nothing in the UI writes one, but it is what
    somebody driving the API by hand writes first, and refusing it would be a
    puzzle rather than a rule.
    """
    if isinstance(value, (list, tuple)):
        wanted = {str(item) for item in value}
        return tuple(name for name in NAMES if name in wanted)

    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return ()
    if isinstance(data, list):
        return chosen(data)
    if not isinstance(data, dict):
        return ()
    return tuple(name for name in NAMES if data.get(name) is True)


def clear(text: str) -> str:
    """Take this node's own tokens back out, so a second run does not stack them.

    Only the ten, and only in whole: a token somebody typed for their own
    textual inversion is theirs, and removing it because it sits in the same
    prompt would be this node deciding what else is allowed in the text.
    A capitalised one goes too -- it is one of ours, broken, and leaving it
    behind would leave a word that reads like an effect and is not one.
    """
    text = text or ""
    if not _TOKEN.search(text):
        return text
    text = _TOKEN.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def _joined(names) -> str:
    return " ".join(effect.token for effect in (by_name(name) for name in names) if effect)


def insert(text: str, names, placement: str = BODY) -> tuple[str, str]:
    """Put the chosen tokens into ``text``. Returns the prompt and a note.

    The prompt is otherwise passed through character for character -- no
    re-wrapping, no re-labelling, nothing rebuilt from parsed sections. What a
    writing node produced is what H3 was meant to read.

    The note is empty unless the placement could not be honoured, which happens
    exactly once: a prompt with no field labels has no description to open, and
    the tokens go to the top instead of nowhere.
    """
    text = clear(text)
    block = _joined(names)
    if not block:
        return text, ""
    if not text.strip():
        return block, ""

    note = ""
    if placement == BODY:
        offset = body_offset(text, ALL_FIELDS)
        if offset is None:
            placement = TOP
            note = (
                "this prompt has no field labels, so there is no description to open: "
                f"the tokens went to the {TOP} instead."
            )
        else:
            head, tail = text[:offset], text[offset:]
            # A token glued to the colon is not a token: the tokenizer splits on
            # "embedding:" only where whitespace comes first.
            if head and not head[-1:].isspace():
                head += " "
            if tail and not tail[:1].isspace():
                block += " "
            return head + block + tail, note

    if placement == END:
        return text + "\n\n" + block, note
    return block + "\n\n" + text, note


def fetch(directory: str, effects=None, on_progress=None, on_status=None) -> dict:
    """Download the effects that are not here yet, flat into ``directory``.

    Hand-built tasks rather than ``download.sync_repo``, for one reason:
    ``build_tasks`` keeps the repository's own folder, and these live in
    ``embeddings/`` inside it, so the sync would land them one directory below
    where ``embedding:`` can see them. This is the same shape ``llamacpp`` uses
    for the same reason.

    The built-in transfer is used whatever the options node says. Ten files of
    a megabyte are not what the ``downloader`` choice is for, and the dialog
    that calls this has no options node to ask.

    ``download`` is imported here rather than at the top so that the rest of
    this module -- which is string handling and eight bytes of file header --
    stays importable with no network stack behind it. The self-check imports it
    for the token rules, and that path should not need ``requests``.
    """
    from . import download

    os.makedirs(directory, exist_ok=True)
    wanted = list(effects) if effects is not None else missing(directory)
    wanted = [effect for effect in wanted if not present(directory, effect)]
    if not wanted:
        return {"files": 0, "bytes": 0}

    total = sum(effect.size for effect in wanted)
    download.check_space(directory, total)

    token = download.access_token()
    base = f"{download.endpoint()}/{REPO_ID}/resolve/main"
    transferred = 0

    for effect in wanted:
        if on_status is not None:
            on_status(effect.file)
        task = download.FileTask(
            path=effect.repo_path,
            url=f"{base}/{effect.repo_path}",
            dest=path_for(directory, effect.name),
            size=effect.size,
            already=0,
        )

        def report(position: int, _label: str = "", _name=effect.file) -> None:
            if on_progress is not None:
                on_progress(position, _name)

        # download_task answers with this file's own size, and takes the bytes
        # of every other one as its base -- so the position it reports stays
        # absolute across the ten.
        transferred += download.download_task(task, token, transferred, report)

    log.info(
        "[minimax_h3_rewriter.embeddings] fetched %d of %d effects into %s",
        len(wanted), len(EFFECTS), directory,
    )
    return {"files": len(wanted), "bytes": total}
