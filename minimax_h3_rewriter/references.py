"""Sorting references into one reference per socket, in both directions.

The arithmetic behind ``reference_adapter``, which feeds the writers, and
behind ``reference_slots``, which hands on what a writer numbered. Kept apart
from both so it can be read and tested without ComfyUI in the room -- the same
split the rest of the pack uses, with ``checks`` under the writers and
``fields`` under both.

Everything here is defensive on purpose. One of these inputs takes any type at
all and another reads a bundle format belonging to a different pack, so "this
is not a reference" and "this bundle is not the shape it was" are ordinary
answers rather than faults, and each is counted and reported instead of raised.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MAX_PICTURES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3

BUNDLE_TYPE = "H3_REFS"
BUNDLE_KEYS = ("pictures", "videos", "video_audios", "audios")

KINDS = (("image", "picture(s)"), ("video", "clip(s)"), ("audio", "sound(s)"))


def first(value):
    """The one value behind an input that ``is_input_list`` wrapped in a list.

    Widgets and single sockets are still single things; the flag only means
    they arrive spelled as a list of one. A caller that forgot this would put
    ``[4.0]`` where a number goes, which is the kind of mistake that survives
    all the way to a model.
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value


def kind_of(value) -> str:
    """Picture, clip, sound -- or "" for something that is none of the three.

    Stricter than ``universal.kind_of``, which may be: that one reads values
    off typed sockets, where anything arriving is already one of the three and
    the only question is which. This socket takes any type at all, so "not a
    reference" is a real answer and has to be one. A string that fell through
    as a clip would be described by a captioner and reach the writer as prose
    about nothing.
    """
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return ""
    if hasattr(value, "shape"):
        return "image"
    if hasattr(value, "keys"):
        try:
            return "audio" if "waveform" in value else ""
        except TypeError:
            return ""
    if any(hasattr(value, name) for name in ("get_components", "get_stream_source",
                                             "get_frame_count", "save_to")):
        return "video"
    return ""


def frames_of(image):
    """One image per frame of a batch, each still a batch of one.

    ComfyUI's IMAGE is ``(frames, height, width, channels)`` and a slice of it
    would be three-dimensional, which every consumer downstream would then have
    to special-case. ``image[index : index + 1]`` keeps the batch axis, so each
    frame leaves here the same shape a single loaded picture has.
    """
    try:
        count = int(image.shape[0])
    except (AttributeError, IndexError, TypeError, ValueError):
        return [image]
    if count <= 1:
        return [image]
    return [image[index:index + 1] for index in range(count)]


def unpack(value) -> list:
    """Flatten one collection into the things inside it.

    Two shapes reach the socket and only one of them is ComfyUI's. A node that
    declares ``is_output_list`` produces a real list, which the executor hands
    over already spread out; a node that does not declare anything sends a
    plain Python list as a single value, and it arrives nested. Both are
    ordinary, so both are taken apart -- one level, because a list of lists is
    nobody's reference.
    """
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(item if isinstance(item, (list, tuple)) else [item])
        return found
    return [value]


def from_bundle(bundle) -> tuple[list, int]:
    """``(items, unreadable)`` out of another pack's reference bundle.

    Every step is optional and every failure is counted rather than raised.
    This is the one place in the pack that reads a format it does not own, and
    a bundle that has changed shape should cost a reference, not the run.
    """
    if not hasattr(bundle, "get"):
        return [], 0

    found: list = []
    unreadable = 0
    for key in BUNDLE_KEYS:
        try:
            values = bundle.get(key) or []
        except Exception:
            unreadable += 1
            continue
        if not isinstance(values, (list, tuple)):
            values = [values]
        for value in values:
            if value is None:
                continue
            if kind_of(value):
                found.append(value)
            else:
                unreadable += 1
                log.info(
                    "[minimax_h3_rewriter.references] '%s' in the bundle held a %s, "
                    "which is not a picture, a clip or a sound",
                    key, type(value).__name__,
                )
    return found, unreadable


