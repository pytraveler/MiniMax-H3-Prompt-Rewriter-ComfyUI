"""Draw the card images ComfyUI's template browser shows for this pack.

The browser builds a custom node pack's card from the file name alone and
looks the picture up at ``<name>.jpg`` beside the workflow, so every template
in ``example_workflows/`` needs one image with exactly its own name. A
screenshot of the canvas will not do it: the prompt boxes, notes and previews
are HTML drawn over the canvas and photograph blank, so a screenshot of one of
these templates is mostly empty rectangles.

What is drawn instead is the shape of the workflow -- its chain of nodes, left
to right -- with the number that orders the cards and one line saying what the
template costs to run. Blue is a template that writes text; amber is one that
needs the MiniMax-H3 video weights.

    python tools/template_cards.py          # write every missing card
    python tools/template_cards.py --force  # redraw them all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - the tool is not part of the runtime
    sys.exit("This tool needs Pillow: pip install pillow")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "example_workflows"

WIDTH, HEIGHT = 1024, 576
BACK_TOP = (30, 34, 40)
BACK_BOTTOM = (22, 25, 30)
TITLE = (236, 239, 243)
BODY = (150, 158, 170)
BOX = (54, 60, 69)
BOX_EDGE = (74, 82, 94)
BOX_TEXT = (206, 213, 222)
WIRE = (104, 114, 128)
LIGHT = (74, 144, 196)
HEAVY = (201, 151, 63)

CARDS = [
    {
        "number": "1",
        "name": "1 - Write a prompt",
        "title": "Write a prompt",
        "line": "One line in, a full MiniMax-H3 audio-video description out.",
        "cost": "One 2.6 GB language model. No video weights.",
        "accent": LIGHT,
        "chain": ["your idea", "Prompt Writer", "Prompt Check", "H3 prompt"],
    },
    {
        "number": "2",
        "name": "2 - Rewrite a prompt with the 27B LoRA",
        "title": "Rewrite with the 27B LoRA",
        "line": "The LightX2V adapter this pack is named after, on Qwen3.6-27B.",
        "cost": "One 15.7 GB model. No video weights.",
        "accent": LIGHT,
        "chain": ["your idea", "Prompt Rewriter", "Prompt Check", "H3 prompt"],
    },
    {
        "number": "3",
        "name": "3 - Write a prompt from references",
        "title": "Write from references",
        "line": "The writer describes your pictures and writes from what it saw.",
        "cost": "Two GGUFs, 6 GB together. No video weights.",
        "accent": LIGHT,
        "chain": [("picture 1", "picture 2"), "Universal Writer", "Prompt Check", "H3 prompt"],
    },
    {
        "number": "4",
        "name": "4 - Ready-made prompts",
        "title": "Ready-made prompts",
        "line": "A thousand finished prompts, picked in a browser with their frames.",
        "cost": "Nothing is loaded and nothing is downloaded.",
        "accent": LIGHT,
        "chain": ["Prompt Presets", "Prompt Check", "H3 prompt"],
    },
    {
        "number": "5",
        "name": "5 - Prompt to video",
        "title": "Prompt to video",
        "line": "ComfyUI's text-to-video template with the writer in front of it.",
        "cost": "Needs the MiniMax-H3 weights, tens of gigabytes.",
        "accent": HEAVY,
        "chain": ["your idea", "Prompt Writer", "Prompt Check", "MiniMax-H3", "video"],
    },
    {
        "number": "6",
        "name": "6 - References to video",
        "title": "References to video",
        "line": "Ref2VA with the writer in front: the pictures reach both of them.",
        "cost": "Needs the MiniMax-H3 ref2va weights, tens of gigabytes.",
        "accent": HEAVY,
        "chain": [
            ("picture 1", "picture 2"),
            "Universal Writer",
            "Prompt Check",
            "MiniMax-H3",
            "video",
        ],
    },
]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """A Windows UI face, falling back to whatever Pillow can find."""
    for candidate in (f"C:/Windows/Fonts/{name}", name):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def backdrop() -> Image.Image:
    card = Image.new("RGB", (WIDTH, HEIGHT), BACK_TOP)
    draw = ImageDraw.Draw(card)
    for y in range(HEIGHT):
        t = y / HEIGHT
        draw.line(
            [(0, y), (WIDTH, y)],
            fill=tuple(
                round(a + (b - a) * t) for a, b in zip(BACK_TOP, BACK_BOTTOM)
            ),
        )
    return card


def wide(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=face)
    return right - left


def chain(draw: ImageDraw.ImageDraw, items: list, accent: tuple[int, int, int]) -> None:
    """Boxes wired left to right, sized to fill the width whatever the count."""
    small = font("segoeui.ttf", 19)
    gap, top, height = 26, 306, 62
    span = (WIDTH - 120 - gap * (len(items) - 1)) / len(items)
    for index, item in enumerate(items):
        x = 60 + index * (span + gap)
        stack = item if isinstance(item, tuple) else (item,)
        step = height + 14 if len(stack) > 1 else 0
        first = top - (step * (len(stack) - 1)) // 2
        for row, label in enumerate(stack):
            y = first + row * step
            draw.rounded_rectangle(
                [x, y, x + span, y + height],
                radius=10,
                fill=BOX,
                outline=accent if index else BOX_EDGE,
                width=2 if index else 1,
            )
            draw.text(
                (x + span / 2 - wide(draw, label, small) / 2, y + height / 2 - 12),
                label,
                font=small,
                fill=BOX_TEXT,
            )
        if index + 1 < len(items):
            middle = top + height / 2
            draw.line([x + span + 6, middle, x + span + gap - 8, middle], fill=WIRE, width=2)
            draw.polygon(
                [
                    (x + span + gap - 8, middle),
                    (x + span + gap - 15, middle - 5),
                    (x + span + gap - 15, middle + 5),
                ],
                fill=WIRE,
            )


def card(spec: dict) -> Image.Image:
    image = backdrop()
    draw = ImageDraw.Draw(image)
    accent = spec["accent"]

    draw.rectangle([0, 0, WIDTH, 5], fill=accent)
    draw.rounded_rectangle([60, 58, 128, 126], radius=14, fill=accent)
    number = font("segoeuib.ttf", 40)
    draw.text(
        (94 - wide(draw, spec["number"], number) / 2, 70),
        spec["number"],
        font=number,
        fill=BACK_TOP,
    )

    draw.text((152, 62), spec["title"], font=font("segoeuib.ttf", 42), fill=TITLE)
    draw.text((152, 122), spec["line"], font=font("segoeui.ttf", 22), fill=BODY)

    chain(draw, spec["chain"], accent)

    draw.text((60, 486), spec["cost"], font=font("segoeui.ttf", 21), fill=accent)
    pack = "MiniMax-H3 Prompt Rewriter for ComfyUI"
    small = font("segoeui.ttf", 18)
    draw.text((60, 522), pack, font=small, fill=(96, 104, 116))
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="redraw cards that exist")
    args = parser.parse_args()

    if not WORKFLOWS.is_dir():
        sys.exit(f"no {WORKFLOWS}")

    written = 0
    for spec in CARDS:
        workflow = WORKFLOWS / f"{spec['name']}.json"
        if not workflow.is_file():
            print(f"skipped {spec['name']}: no workflow beside it")
            continue
        target = WORKFLOWS / f"{spec['name']}.jpg"
        if target.exists() and not args.force:
            print(f"kept    {target.name}")
            continue
        card(spec).save(target, "JPEG", quality=92, optimize=True)
        print(f"wrote   {target.name}")
        written += 1

    strays = sorted(
        path.name
        for path in WORKFLOWS.glob("*.json")
        if not (WORKFLOWS / f"{path.stem}.jpg").exists()
    )
    if strays:
        print("\nno card for: " + ", ".join(strays))
    return 0 if not strays else 1


if __name__ == "__main__":
    raise SystemExit(main())
