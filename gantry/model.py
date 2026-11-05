"""The data a site's WBS.md gives every Gantry consumer (#8).

Nodes are value objects: frozen, but each holds its metadata as a `dict`, so a
Leaf or Row is unhashable. Key collections by `.key`, never by the node.

`SiteData.links` is complete: it keeps the links whose ends are held out by a
cycle. A consumer that wants the schedulable graph filters by `held_out`.

A Leaf's `deps` holds only its own entries. A Row has no `deps`: its entries,
foreign ones included, are only in `meta["depends_on"]`, and a cross-site
reader applies them to the row's open leaves itself (docs/worksite-page.md).
"""

from dataclasses import dataclass

REPORT_KINDS = (
    "no_issue", "duplicate_issue", "key_collision", "unresolved_ref",
    "malformed_ref", "bad_priority", "cycle", "waits_on_cycle",
    "skipped_line", "no_nodes", "site_unreadable",
    # The optional inputs (#10): gantry/estimates_file.py and gantry/inputs.py.
    "no_estimates_file", "estimates_ignored", "estimates_unreadable",
    "estimates_column_missing", "estimates_cell_bad", "estimates_row_unmatched",
    "estimates_row_duplicate", "est_unparseable", "unestimated", "no_priority",
    "no_model_tiers",
)


def report_order(entry):
    """A report's order: whole-file entries first, then by line, then by kind."""
    return entry.line is not None, entry.line or 0, REPORT_KINDS.index(entry.kind)


# Agent hours in a working day and working days in a week (#10): est: units,
# the page's day figures and its schedule all convert with these.
HOURS_PER_DAY = 6
DAYS_PER_WEEK = 5
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
class Estimate:
    """A leaf's hours (#10): lo_h <= hi_h, both above 0."""
    lo_h: float
    hi_h: float
    source: str  # "est", "estimates_file" or "default"
    est_kind: str  # the leaf's est: class (gantry/est_rules.py)
    effort: str | None  # the estimates file's effort bucket, as written


@dataclass(frozen=True)
class Models:
    """A leaf's model tiers (#10), as written; None when not recorded."""
    thinking: str | None
    checking: str | None
    source: str  # "estimates_file"


@dataclass(frozen=True)
class Totals:
    """A site's totals over the leaves its schedule plans (#10).

    hours sums each leaf's midpoint, hours_lo and hours_hi the ends;
    unestimated leaves count at the default unit. non_time is
    {unit: {"quantity": sum, "leaves": count}} and is in no hour figure.
    """
    hours: float
    hours_lo: float
    hours_hi: float
    unestimated_count: int
    non_time: dict[str, dict[str, int | float]]


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
    return raw.strip()


def meta_scalar(raw: str | None) -> str | None:
    """A raw text's string value; None when missing or a list."""
    if raw is None:
        return None
    value = meta_value(raw)
    return value if isinstance(value, str) else None


def meta_values(raw: str | None) -> list[str]:
    """A raw text as a list of entries: a scalar is one entry, empty is none."""
    if raw is None:
        return []
    value = meta_value(raw)
    if not value:
        return []
    return value if isinstance(value, list) else [value]
