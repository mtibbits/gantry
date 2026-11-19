"""The leaf box rule both pages draw (#170).

The worksite network's node box and the week view's placement box read their
texts and cue classes here: the key, the leaf's own priority and its title
cut and wrapped, the model band, and the slack, state, unest and xsite
classes. No page markup, only the values both pages place; colours() alone
reads a file, the cue colour sheet.
"""

import re
from typing import NamedTuple

from gantry import render
from gantry.model import Models

KEY_MAX = 16
PRIORITY_MAX = 6
TITLE_MAX = 40
TITLE_WIDTH = 25
TITLE_LINES = 2
MODEL_MAX = 9
BAND_THINK = "think"
BAND_CHECK = "check"
BAND_LABELS = (BAND_THINK, BAND_CHECK)
NO_MODEL = "none"
XSITE_CLASS = "xsite"
ELLIPSIS = "\u2026"
COLOURS = "templates/leaf_colours.css"
UNSAFE_STYLE_RE = re.compile(r"</style", re.IGNORECASE)


def cut(s, n):
    """s at most n characters long; truncate before escaping."""
    return s if len(s) <= n else s[:n - 1].rstrip() + ELLIPSIS


def wrap(text, width, lines):
    out, cur = [], ""
    for word in text.split():
        while len(word) > width:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:width])
            word = word[width:]
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][:width - 1].rstrip() + ELLIPSIS
    return out


def model_tiers(models):
    """[thinking, checking], or None when the leaf records neither."""
    if models is None:
        return None
    if not isinstance(models, Models):
        raise TypeError(f"a leaf's models is a {type(models).__name__}, not Models")
    found = [t if isinstance(t, str) and t else None for t in (models.thinking, models.checking)]
    return found if any(found) else None


class Box(NamedTuple):
    key: str
    priority: str | None  # None only when the leaf's priority is None
    title_lines: list
    band: tuple | None  # ((text, class), (text, class)), or None with no models


def box(key, priority, title, tiers) -> Box:
    """The box's texts for a leaf: tiers is model_tiers' value."""
    band = None
    if tiers is not None:
        band = tuple((f"{label} {cut(tier if tier is not None else NO_MODEL, MODEL_MAX)}", render.model_class(tier))
                     for label, tier in zip(BAND_LABELS, tiers))
    return Box(cut(key, KEY_MAX), cut(priority, PRIORITY_MAX) if priority is not None else None,
               wrap(cut(title, TITLE_MAX), TITLE_WIDTH, TITLE_LINES), band)


def box_classes(sclass, sc, unestimated, xsite, between=()) -> list:
    """The box's cue classes in the worksite's order; between sits after the state class."""
    return ([c for c in (sclass, sc) if c is not None] + list(between)
            + ([render.UNESTIMATED_CLASS] if unestimated else []) + ([XSITE_CLASS] if xsite else []))


def colours():
    """The cue colour sheet both pages insert into their own <style>."""
    text = render.asset(COLOURS)
    # It would end the style element that holds it.
    if UNSAFE_STYLE_RE.search(text):
        raise ValueError(f"{COLOURS} must not hold </style")
    return render.Markup(text)
