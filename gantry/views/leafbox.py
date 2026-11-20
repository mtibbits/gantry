"""The leaf box rule both pages draw (#170).

The worksite network's node box and the week view's placement box read their
texts and cue classes here: the key, the leaf's own priority and its title
cut and wrapped, the model band, and the slack, state, unest and xsite
classes. The workflow phase row (#173) is the one piece of markup here: both
pages draw the same `g.ph-row`, the worksite inside its node and the week
view inside phase_svg's own `<svg>`, with its tooltip text and legend
swatches. colours() and phases_css() read the two shared sheets.
"""

import re
from typing import NamedTuple

from gantry import render
from gantry.model import Models
from gantry.phases import LOOK_WORDS, LOOKS, PHASE_NAMES

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
PHASES_SHEET = "templates/leaf_phases.css"
PH_CELL = 10
PH_GAP = 3
PH_WIDTH = 5 * PH_CELL + 4 * PH_GAP  # 62
PHASE_LEGEND = "Workflow phase row, left to right: plan, implement, check, merge, wrap up, from each issue's checklist.md"
PHASE_LIMIT = ("The row shows this build machine's copy of each checklist, which may lag an issue running on "
               "another machine.")
PHASE_TEXT = "phases: {runs}"
PHASE_TEXT_UNKNOWN = "phases unknown: {why}"
GLYPH_MARKS = {"stuck": "!", "parked": "P", "blocked": "?"}
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
    return _sheet(COLOURS)


def num(x):
    """An SVG number: one decimal, none when whole."""
    text = f"{x:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _sheet(name):
    text = render.asset(name)
    # It would end the style element that holds it.
    if UNSAFE_STYLE_RE.search(text):
        raise ValueError(f"{name} must not hold </style")
    return render.Markup(text)


def phases_css():
    """The phase row sheet both pages insert into their own <style>."""
    return _sheet(PHASES_SHEET)


def _mark(look, x, y):
    """A cell's mark, the cell's top left at (x, y)."""
    if look == "done":
        return f'<path class="ph-mark" d="M{num(x + 2.2)},{num(y + 5.2)} L{num(x + 4.3)},{num(y + 7.4)} ' \
               f'L{num(x + 7.8)},{num(y + 2.8)}"/>'
    if look == "current":
        return f'<circle class="ph-mark" cx="{num(x + 5)}" cy="{num(y + 5)}" r="2.6"/>'
    if look in GLYPH_MARKS:
        return (f'<text class="ph-t" x="{num(x + 5)}" y="{num(y + 8.3)}" text-anchor="middle">'
                f"{GLYPH_MARKS[look]}</text>")
    if look == "none":
        return f'<line class="ph-mark" x1="{num(x + 2.5)}" y1="{num(y + 5)}" x2="{num(x + 7.5)}" y2="{num(y + 5)}"/>'
    if look == "unknown":
        return (f'<path class="ph-mark" d="M{num(x + 2)},{num(y + 2)} L{num(x + 8)},{num(y + 8)} '
                f'M{num(x + 8)},{num(y + 2)} L{num(x + 2)},{num(y + 8)}"/>')
    return ""  # ahead: the empty box


def _cell(look, x, y):
    return (f'<g class="ph-cell ph-{look}"><rect class="ph-box" x="{num(x)}" y="{num(y)}" width="{PH_CELL}" '
            f'height="{PH_CELL}" rx="1.5"/>{_mark(look, x, y)}</g>')


def _looks(looks):
    """`looks` as a list, if it is five members of LOOKS; else ValueError."""
    if isinstance(looks, str) or len(looks) != len(PHASE_NAMES) or not set(looks) <= set(LOOKS):
        raise ValueError("a phase row is five phase looks")
    return list(looks)


def phase_row(looks, x, y) -> list:
    """The row's markup: `g.ph-row` and its five cells, the first cell's top left at (x, y)."""
    cells = [_cell(look, x + i * (PH_CELL + PH_GAP), y) for i, look in enumerate(_looks(looks))]
    return ['<g class="ph-row">', *cells, "</g>"]


def phase_text(row) -> str:
    """The row as tooltip text: adjacent phases with one look share it."""
    looks = _looks(row.looks)
    if row.why is not None:
        return PHASE_TEXT_UNKNOWN.format(why=row.why)
    if "unknown" in looks:
        raise ValueError("an unknown phase row needs its reason")
    runs = []
    for name, look in zip(PHASE_NAMES, looks):
        if runs and runs[-1][1] == look:
            runs[-1][0].append(name)
        else:
            runs.append(([name], look))
    return PHASE_TEXT.format(runs="; ".join(f"{', '.join(names)} {LOOK_WORDS[look]}" for names, look in runs))


def phase_svg(row) -> str:
    """The row in an `<svg>` of its own, its text as the label and the tooltip."""
    text = render.esc(phase_text(row))
    return (f'<svg class="ph-svg" viewBox="0 0 {PH_WIDTH} {PH_CELL}" width="{PH_WIDTH}" height="{PH_CELL}" '
            f'role="img" aria-label="{text}"><title>{text}</title>{"".join(phase_row(row.looks, 0, 0))}</svg>')


def phase_swatch(look) -> str:
    """One cell of `look`, for the legend; the word beside it names it."""
    if look not in LOOKS:
        raise ValueError(f"not a phase look: {look!r}")
    return (f'<svg class="ph-svg" viewBox="0 0 {PH_CELL} {PH_CELL}" width="{PH_CELL}" height="{PH_CELL}" '
            f'aria-hidden="true"><g class="ph-row">{_cell(look, 0, 0)}</g></svg>')
