"""Building the bundled prompt presets from the public MiniMax-H3 clip set.

The pack ships the presets as two gzipped JSON files and reads them off disk, so
the library window works with no network at all -- a third-party site being down
must never take a node with it. This tool is what makes those two files; it is
run by hand when the collection changes, never at install time and never by a
node.

    python tools/build_presets.py --dataset <folder> [--atlas index.json]

``--dataset`` is a local copy of https://huggingface.co/datasets/ostris/minimax_h3_1k
-- NNNNNN.txt beside NNNNNN.mp4. Everything the presets need is taken from
there: the prompt text verbatim, the frame size and duration from the clip, and
the shot count, the spoken languages and the aspect read back out of the prompt
itself. Those three were checked against the atlas for all 1000 records and
agree exactly, so this tool derives them rather than copying them.

``--atlas`` is the index.json of the H3 Atlas, which is where the two editorial
fields come from: the shooting style and the subject tags. They are somebody's
reading of the collection, not a fact about it, and the credit block written
into both files says so. Without ``--atlas`` the presets are built with the
style and the topics left empty rather than guessed.

ffmpeg has to be on PATH: one frame is cut from the middle of each clip, shrunk
and encoded as WebP. That is a build-time dependency of this file alone -- the
thumbnails ship encoded, and nothing at runtime looks for ffmpeg.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS = os.path.join(ROOT, "minimax_h3_rewriter", "presets")

PROMPTS_FILE = "prompts.json.gz"
THUMBS_FILE = "thumbs.json.gz"

FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")

THUMB_SIDE = 256
THUMB_QUALITY = 72

FRAMES = 1

VIDEO_SOURCES = {
    "huggingface": "https://huggingface.co/datasets/ostris/minimax_h3_1k/resolve/main/{id}.mp4",
    "mirror": "https://hf-mirror.com/datasets/ostris/minimax_h3_1k/resolve/main/{id}.mp4",
}

SHOT = re.compile(r"\[Shot\s+(\d+)\]")
SPOKEN = re.compile(r"<d>\s*\[([A-Za-z ]+)\]")

CREDIT = {
    "prompts": {
        "who": "ostris",
        "what": "the prompts, and the clips they were written for",
        "url": "https://huggingface.co/datasets/ostris/minimax_h3_1k",
    },
    "tags": {
        "who": "H3 Atlas",
        "what": "the shooting-style and subject tags",
        "url": "https://cohub.live/baize/video-altas/w/h3-atlas",
    },
    "thumbnails": {
        "who": "MiniMax-H3 Prompt Rewriter",
        "what": "one frame a clip, cut from the middle of the original and shrunk",
        "url": "",
    },
}


def read_prompt(path: str) -> dict:
    """The three fields of one .txt, split on their own labels.

    The dataset writes exactly what this pack's nodes write -- the field name, a
    colon, the text -- so the split is the same one ``split_fields`` does and
    the record needs no translating anywhere.
    """
    raw = open(path, encoding="utf-8").read()
    found = {}
    for name in FIELDS:
        match = re.search(rf"^{name}:\s*(.*?)(?=^\w+:|\Z)", raw, re.S | re.M)
        found[name] = match.group(1).strip() if match else ""
    return found


def probe(path: str) -> tuple[int, int, float]:
    """Frame size and duration, straight out of the clip."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"]), round(float(data["format"]["duration"]), 2)


def frame(path: str, at: float):
    """One frame, decoded. ``-ss`` before ``-i`` so the seek is the fast one."""
    from PIL import Image

    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", path,
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if out.returncode or not out.stdout:
        raise RuntimeError(f"ffmpeg gave nothing for {os.path.basename(path)} at {at:.2f}s")
    return Image.open(io.BytesIO(out.stdout)).convert("RGB")


def thumbnail(image) -> bytes:
    """A frame shrunk to ``THUMB_SIDE`` on its long side, WebP, aspect kept.

    The collection runs from 512x1184 to 1184x512 and the window shows them in
    one grid, so what has to match is the long side, not the shape.
    """
    from PIL import Image

    width, height = image.size
    scale = THUMB_SIDE / max(width, height)
    small = image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS
    )
    buffer = io.BytesIO()
    small.save(buffer, "WEBP", quality=THUMB_QUALITY, method=6)
    return buffer.getvalue()


def clip_times(seconds: float) -> list[float]:
    """Where the frame is cut: the middle of the clip.

    The middle rather than the opening because that is where the shot has
    settled -- a clip often opens on a fade, and a black square says nothing
    about the prompt.
    """
    return [seconds / 2]


FLAT = 6.0

STEP = 0.4
TRIES = 3


