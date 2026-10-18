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

from . import checks, media

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


def _bundle_values(bundle, key) -> list | None:
    """One key of the bundle as a list, or None when reading it failed."""
    try:
        values = bundle.get(key) or []
    except Exception:
        return None
    return list(values) if isinstance(values, (list, tuple)) else [values]


def from_bundle(bundle, as_clip=None) -> tuple[list, int]:
    """``(items, unreadable)`` out of another pack's reference bundle.

    Every step is optional and every failure is counted rather than raised.
    This is the one place in the pack that reads a format it does not own, and
    a bundle that has changed shape should cost a reference, not the run.

    A clip in that bundle is not a VIDEO but its frames: an IMAGE batch under
    "videos", with the soundtrack at the same position under "video_audios".
    Read by value alone it is a picture, and split it is a hundred of them, so
    ``as_clip(frames, sound)`` turns it back into a clip, taking its soundtrack
    along. Without it -- or for a sound whose clip is not frames -- the old
    reading stands and the soundtrack is a sound of its own.
    """
    if not hasattr(bundle, "get"):
        return [], 0

    groups = {key: _bundle_values(bundle, key) for key in BUNDLE_KEYS}
    unreadable = sum(1 for values in groups.values() if values is None)
    groups = {key: values or [] for key, values in groups.items()}

    taken: set[int] = set()
    clips: list = []
    if as_clip is not None:
        tracks = groups["video_audios"]
        for position, value in enumerate(groups["videos"]):
            if kind_of(value) != "image":
                clips.append(value)
                continue
            track = tracks[position] if position < len(tracks) else None
            sound = track if kind_of(track) == "audio" else None
            try:
                clips.append(as_clip(value, sound))
            except Exception as error:
                unreadable += 1
                log.info(
                    "[minimax_h3_rewriter.references] a clip in the bundle could not be "
                    "rebuilt from its frames: %s", error,
                )
                continue
            if sound is not None:
                taken.add(position)
        groups["videos"] = clips
        groups["video_audios"] = [
            None if position in taken else track for position, track in enumerate(tracks)
        ]

    found: list = []
    for key in BUNDLE_KEYS:
        for value in groups[key]:
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
    "alone. Nothing is decoded here: a clip travels as it arrived, and says whether its own "
    "sound was counted, so that sound reaches the generator exactly when the prompt names it."
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

    An entry may carry a fourth element: True when the writer counted a clip's
    own sound as an ``<Audio N>`` of its own. ``video_sounds`` holds that, one
    flag per clip, so 'Reference Slots' puts exactly those sounds on the clip
    sockets -- and the generator numbers them the way the prompt already did.
    """
    bundle = {"pictures": [], "videos": [], "audios": []}
    sources = {group: [] for group in bundle}
    sounds: list[bool] = []
    subjects, subject_sources = [], []
    for entry in entries:
        slot, role, value = entry[:3]
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
        if group == "videos":
            sounds.append(bool(entry[3]) if len(entry) > 3 else False)
    bundle["pictures"].extend(subjects)
    sources["pictures"].extend(subject_sources)
    bundle["sources"] = sources
    bundle["video_sounds"] = sounds
    return bundle


def clip_sounds(items) -> tuple[dict[str, object], str]:
    """``(sounds, note)``: the sound of every clip whose sound is switched on.

    ``items`` carry ``slot``, ``value`` and ``sound`` -- a writer's assets or
    references, whichever it holds. ``sounds`` is keyed by slot. They are read
    before anything is numbered, because a clip with no sound track gets no
    ``<Audio N>`` from the generator -- 'Reference Slots' finds nothing to put
    on its socket -- and so must take none in the prompt either. ``note`` says
    so when it happens, and is empty otherwise: an idle switch is said, not
    refused.
    """
    wanted = [item for item in items if getattr(item, "sound", False)]
    if not wanted:
        return {}, ""
    sounds: dict[str, object] = {}
    silent: list[str] = []
    with media.Workspace(prefix="minimax_h3_clip_sound_") as workspace:
        for item in wanted:
            sound = media.clip_soundtrack(item.value, workspace)
            if sound is None:
                silent.append(f"'{item.slot}'")
            else:
                sounds[item.slot] = sound
    if not silent:
        return sounds, ""
    one = len(silent) == 1
    note = (
        f"{', '.join(silent)} {'has' if one else 'have'} no sound track, so the sound "
        f"switched on for {'it' if one else 'them'} does nothing -- the generator gets "
        f"none from {'it' if one else 'them'} either."
    )
    return sounds, note


def turn_order(entries) -> list[tuple[int, bool]]:
    """Where each reference goes in a writer's turn, a clip's sound in front of it.

    ``entries`` is ``(word, sound)`` per reference in strip order, ``word``
    being its tag -- Picture, Subject, Video or Audio -- and ``sound`` True for
    a clip whose own sound is a reference too. What comes back is
    ``(index, is_sound)`` pairs: such a clip appears twice, its sound first,
    which is the order the generator writes them in -- ``<Audio k>:`` and then
    ``<Video m>:``.

    The generator also numbers every clip's sound before the first standalone
    one, and a writer numbers labels in the order it meets them. So a
    standalone sound ahead of the last sounding clip moves to just after it:
    left where it was, it would take the number the generator gives that
    clip's sound. Everything else keeps the strip's order.
    """
    entries = list(entries)
    last = max(
        (index for index, (word, sound) in enumerate(entries) if word == "Video" and sound),
        default=-1,
    )
    order: list[tuple[int, bool]] = []
    held: list[tuple[int, bool]] = []
    for index, (word, sound) in enumerate(entries):
        if word == "Audio" and index < last:
            held.append((index, False))
            continue
        if word == "Video" and sound:
            order.append((index, True))
        order.append((index, False))
        if index == last:
            order.extend(held)
    return order


def pipeline_notes(task, entries) -> list[str]:
    """What in these references LightX2V's H3 runner refuses, as sentences to say.

    ``entries`` is ``(slot, word, value)`` per switched-on reference, ``word``
    being the tag the writer gives it -- Picture, Subject, Video or Audio.

    Notices, never refusals. ComfyUI's MiniMaxH3ReferenceToVideo takes both of
    these without checking, so a workflow that stays in ComfyUI runs; the
    reference pipeline H3 ships with refuses them, and a video that came out
    wrong is slower to trace back to one reference than a line said before the
    writer starts.

    The sentences name no task: one writer calls it Ref2VA and another Ref2AV,
    and the slot is what the person has to find.
    """
    if checks.normalize(task) != "ref2va":
        return []
    entries = list(entries)

    counts: dict[str, int] = {}
    for _slot, word, _value in entries:
        counts[word] = counts.get(word, 0) + 1

    notes = []
    if checks.audio_only(task, counts):
        notes.append(
            "These references are sounds alone. LightX2V's MiniMax-H3 runner refuses a "
            "reference set with nothing in it to see, and ComfyUI's MiniMaxH3ReferenceToVideo "
            "runs one without checking. Add a picture, a subject or a clip to stay inside what "
            "H3's own pipeline accepts."
        )

    odd = []
    for slot, word, value in entries:
        if word == "Audio":
            continue
        size = media.frame_size(value)
        if size and checks.out_of_shape(*size):
            odd.append(f"'{slot}' ({size[0]}x{size[1]})")
    if odd:
        one = len(odd) == 1
        notes.append(
            f"{', '.join(odd)} {'is' if one else 'are'} outside 1:4 to 4:1. LightX2V's "
            f"MiniMax-H3 runner refuses a reference that narrow or that wide, and ComfyUI's "
            f"MiniMaxH3ReferenceToVideo scales it without checking. Crop "
            f"{'it' if one else 'them'} to stay inside what H3's own pipeline accepts."
        )
    return notes


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
    already frames and goes through as it is.

    A clip's sound takes an ``<Audio N>`` of its own on the far side, numbered
    ahead of every standalone sound, so it reaches its socket when the writer
    counted it -- ``video_sounds`` in the bundle says which -- and then the
    prompt and the generator agree. ``soundtracks`` puts every clip's sound
    there as well; one the writer did not count moves the prompt's labels, and
    the warning says by how much.
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
    paired = forced = 0
    counted = _values(bundle, "video_sounds") if hasattr(bundle, "get") else []

    def place(group: str, word: str, room: int, handle) -> int:
        placed = 0
        for index, value in enumerate(_values(bundle, group) if hasattr(bundle, "get") else []):
            if value is None:
                continue
            if placed == room:
                over[group] += 1
                continue
            line = f"{word}_{placed + 1} <- {named(group, index)}"
            lines.append(handle(placed, index, value, line))
            placed += 1
        return placed

    def picture_or_sound(group):
        def handle(position, _index, value, line):
            slots[group][position] = value
            return line
        return handle

    def video(position, index, value, line):
        nonlocal paired, forced
        writer_counted = index < len(counted) and counted[index] is True
        wanted = writer_counted or soundtracks
        if kind_of(value) == "video":
            frames, sound, detail = clip(value, wanted)
        else:
            frames, sound, detail = value, None, f"{_frame_count(value)} frame(s) as they came"
        slots["videos"][position] = frames
        line = f"{line} ({detail})"
        if 0 < _frame_count(frames) < 5:
            short.append(f"video_{position + 1}")
        if wanted and sound is not None:
            slots["video_audios"][position] = sound
            paired += 1
            if not writer_counted:
                forced += 1
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
    if forced:
        troubles.append(
            f"{forced} clip sound(s) are on video_audio because 'soundtracks' is on, and the "
            f"writer did not count them: MiniMaxH3ReferenceToVideo gives each an <Audio N> of "
            f"its own, numbered before the sounds on audio_1 onwards, so the <Audio N> labels "
            f"in the prompt are now {forced} behind. Switch the clip's sound on in the "
            f"writer's strip instead, which numbers it."
        )

    summary = "\n".join([head, *troubles, *lines])
    return slots, summary, " ".join(troubles)