def sort_out(items: list, split_batches: bool) -> tuple[dict, int, dict]:
    """``(by kind, skipped, over capacity)`` for everything that arrived.

    Arrival order is kept within each kind, because it is the only order there
    is: the strip renumbers references itself once they reach a writer, and
    guessing at a better one here would only make the two disagree.
    """
    sorted_out: dict[str, list] = {kind: [] for kind, _word in KINDS}
    room = {"image": MAX_PICTURES, "video": MAX_VIDEOS, "audio": MAX_AUDIOS}
    over = {kind: 0 for kind, _word in KINDS}
    skipped = 0

    for value in items:
        kind = kind_of(value)
        if not kind:
            skipped += 1
            continue
        pieces = frames_of(value) if (kind == "image" and split_batches) else [value]
        for piece in pieces:
            if len(sorted_out[kind]) < room[kind]:
                sorted_out[kind].append(piece)
            else:
                over[kind] += 1
    return sorted_out, skipped, over


def summarise(sorted_out: dict, skipped: int, over: dict, unreadable: int) -> tuple[str, str]:
    """``(summary, warning)``: what happened, and the part worth interrupting for."""
    lines = [", ".join(f"{len(sorted_out[kind])} {word}" for kind, word in KINDS)]
    troubles = []

    spilled = [f"{over[kind]} {word}" for kind, word in KINDS if over[kind]]
    if spilled:
        troubles.append(
            f"{', '.join(spilled)} arrived past what Ref2VA can hold "
            f"({MAX_PICTURES} pictures, {MAX_VIDEOS} clips, {MAX_AUDIOS} sounds) and are "
            f"not on any output."
        )
    if skipped:
        troubles.append(
            f"{skipped} item(s) on 'items' were not a picture, a clip or a sound and were "
            f"skipped."
        )
    if unreadable:
        troubles.append(f"{unreadable} entry/entries in the bundle could not be read.")

    lines.extend(troubles)
    return "\n".join(lines), " ".join(troubles)


SLOTS_TYPE = "H3_REWRITER_REFERENCES"

SLOTS_OUTPUT_TOOLTIP = (
    "The references this node numbered, in the order its labels count them, for 'MiniMax-H3 "
    "Reference Slots' to hand on to MiniMaxH3ReferenceToVideo. That node numbers references by "
    "the socket they arrive on, so wiring the same assets to it by hand agrees with this "
    "node's labels only until a reference is reordered or switched off.\n\n"
    "Switched-off references are not in it, and it is empty when the task writes from text "
    "alone. Nothing is decoded here: a clip travels as it arrived."
)

_GROUP_OF_ROLE = {"Picture": "pictures", "Video": "videos", "Audio": "audios"}


def slot_bundle(entries) -> dict:
    """A writer's references, grouped the way MiniMaxH3ReferenceToVideo takes them.

    ``entries`` is ``(slot, role, value)`` in the writer's own order, and the
    order within each group is kept, because it is the numbering: the second
    picture here is ``<Picture 2>`` in the prompt and on the far node alike.

    A picture used as a *subject* goes after every picture. The generator has
    no subject socket and calls it ``<Picture N>`` like any other image, while
    the writer never gave it a picture number at all -- so it has to take a
    number no picture in the prompt is using, and the only such numbers are
    the ones past the last of them.
    """
    bundle = {"pictures": [], "videos": [], "audios": []}
    sources = {group: [] for group in bundle}
    subjects, subject_sources = [], []
    for slot, role, value in entries:
        if value is None:
            continue
        if role == "Subject":
            subjects.append(value)
            subject_sources.append(f"{slot} (subject)")
            continue
        group = _GROUP_OF_ROLE.get(role)
        if group is None:
            continue
        bundle[group].append(value)
        sources[group].append(str(slot))
    bundle["pictures"].extend(subjects)
    sources["pictures"].extend(subject_sources)
    bundle["sources"] = sources
    return bundle


def _values(bundle, key) -> list:
    try:
        values = bundle.get(key) or []
    except Exception:
        return []
    return list(values) if isinstance(values, (list, tuple)) else [values]


def _frame_count(value) -> int:
    try:
        return int(value.shape[0])
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0