def lively(path: str, at: float, seconds: float):
    """The frame at ``at``, or the nearest moment that is not a fade.

    Walks away from the edge of the clip, since that is where fades live, and
    keeps the liveliest of what it saw rather than the last -- so a clip that is
    dark all through still gets its best frame instead of an arbitrary one. Four
    frames in three thousand needed this when three were taken per clip; taking
    one from the middle avoids most of it, and the walk covers the rest.
    """
    from PIL import ImageStat

    step = -STEP if at > seconds / 2 else STEP
    best = None
    for attempt in range(TRIES):
        moment = min(max(0.0, at + step * attempt), max(0.0, seconds - 0.05))
        image = frame(path, moment)
        spread = ImageStat.Stat(image.convert("L")).stddev[0]
        if best is None or spread > best[0]:
            best = (spread, image)
        if spread >= FLAT:
            break
    return best[1]


def atlas_tags(source: str | None) -> dict:
    """The style and topic tags, by id, or an empty map when not given."""
    if not source:
        return {}
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as answer:
            items = json.loads(answer.read().decode("utf-8"))
    else:
        items = json.loads(open(source, encoding="utf-8").read())
    return {
        item["id"]: {
            "style": item.get("style", ""),
            "styleLabel": item.get("styleLabel", ""),
            "topics": list(item.get("topics") or ()),
            "topicLabels": list(item.get("topicLabels") or ()),
        }
        for item in items
    }


def build(dataset: str, atlas: str | None, limit: int = 0) -> tuple[dict, dict]:
    tags = atlas_tags(atlas)
    ids = sorted(
        os.path.splitext(name)[0]
        for name in os.listdir(dataset)
        if name.endswith(".txt")
    )
    if limit:
        ids = ids[:limit]
    print(f"{len(ids)} prompts, {len(tags)} tagged by the atlas")

    styles: dict[str, str] = {}
    topics: dict[str, str] = {}

    def one(idx: str) -> tuple[dict, str]:
        text = read_prompt(os.path.join(dataset, f"{idx}.txt"))
        clip = os.path.join(dataset, f"{idx}.mp4")
        width, height, seconds = probe(clip)
        shots = len(set(SHOT.findall(text[FIELDS[0]])))
        spoken = sorted(set(SPOKEN.findall(text[FIELDS[0]])))
        tag = tags.get(idx, {})
        record = {
            "id": idx,
            "description": text[FIELDS[0]],
            "soundscape": text[FIELDS[1]],
            "music": text[FIELDS[2]],
            "style": tag.get("style", ""),
            "topics": tag.get("topics", []),
            "aspect": "square" if width == height else ("landscape" if width > height else "portrait"),
            "w": width,
            "h": height,
            "seconds": seconds,
            "shots": shots,
            "langs": spoken,
        }
        at = clip_times(seconds)[0]
        picture = base64.b64encode(thumbnail(lively(clip, at, seconds))).decode("ascii")
        return record, picture

    records: list[dict] = []
    thumbs: dict[str, str] = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for done, (record, picture) in enumerate(pool.map(one, ids), 1):
            records.append(record)
            thumbs[record["id"]] = picture
            tag = tags.get(record["id"], {})
            if tag.get("style"):
                styles[tag["style"]] = tag.get("styleLabel") or tag["style"]
            for key, label in zip(tag.get("topics", ()), tag.get("topicLabels", ())):
                topics[key] = label
            if done % 100 == 0:
                print(f"  {done}/{len(ids)}  {time.time() - started:.0f}s")

    made = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prompts = {
        "version": 1,
        "made_at": made,
        "task": "T2VA",
        "credit": CREDIT,
        "video": VIDEO_SOURCES,
        "styles": dict(sorted(styles.items())),
        "topics": dict(sorted(topics.items())),
        "records": records,
    }
    pictures = {
        "version": 1,
        "made_at": made,
        "format": "webp",
        "long_side": THUMB_SIDE,
        "quality": THUMB_QUALITY,
        "frames": FRAMES,
        "credit": CREDIT,
        "thumbs": thumbs,
    }
    return prompts, pictures


def write(data: dict, name: str) -> str:
    """One file, gzipped.

    Text is the reason for it -- the prompts shrink by better than three to one.
    The thumbnails do not compress, being WebP already; gzip on them buys back
    the third that base64 costs and nothing more, which is why the pictures are
    a file of their own: a window that only wants the words never touches six
    megabytes to get them.
    """
    os.makedirs(PRESETS, exist_ok=True)
    target = os.path.join(PRESETS, name)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(target, "wb", compresslevel=9) as handle:
        handle.write(raw)
    print(f"{name}: {len(raw) / 1024 / 1024:.2f} MB -> {os.path.getsize(target) / 1024 / 1024:.2f} MB")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True, help="folder of NNNNNN.txt beside NNNNNN.mp4")
    parser.add_argument("--atlas", default="", help="H3 Atlas index.json, a path or a URL")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many, for a trial run")
    args = parser.parse_args()

    if not os.path.isdir(args.dataset):
        print(f"no such folder: {args.dataset}")
        return 1
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Pillow is needed to encode the thumbnails: pip install pillow")
        return 1

    prompts, pictures = build(args.dataset, args.atlas or None, args.limit)
    write(prompts, PROMPTS_FILE)
    write(pictures, THUMBS_FILE)
    missing = [record["id"] for record in prompts["records"] if not record["style"]]
    if missing:
        print(f"note: {len(missing)} record(s) carry no style or topics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
