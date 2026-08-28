"""Aspect ratios that arrive on a wire instead of off the picker.

The ratio is the one field of a request another node usually knows already:
whatever decided the frame size knows its shape. So the nodes take it on an
input as well -- and what arrives there has to be read rather than trusted.
ComfyUI's own Resolution Selector says '3:4 (Portrait Standard)'; a size node
says 3840x1080; a person types 2.39. All three mean an aspect ratio, and none
of them is spelled the way the task message spells one.

The picker stays the place a person sets it. This is the wire, and the wire
wins when something is connected to it.
"""

from __future__ import annotations

import logging
import re
from fractions import Fraction

from .constants import RESOLUTIONS

log = logging.getLogger(__name__)

PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*[:/xX*×]\s*(\d+(?:\.\d+)?)")

SINGLE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")

TOLERANCE = 0.02

PLAIN = 100

PICKER_TOOLTIP = (
    "Target aspect ratio the rewrite is composed for. It has no socket on purpose: "
    "a ratio arriving from the graph belongs on 'aspect_ratio', which reads the "
    "spellings other nodes use and overrides this while it is connected."
)

TOOLTIP = (
    "Optional, and it overrides the picker while something is connected. Reads "
    "a ratio ('16:9'), a frame size ('3840x1080') or a bare number ('1.78'), and "
    "a label around the pair is fine -- '3:4 (Portrait Standard)' reads as 3:4. "
    "A size within 2% of a listed ratio is called by its name, so 1376x768 "
    "arrives as 16:9 rather than as 43:24."
)


def _value(name: str) -> float:
    width, height = name.split(":")
    return float(width) / float(height)


def _listed(value: float) -> str:
    """The name of the offered ratio this value is, or "" if it is none of them."""
    nearest = min(RESOLUTIONS, key=lambda name: abs(_value(name) - value))
    return nearest if abs(_value(nearest) - value) / value <= TOLERANCE else ""


def _written(width: float, height: float) -> str:
    """A ratio that is none of the offered ones, written so a model can read it.

    Whole numbers small enough to be a ratio somebody meant are kept as they
    were given -- 5:4 is 5:4, not 1.25:1. Anything else is a measurement: a
    frame size reduces by its common divisor, and a number that reduces to
    nothing useful is stated against 1.
    """
    if width.is_integer() and height.is_integer():
        ratio = Fraction(int(width), int(height))
        if ratio.numerator <= PLAIN and ratio.denominator <= PLAIN:
            return f"{ratio.numerator}:{ratio.denominator}"
    return f"{width / height:.2f}:1"


def read(text: str) -> str:
    """One aspect ratio, from whatever shape it arrived in."""
    pair = PAIR.search(text)
    if pair:
        width, height = float(pair.group(1)), float(pair.group(2))
        if width > 0 and height > 0:
            return _listed(width / height) or _written(width, height)

    single = SINGLE.match(text)
    if single and float(single.group(1)) > 0:
        value = float(single.group(1))
        return _listed(value) or f"{value:.2f}:1"

    raise ValueError(
        f"'{text.strip()}' is not an aspect ratio. Write it as a pair -- '16:9' -- as "
        f"the frame it describes -- '3840x1080' -- or as the number itself -- '1.78'. "
        f"A label around the pair is read through: '3:4 (Portrait Standard)' is 3:4."
    )


def resolve(text: object, chosen: str) -> str:
    """The ratio to compose for: what came in on the input, or what was picked.

    ``text`` is whatever the socket delivered, and the socket takes STRING and
    COMBO alike -- so it is stringified rather than assumed, and a node that
    hands over a number is read the same way a person typing one would be.
    """
    given = "" if text is None else str(text).strip()
    if not given:
        return chosen

    ratio = read(given)
    log.info(
        "[minimax_h3_rewriter.aspect] aspect_ratio %r read as %s, over the picker's %s",
        given, ratio, chosen,
    )
    return ratio