def fan_out(bundle, soundtracks: bool, clip) -> tuple[dict, str, str]:
    """``(slots, summary, warning)``: a writer's references, one to a socket.

    ``slots`` holds the four groups MiniMaxH3ReferenceToVideo takes, padded
    with None to their socket counts -- an empty socket is skipped there and
    closes up the numbering, which is what a switched-off square did in the
    writer.

    ``clip`` turns a VIDEO into ``(frames, sound, detail)`` and is only called
    for one; a batch of frames that a writer was told to read as a clip is
    already frames and goes through as it is. A clip's sound reaches its
    socket only with ``soundtracks`` on, because it takes an ``<Audio N>`` of
    its own on the far side and moves every standalone sound up by one.
    """
    slots = {
        "pictures": [None] * MAX_PICTURES,
        "videos": [None] * MAX_VIDEOS,
        "video_audios": [None] * MAX_VIDEOS,
        "audios": [None] * MAX_AUDIOS,
    }
    sources = bundle.get("sources") if hasattr(bundle, "get") else None
    if not hasattr(sources, "get"):
        sources = {}

    def named(group: str, index: int) -> str:
        found = _values(sources, group)
        return str(found[index]) if index < len(found) else f"reference {index + 1}"

    lines: list[str] = []
    over = {"pictures": 0, "videos": 0, "audios": 0}
    short: list[str] = []
    paired = 0

    def place(group: str, word: str, room: int, handle) -> int:
        placed = 0
        for index, value in enumerate(_values(bundle, group) if hasattr(bundle, "get") else []):
            if value is None:
                continue
            if placed == room:
                over[group] += 1
                continue
            lines.append(handle(placed, value, f"{word}_{placed + 1} <- {named(group, index)}"))
            placed += 1
        return placed

    def picture_or_sound(group):
        def handle(position, value, line):
            slots[group][position] = value
            return line
        return handle

    def video(position, value, line):
        nonlocal paired
        if kind_of(value) == "video":
            frames, sound, detail = clip(value, soundtracks)
        else:
            frames, sound, detail = value, None, f"{_frame_count(value)} frame(s) as they came"
        slots["videos"][position] = frames
        line = f"{line} ({detail})"
        if 0 < _frame_count(frames) < 5:
            short.append(f"video_{position + 1}")
        if soundtracks and sound is not None:
            slots["video_audios"][position] = sound
            paired += 1
            line += f", its sound on video_audio_{position + 1}"
        return line

    pictures = place("pictures", "picture", MAX_PICTURES, picture_or_sound("pictures"))
    clips = place("videos", "video", MAX_VIDEOS, video)
    sounds = place("audios", "audio", MAX_AUDIOS, picture_or_sound("audios"))

    head = f"{pictures} picture(s), {clips} clip(s), {sounds} sound(s)"
    if paired:
        head += f", {paired} clip sound(s)"

    troubles = []
    spilled = [
        f"{over[group]} {word}"
        for group, word in (("pictures", "picture(s)"), ("videos", "clip(s)"), ("audios", "sound(s)"))
        if over[group]
    ]
    if spilled:
        troubles.append(
            f"{', '.join(spilled)} arrived past what MiniMaxH3ReferenceToVideo takes "
            f"({MAX_PICTURES} pictures, {MAX_VIDEOS} clips, {MAX_AUDIOS} sounds) and are not "
            f"on any output."
        )
    if short:
        troubles.append(
            f"{', '.join(short)} {'has' if len(short) == 1 else 'have'} fewer than 5 frames, "
            f"which MiniMaxH3ReferenceToVideo refuses."
        )
    if paired:
        troubles.append(
            f"{paired} clip sound(s) are on video_audio: MiniMaxH3ReferenceToVideo gives each an "
            f"<Audio N> of its own, numbered before the sounds on audio_1 onwards. The writers "
            f"do not count clip sounds, so the <Audio N> labels in their prompt are now "
            f"{paired} behind."
        )

    summary = "\n".join([head, *troubles, *lines])
    return slots, summary, " ".join(troubles)
