"""The data a site's WBS.md gives every Gantry consumer (#8), as devagent's
portfolio JSON holds it (gantry/portfolio_json.py, #12).

Nodes are value objects: frozen, but each holds its metadata as a `dict`, so a
Leaf or Row is unhashable. Key collections by `.key`, never by the node.

`SiteData.links` is complete: it keeps the links whose ends are held out by a
cycle. A consumer that wants the schedulable graph filters by `held_out`.

A Leaf's `deps` holds only its own entries. A Row has no `deps`: its entries,
foreign ones included, are only in `meta["depends_on"]`, and a cross-site
reader applies them to the row's open leaves itself (docs/worksite-page.md).
That cross-site reader is gantry/crosssite.py (#68): it sets a Leaf's
`waits_on_other_site` and `foreign_targets` and records a ForeignOutcome for
every foreign entry.
"""

from dataclasses import dataclass

REPORT_KINDS = (
    "no_issue", "duplicate_issue", "key_collision", "unresolved_ref",
    "malformed_ref", "foreign_site_absent", "foreign_unresolved", "bad_priority", "cycle", "waits_on_cycle",
    "skipped_line", "no_nodes", "site_unreadable",
    # The optional inputs (#10): gantry/estimates_file.py and gantry/inputs.py.
    "no_estimates_file", "estimates_ignored", "estimates_unreadable", "estimates_no_table",
    "estimates_column_missing", "estimates_cell_bad", "estimates_row_unmatched",
    "estimates_row_duplicate", "est_unparseable", "unestimated", "no_priority",
    "no_model_tiers", "models_from_estimates_file",
    # Attention need (#36): gantry/attention.py.
    "bad_h",
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
class ForeignOutcome:
    """One foreign depends_on entry's resolution in a full build (#68).

    status is "open", "satisfied", "absent" or "unresolved". An open outcome
    has target_key, title and href; a satisfied one target_key; an absent one
    none of them; an unresolved one a reason.
    """
    site: str  # the waiting site
    source_key: str
    source_line: int
    source_is_row: bool
    raw: str
    target_site: str
    target_issue: str
    status: str
    target_key: str | None = None
    target_is_row: bool | None = None
    title: str | None = None
    href: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Leaf:
    key: str
    issue: str | None
    outline: str
    title: str
    glyph: str
    state: str
    open: bool
    priority: str | list[str] | None  # a ParsedValue or a list of them, as devagent parsed it (#83, #12)
    deps: tuple[DepRef, ...]
    # Each value is devagent's parsed shape: a ParsedValue or a list of them
    # (gantry/portfolio_json.py).
    meta: dict[str, str | list[str]]
    epic: str | None  # the top-level ancestor's key
    line: int
    estimate: object = None
    models: object = None
    flags: object = None
    # Set by gantry/crosssite.py on each open leaf in a full build (#68).
    # foreign_targets None: not resolved (a --site build, or a finished
    # leaf); (): resolved, nothing on another site.
    waits_on_other_site: bool = False
    foreign_targets: tuple[ForeignOutcome, ...] | None = None


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
    """A leaf's model tiers (#10, #14), as written; None when not recorded.

    `source` is where the leaf's tiers came from, for the whole leaf: "wbs"
    (the portfolio JSON's `models`, when at least one class is from the
    leaf's own WBS.md key) or "estimates_file" (the estimates file's row).
    `classes` is record-only: each class's source in the JSON ("wbs",
    "project" or None), or None when the tiers came from the row. Tests and
    the expected-leaves snapshots read it; no production code does.
    """
    thinking: str | None
    checking: str | None
    source: str  # "wbs" or "estimates_file"
    classes: tuple[str | None, str | None] | None = None


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
    meta: dict[str, str | list[str]]  # devagent's parsed values, as Leaf.meta


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
    # Whether the portfolio document's schema carries leaf `models` (minor 2
    # and later, #14). No default: every builder states it.
    schema_models: bool


class ParsedValue(str):
    """A metadata string devagent has already parsed (#83).

    gantry/portfolio_json.py stores each string of a node's `meta` as one, and
    each list as a list of them. The accessors below return these shapes as
    they are: no quote or bracket is read a second time (#12 removed the
    raw-text reading with Gantry's own parser). The tag marks where a value
    came from: a str method returns a plain str.
    """

    __slots__ = ()


def meta_scalar(raw: str | list[str] | None) -> str | None:
    """A metadata value's string value; None when missing or a list."""
    if raw is None:
        return None
    return raw if isinstance(raw, str) else None


def meta_values(raw: str | list[str] | None) -> list[str]:
    """A metadata value as a list of entries: a scalar is one entry, empty is none."""
    if not raw:
        return []
    return raw if isinstance(raw, list) else [raw]
