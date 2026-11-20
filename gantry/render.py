"""Page render helpers: escaping, the template fill, the JSON embedding and the
fixed class sets (#9).

Every record-derived string reaches a page through `esc`, through `fill`
(which escapes every value that is not `Markup`) or through `embed_json`. A
class name is never built from a record value: it is looked up in a fixed set,
with a fallback. docs/worksite-page.md states the rules.
"""

import functools
import html
import json
import math
import re
from fractions import Fraction

from gantry.est_rules import classify_est
from gantry.roots import PACKAGE_KEY, package_root, package_roots, read_bytes_confined


@functools.cache
def asset(name: str) -> str:
    """A packaged page asset's text (a template or a script), read as UTF-8
    bytes under the package root, once per process."""
    path = package_root().path / name
    return read_bytes_confined(package_roots(), path, key=PACKAGE_KEY).decode("utf-8")


class Markup(str):
    """A fragment the page builder made itself, from escaped parts: fill inserts it as is."""


def esc(x) -> str:
    """Text for element content or a double-quoted attribute value."""
    return html.escape(str(x), quote=True)


def stamp_html(stamp: str):
    """The build stamp's paragraph, or nothing when there is no stamp."""
    return Markup(f'<p class="label stamp">{esc(stamp)}</p>') if stamp else ""


PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def fill(template: str, **values) -> str:
    """Replace each {{NAME}} in one pass; a value is never scanned again."""
    used = set()

    def one(m):
        name = m.group(1)
        if name not in values:
            raise ValueError(f"template placeholder {name} has no value")
        used.add(name)
        value = values[name]
        return value if isinstance(value, Markup) else esc(value)

    out = PLACEHOLDER_RE.sub(one, template)
    for name in values:
        if name not in used:
            raise ValueError(f"template value {name} is not used")
    return out


def embed_json(obj) -> Markup:
    """JSON for a <script type="application/json"> block: no <, > or & survives."""
    text = json.dumps(obj, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


MODEL_CLASSES = {"haiku": "m-haiku", "sonnet": "m-sonnet", "opus": "m-opus", "fable": "m-fable"}
MODEL_FALLBACK = "m-other"
STATE_CLASSES = {
    "pending": "st-pending",
    "in_progress": "st-wip",
    "stuck": "st-stuck",
    "blocked_external": "st-blocked",
    "parked": "st-parked",
}
STATE_FALLBACK = "st-other"
SLACK_CLASSES = ("crit", "near", "free")


def _lookup(table, value, fallback):
    # An exact str only: a list or None never reaches the dict.
    return table.get(value, fallback) if type(value) is str else fallback


def model_class(tier) -> str:
    return _lookup(MODEL_CLASSES, tier, MODEL_FALLBACK)


def state_class(state) -> str:
    return _lookup(STATE_CLASSES, state, STATE_FALLBACK)


def slack_class(slack: int) -> str:
    """crit at slack 0, near at 1 to 3, free above."""
    if slack == 0:
        return "crit"
    return "near" if slack <= 3 else "free"


# A leaf scheduled at the default unit, and the schedule's marks for a leaf
# whose own priority is P1 or P2, or P0 (#10).
UNESTIMATED_CLASS = "unest"
PRIORITY_MARKS = ("pr-hi", "pr-p0")
CLASS_TOKENS = frozenset(
    [*MODEL_CLASSES.values(), MODEL_FALLBACK, *STATE_CLASSES.values(), STATE_FALLBACK, *SLACK_CLASSES,
     UNESTIMATED_CLASS, *PRIORITY_MARKS]
)


# The week view's tables (#38), outside CLASS_TOKENS: the worksite script
# reads no class from them, and the week script reads no class from data.
# The week view's leaf box also carries the slack, state, model and unest
# classes above (#170), by the worksite's rule in gantry/views/leafbox.py.
TAG_CLASSES = {"scored": "tag-scored", "measured": "tag-measured", "declared": "tag-declared",
               "estimated": "tag-estimated"}
TAG_FALLBACK = "tag-other"
TIER_CLASSES = {"p0": "tier-p0", "started": "tier-started", "fair_share": "tier-fair"}
TIER_FALLBACK = "tier-other"
# One per planner.UNPLANNED_REASONS (a test pins the keys); no planner import.
REASON_CLASSES = {
    "blocked_externally": "why-blocked", "parked": "why-parked", "waits_on_cycle": "why-cycle",
    "cross_site_cycle": "why-cross-cycle", "waits_on_site_absent": "why-site-absent",
    "waits_on_unresolved": "why-unresolved", "waits_on_unplanned": "why-waits", "beyond_horizon": "why-beyond",
}
REASON_FALLBACK = "why-other"
WEEK_CLASSES = frozenset([*TAG_CLASSES.values(), TAG_FALLBACK, *TIER_CLASSES.values(), TIER_FALLBACK,
                          *REASON_CLASSES.values(), REASON_FALLBACK])


def tag_class(tag) -> str:
    return _lookup(TAG_CLASSES, tag, TAG_FALLBACK)


def tier_class(tier) -> str:
    return _lookup(TIER_CLASSES, tier, TIER_FALLBACK)


def reason_class(reason) -> str:
    return _lookup(REASON_CLASSES, reason, REASON_FALLBACK)


def num(x) -> str:
    """An SVG number: one decimal, none when whole."""
    text = f"{x:.1f}"
    return text[:-2] if text.endswith(".0") else text


def hundredths(x) -> str:
    """x rounded once, half up, to two decimals from its shortest decimal form,
    trailing zeros dropped: 2.25, 2, 3.03."""
    exact = Fraction(repr(float(x)))
    cents = math.floor(abs(exact) * 100 + Fraction(1, 2))
    sign = "-" if exact < 0 and cents else ""
    return (sign + f"{cents // 100}.{cents % 100:02d}").rstrip("0").rstrip(".")


def hours_text(hours) -> str:
    """An hour figure as the week view and #33 write it: 135 minutes is "2.25 h".

    On whole minutes it equals rounding the minutes to hundredths of an hour;
    a fair-share target, an exact fraction, rounds the same way (3.025 is "3.03 h").
    """
    return hundredths(hours) + " h"


def non_time_text(raw):
    """A non-time est: value as text ("2.5 pr"), or None."""
    value = classify_est(raw)
    return f"{value.number:g} {value.unit}" if value.kind == "non_time" else None


def unestimated_text(raw) -> str:
    """An unestimated leaf's label: "unestimated (3 pr)" with a non-time est:, else "unestimated"."""
    value = non_time_text(raw)
    return f"unestimated ({value})" if value is not None else "unestimated"
