"""The data a site's WBS.md gives every Gantry consumer (#8).

Nodes are value objects: frozen, but each holds its metadata as a `dict`, so a
Leaf or Row is unhashable. Key collections by `.key`, never by the node.

`SiteData.links` is complete: it keeps the links whose ends are held out by a
cycle. A consumer that wants the schedulable graph filters by `held_out`.
"""

from dataclasses import dataclass

REPORT_KINDS = (
    "no_issue", "duplicate_issue", "key_collision", "unresolved_ref",
    "malformed_ref", "bad_priority", "cycle", "waits_on_cycle",
    "skipped_line", "no_nodes", "site_unreadable",
)
# devagent's STATE_GLYPHS (scripts/lib/wbs-parser.py).
STATE_GLYPHS = {" ": "pending", "x": "done", "-": "skipped", "!": "stuck",
                "~": "in_progress", "?": "blocked_external", "P": "parked"}
# A node with one of these glyphs is finished; any other glyph is open.
FINISHED_GLYPHS = frozenset("x-")


@dataclass(frozen=True)
class DepRef:
    raw: str
    kind: str  # "local", "foreign" or "malformed"
    site: str | None
    issue: str | None


@dataclass(frozen=True)
class Leaf:
    key: str
    issue: str | None
    outline: str
    title: str
    glyph: str
    state: str
    open: bool
    priority: str | None  # the raw text
    deps: tuple[DepRef, ...]
    meta: dict[str, str]  # raw value texts
    epic: str | None  # the top-level ancestor's key
    line: int
    estimate: object = None
    models: object = None
    flags: object = None


@dataclass(frozen=True)
class Row:
    key: str
    issue: str | None
    outline: str
    title: str
    glyph: str
    state: str
    line: int
    parent: str | None  # the parent row's key
    meta: dict[str, str]


@dataclass(frozen=True)
class ReportEntry:
    site: str
    line: int | None  # None: the whole file
    kind: str
    message: str


@dataclass(frozen=True)
class SiteData:
    leaves: tuple[Leaf, ...]
    rows: tuple[Row, ...]
    # (prerequisite key, dependent key), by the dependent's then the
    # prerequisite's position in WBS.md.
    links: tuple[tuple[str, str], ...]
    held_out: tuple[str, ...]
    report: tuple[ReportEntry, ...]


def _strip_quotes(s):
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def meta_value(raw: str) -> str | list[str]:
    """A raw metadata text as devagent reads it: a list or a string."""
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return raw[1:].strip()
        return [_strip_quotes(x.strip()) for x in raw[1:end].split(",") if x.strip()]
    if raw[:1] in ("\"", "'"):
        close = raw.find(raw[0], 1)
        return raw[1:] if close == -1 else raw[1:close]
    return raw


def meta_values(raw: str | None) -> list[str]:
    """A raw text as a list of entries: a scalar is one entry, empty is none."""
    if raw is None:
        return []
    value = meta_value(raw)
    if not value:
        return []
    return value if isinstance(value, list) else [value]
