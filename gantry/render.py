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
import re

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
