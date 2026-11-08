"""The portfolio file: an installation's private Markdown file of the
operator's business requirements and settings (#22). docs/portfolio-file.md
states the rules.

read_portfolio_file reads and checks the file alone and never raises on its
content; check_portfolio adds the checks that need the installation (the site
list and, when given, each site's issue: values). A consumer prints
Portfolio.report followed by check_portfolio's entries, and nothing else:
Portfolio.brs.report is a subset of Portfolio.report, relabelled the same way.

Every `line` is a 1-based file line; fence_spans' indices are 0-based. No
entry holds a path: the file is always "the portfolio file". gantry build does
not call this module yet (#23 or #61 adds the read).
"""

import json
import math
import os
import re
import stat
import tomllib
from dataclasses import dataclass, fields, replace
from datetime import date, datetime

from gantry import requirements
from gantry.config import ID_TOKEN_RE, describe, error_reason
from gantry.requirements import FENCE_RE, RequirementSet, fence_spans, parse_id, parse_requirements
from gantry.sites import shown

PORTFOLIO_LABEL = "the portfolio file"
SECTIONS = ("business requirements", "settings")  # section None: the whole file (portfolio_unreadable only)
# An ATX heading, matched on the line with its trailing spaces and tabs removed,
# outside fences only. Greedy throughout, so it is linear on a long line.
HEADING_RE = re.compile(r"(?P<hashes>#{1,6})[ \t]+(?P<text>.*)")  # fullmatch
BR_HEADING, SETTINGS_HEADING = "Business requirements", "Settings"  # level 2, matched exactly
_BOM = "\N{BYTE ORDER MARK}"

TOML_AT_RE = re.compile(r" \(at line (?P<line>[1-9][0-9]*), column [0-9]+\)$")  # search
TOML_END = " (at end of document)"
PORTFOLIO_VERSION = 1
BARE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]+")  # fullmatch; else the segment is a JSON string
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

HHMM_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")  # fullmatch
OUTLINE_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+")  # fullmatch: an outline position, such as 3.2.1

REPORT_KINDS = ("portfolio_unreadable", "no_br_section", "no_settings", "duplicate_section", "settings_invalid",
                "bad_version", "unknown_key", "undated_allocation", "bad_allocation", "bad_value",
                "duplicate_allocation_date", "capacity_missing", "reserved_exceeds_capacity",
                "undefined_priority", "misplaced_priority", "bad_milestone", "unknown_project", "milestone_no_leaf")
ADVISORY_KINDS = ("capacity_missing",)
CARRIED_KINDS = tuple(k for k in requirements.REPORT_KINDS if k != "unreadable")
_KIND_ORDER = {kind: i for i, kind in enumerate(REPORT_KINDS + CARRIED_KINDS)}
_SECTION_ORDER = {None: 0, SECTIONS[0]: 1, SECTIONS[1]: 2}

# --- the key registry ----------------------------------------------------------

REQUIRED = "required"


@dataclass(frozen=True)
class SettingKey:
    path: str  # "capacity.block_min_hours", "allocation[].from", "site.<project>.serves"
    rule: str  # bad_value's text: "must be {rule}, not {got}; {fallback}"
    default: object  # REQUIRED, None (not set), () (empty) or the value
    read_by: str  # the issue that reads it


_INT_0 = "an integer, 0 or more"
_INT_1 = "an integer above 0"
SETTINGS_KEYS = (
    SettingKey("portfolio_version", "the integer 1", REQUIRED, "#22"),
    SettingKey("allocation", "an array of tables ([[allocation]])", (), "#23, Epic #5"),
    SettingKey("allocation[].from", "a TOML local date (YYYY-MM-DD)", REQUIRED, "#23, Epic #5"),
    SettingKey("allocation[].shares", "a table of project name to a number, 0 or more, summing to 100",
               REQUIRED, "#23, Epic #5"),
    SettingKey("site.<project>.serves", "an array of business requirement IDs", (), "#23"),
    SettingKey("capacity.attention_hours", "a table of weekday (mon to sun) to hours", None, "Epic #5"),
    SettingKey("capacity.agent_lanes", _INT_0, None, "Epic #5"),
    SettingKey("capacity.reserved", "an array of tables ([[capacity.reserved]])", (), "Epic #5"),
    SettingKey("capacity.reserved[].day", "a weekday (mon to sun)", REQUIRED, "Epic #5"),
    SettingKey("capacity.reserved[].start", 'a time "HH:MM" from 00:00 to 23:59', REQUIRED, "Epic #5"),
    SettingKey("capacity.reserved[].hours", "a finite number above 0", REQUIRED, "Epic #5"),
    SettingKey("capacity.reserved[].label", "a string", None, "Epic #5"),
    SettingKey("capacity.block_min_hours", "a finite number above 0", 1, "Epic #5"),
    SettingKey("capacity.block_default_hours", "a finite number, at least block_min_hours", 2, "Epic #5"),
    SettingKey("capacity.block_start_cost_hours", "a finite number, 0 or more", 0.25, "Epic #5"),
    SettingKey("capacity.attention_mapping", "an array of exactly five numbers, each 0 or more, not decreasing",
               None, "#36"),
    SettingKey("milestone.project", "a non-empty string", None, "#24"),
    SettingKey("milestone.issue", "a non-empty string", None, "#24"),
    SettingKey("milestone.label", "a string", "autonomy milestone", "#24"),
    SettingKey("thresholds.wip_max", _INT_0, 3, "#29"),
    SettingKey("thresholds.unknown_deps_max", _INT_0, 0, "#29"),
    SettingKey("thresholds.unplanned_max", _INT_0, 0, "#29"),
    SettingKey("thresholds.share_gap_points", "a finite number above 0", 10, "#29, #25"),
    SettingKey("thresholds.neglect_weeks", _INT_1, 4, "#29"),
    SettingKey("thresholds.effort_window_weeks", _INT_1, 4, "#25"),
)
DEFAULTS = {k.path: k.default for k in SETTINGS_KEYS if k.default not in (REQUIRED, None, ())}
_KEYS = {k.path: k for k in SETTINGS_KEYS}
_DAY_RULE = "a finite number from 0 to 24"  # each capacity.attention_hours.<day>
_SERVES_ITEM_RULE = "a business requirement ID"


def _number(v):
    """An int or a float, never a bool."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v):
    # An int is always finite; math.isfinite would raise on a 400-digit one.
    return _number(v) and (isinstance(v, int) or math.isfinite(v))


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _mapping_shape(v):
    # Shape only: #36 owns finiteness and MAX_RATE. nan fails every comparison.
    return (isinstance(v, list) and len(v) == 5 and all(_number(x) and x >= 0 for x in v)
            and all(a <= b for a, b in zip(v, v[1:])))


_CHECKS = {
    "capacity.agent_lanes": lambda v: _int(v) and v >= 0,
    "capacity.reserved[].day": lambda v: isinstance(v, str) and v in WEEKDAYS,
    "capacity.reserved[].start": lambda v: isinstance(v, str) and HHMM_RE.fullmatch(v) is not None,
    "capacity.reserved[].hours": lambda v: _finite(v) and v > 0,
    "capacity.reserved[].label": lambda v: isinstance(v, str),
    "capacity.block_min_hours": lambda v: _finite(v) and v > 0,
    "capacity.block_start_cost_hours": lambda v: _finite(v) and v >= 0,
    "capacity.attention_mapping": _mapping_shape,
    "milestone.project": lambda v: isinstance(v, str) and v != "",
    "milestone.issue": lambda v: isinstance(v, str) and v != "",
    "milestone.label": lambda v: isinstance(v, str),
    "thresholds.wip_max": lambda v: _int(v) and v >= 0,
    "thresholds.unknown_deps_max": lambda v: _int(v) and v >= 0,
    "thresholds.unplanned_max": lambda v: _int(v) and v >= 0,
    "thresholds.share_gap_points": lambda v: _finite(v) and v > 0,
    "thresholds.neglect_weeks": lambda v: _int(v) and v > 0,
    "thresholds.effort_window_weeks": lambda v: _int(v) and v > 0,
}


def _fallback(default):
    if default is None:
        return "it is not set"
    if default == ():
        return "it is empty"
    return f"the default {json.dumps(default) if isinstance(default, str) else repr(default)} is used"


def _num(x):
    """A number for a message; a huge one is named, never formatted."""
    if (isinstance(x, int) and abs(x) >= 10**15) or (isinstance(x, float) and not math.isfinite(x)):
        return "a very large number"
    return f"{x:g}"


def _total(values):
    """A sum that never overflows: inf stands for "too large to matter"."""
    if any(v > 10**9 for v in values):
        return math.inf
    return sum(values)


def _allowed(prefix):
    """The key names the registry allows directly under a pattern prefix
    ("" for the settings root, "capacity", "allocation[]", "site.<project>")."""
    names = set()
    for k in SETTINGS_KEYS:
        if prefix == "":
            rest = k.path
        elif k.path.startswith(prefix + "."):
            rest = k.path[len(prefix) + 1 :]
        else:
            continue
        names.add(re.split(r"[.\[]", rest, maxsplit=1)[0])
    return names


# --- the model -----------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioEntry:
    kind: str
    section: str | None
    line: int | None  # file line, 1-based
    key_path: str | None
    message: str  # one line; never a path
    source: str = PORTFOLIO_LABEL


@dataclass(frozen=True)
class Capacity:
    attention_hours: dict | None  # weekday -> hours, all seven days
    agent_lanes: int | None
    reserved: tuple
    block_min_hours: int | float | None
    block_default_hours: int | float | None
    block_start_cost_hours: int | float | None
    attention_mapping: tuple | None


@dataclass(frozen=True)
class Thresholds:
    wip_max: int | None
    unknown_deps_max: int | None
    unplanned_max: int | None
    share_gap_points: int | float | None
    neglect_weeks: int | None
    effort_window_weeks: int | None


@dataclass(frozen=True)
class Reserved:
    day: str
    start: str
    hours: int | float
    label: str | None
    line: int | None  # its [[capacity.reserved]] header line


@dataclass(frozen=True)
class Milestone:
    project: str
    issue: str
    label: str
    line: int | None  # its [milestone] header line


@dataclass(frozen=True)
class Allocation:
    from_date: date
    shares: dict  # project name -> int | float
    line: int | None  # its [[allocation]] header line


SHARE_TOLERANCE = 0.01

_NO_CAPACITY = Capacity(None, None, (), None, None, None, None)
_NO_THRESHOLDS = Thresholds(None, None, None, None, None, None)


@dataclass(frozen=True)
class Portfolio:
    brs: RequirementSet
    allocations: list  # [Allocation], valid ones in file order
    serves: dict  # project name -> [ReqId]
    capacity: Capacity
    milestone: Milestone | None
    thresholds: Thresholds  # every field None when the settings are not read: consumers use DEFAULTS
    settings_read: bool
    report: list
    key_lines: dict  # key path -> file line, for the keys written in standard form
    # (name, key parts, "share" | "site" | "milestone") for every project the
    # settings name, set-aside allocations included: check_portfolio's input.
    project_refs: tuple = ()


def _sort_key(e):
    return (_SECTION_ORDER[e.section], e.line is not None, e.line or 0, _KIND_ORDER[e.kind])


class _Report:
    def __init__(self):
        self.entries = []

    def add(self, kind, section, line, key_path, message):
        self.entries.append(PortfolioEntry(kind, section, line, key_path, message))


def _empty(br_namespace, report):
    return Portfolio(
        brs=RequirementSet(br_namespace, {}, []),
        allocations=[],
        serves={},
        capacity=_NO_CAPACITY,
        milestone=None,
        thresholds=_NO_THRESHOLDS,
        settings_read=False,
        report=sorted(report, key=_sort_key),
        key_lines={},
    )


# --- key paths and their lines ------------------------------------------------


def _seg(name):
    """A key path segment: bare when it is a bare TOML key, else a JSON string."""
    return name if BARE_SEGMENT_RE.fullmatch(name) else json.dumps(name, ensure_ascii=True)


def _path(parts):
    """A key path from parts: a str is a key, an int the 1-based member of the
    array before it (allocation[2].shares.beta)."""
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + _seg(part)
    return out


_SEG = r"[A-Za-z0-9_-]+"
_DOTTED = rf"{_SEG}(?:[ \t]*\.[ \t]*{_SEG})*"
# Standard-form headers and bare or dotted keys only; every other form falls
# back to the nearest enclosing line. Greedy and anchored: linear on a long line.
TABLE_RE = re.compile(rf"\[(?P<arr>\[)?[ \t]*(?P<name>{_DOTTED})[ \t]*\](?(arr)\])[ \t]*(?:#.*)?")  # fullmatch
KEY_RE = re.compile(rf"(?P<name>{_DOTTED})[ \t]*=")  # match
_DOT_RE = re.compile(r"[ \t]*\.[ \t]*")


def _key_lines(block_lines, opener) -> dict:
    """{key path: file line} for the headers and keys written in standard form.
    `opener` is the settings fence's 0-based index. An array member's
    sub-table takes the member's position (allocation[2].shares)."""
    found = {}
    prefix = ()  # None after a header this locator cannot read
    counters = {}  # array header segments -> members so far

    def record(parts, line):
        found.setdefault(_path(parts), line)

    for k, raw in enumerate(block_lines):
        line = opener + 2 + k
        text = raw.strip(" \t")
        if text.startswith("["):
            m = TABLE_RE.fullmatch(text)
            if m is None:
                prefix = None
                continue
            segs = tuple(_DOT_RE.split(m["name"]))
            if m["arr"]:
                counters[segs] = counters.get(segs, 0) + 1
                for nested in [c for c in counters if len(c) > len(segs) and c[: len(segs)] == segs]:
                    del counters[nested]
            parts = []
            for i, seg in enumerate(segs):
                parts.append(seg)
                if segs[: i + 1] in counters:
                    parts.append(counters[segs[: i + 1]])
            prefix = tuple(parts)
            record(prefix, line)
            continue
        if prefix is None:
            continue
        m = KEY_RE.match(text)
        if m is not None:
            segs = tuple(_DOT_RE.split(m["name"]))
            for i in range(1, len(segs) + 1):
                record(prefix + segs[:i], line)
    return found


def _line(key_lines, parts):
    """The line of the key at `parts`: its own when written, else its nearest
    written enclosing table or key, else None."""
    for k in range(len(parts), 0, -1):
        line = key_lines.get(_path(parts[:k]))
        if line is not None:
            return line
    return None


def _toml_error_line(e, opener, closer):
    """The file line of a TOML error in the block between the fences at
    0-based indices opener and closer, or None. Python 3.14 gives `lineno`;
    3.11 gives the line only in the message."""
    inner = closer - opener - 1
    lineno = getattr(e, "lineno", None)
    text = str(e)
    if isinstance(lineno, int) and not isinstance(lineno, bool):
        at = lineno
    elif (m := TOML_AT_RE.search(text)) is not None:
        at = int(m["line"])
    elif text.endswith(TOML_END):
        at = inner
    else:
        return None
    if inner < 1:
        return None
    # At the end of the document the line can be one past the last.
    return opener + 1 + min(max(at, 1), inner)


def _toml_reason(e):
    msg = getattr(e, "msg", None)
    if not isinstance(msg, str):
        msg = TOML_AT_RE.sub("", str(e)).removesuffix(TOML_END)
    return " ".join(msg.split()) or type(e).__name__


# --- reading -------------------------------------------------------------------


def read_portfolio_file(path, *, br_namespace) -> Portfolio:
    """Read and check a portfolio file as UTF-8. A file that cannot be read
    gives an empty Portfolio with one portfolio_unreadable entry."""

    def unreadable(message):
        return _empty(br_namespace, [PortfolioEntry("portfolio_unreadable", None, None, None, message)])

    # stat first, so a directory reads alike on every operating system.
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return unreadable("the portfolio file does not exist")
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        return unreadable(f"cannot read the portfolio file: {error_reason(e)}")
    if not stat.S_ISREG(st.st_mode):
        return unreadable("the portfolio file is not a file")
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except (OSError, ValueError) as e:
        return unreadable(f"cannot read the portfolio file: {error_reason(e)}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return unreadable(f"the portfolio file is not valid UTF-8 at byte {e.start}")
    return _read_text(text, str(path), br_namespace)


def _sections(lines, report):
    """{heading text: (heading index, end index)} for the first level-2 BR and
    Settings headings outside fences; a second one is reported."""
    hidden = [False] * len(lines)
    for i, j in fence_spans(lines):
        hidden[i : j + 1] = [True] * (j + 1 - i)
    headings = []
    for i, line in enumerate(lines):
        if not hidden[i]:
            m = HEADING_RE.fullmatch(line.rstrip(" \t"))
            if m is not None:
                headings.append((i, len(m["hashes"]), m["text"]))
    found = {}
    for n, (i, level, text) in enumerate(headings):
        if level != 2 or text not in (BR_HEADING, SETTINGS_HEADING):
            continue
        section = SECTIONS[0] if text == BR_HEADING else SECTIONS[1]
        if text in found:
            first = found[text][0] + 1
            report.add("duplicate_section", section, i + 1, None,
                       f"a second '## {text}' heading; only the first (line {first}) is read")
            continue
        end = next((k for k, lvl, _ in headings[n + 1 :] if lvl <= 2), len(lines))
        found[text] = (i, end)
    return found


def _read_brs(lines, found, source, br_namespace, report):
    if BR_HEADING not in found:
        report.add("no_br_section", SECTIONS[0], None, None,
                   "no '## Business requirements' heading; no business requirements are read")
        return RequirementSet(br_namespace, {}, [])
    start, end = found[BR_HEADING]
    rs = parse_requirements("\n".join(lines[start + 1 : end]), br_namespace, source=source,
                            first_line=start + 2, layers={"BR"}, br_namespace=br_namespace)
    for e in rs.report:
        report.add(e.kind, SECTIONS[0], e.line, None, e.message)
    return replace(rs, report=[replace(e, source=PORTFOLIO_LABEL) for e in rs.report])


def _settings_block(lines, found, report):
    """(opener, closer) of the first toml block under ## Settings, or None."""
    blocks = []
    if SETTINGS_HEADING in found:
        start, end = found[SETTINGS_HEADING]
        blocks = [(i, j) for i, j in fence_spans(lines[:end])
                  if i > start and FENCE_RE.fullmatch(lines[i])["info"].strip() == "toml"]
    if not blocks:
        report.add("no_settings", SECTIONS[1], None, None,
                   "no ```toml block under a '## Settings' heading (level 2, outside a fence); "
                   "every setting is not set")
        return None
    for i, _ in blocks[1:]:
        report.add("duplicate_section", SECTIONS[1], i + 1, None,
                   f"a second toml block under '## Settings'; only the first (line {blocks[0][0] + 1}) is read")
    return blocks[0]


def _load_settings(lines, block, report):
    """(the settings table, key lines), or (None, key lines) when the block is
    not valid TOML or its version is not this Gantry's."""
    opener, closer = block
    inner = lines[opener + 1 : closer]
    key_lines = _key_lines(inner, opener)
    try:
        raw = tomllib.loads("\n".join(inner) + "\n")
    except (ValueError, RecursionError) as e:
        # TOMLDecodeError and the integer-digit limit are ValueErrors.
        report.add("settings_invalid", SECTIONS[1], _toml_error_line(e, opener, closer), None,
                   f"the settings block is not valid TOML: {_toml_reason(e)}; every setting is not set")
        return None, key_lines
    line = _line(key_lines, ("portfolio_version",))
    if "portfolio_version" not in raw:
        report.add("bad_version", SECTIONS[1], line, "portfolio_version",
                   f"no portfolio_version; this Gantry reads portfolio_version {PORTFOLIO_VERSION}, "
                   "so the settings block is not read")
        return None, key_lines
    version = raw["portfolio_version"]
    if not (isinstance(version, int) and not isinstance(version, bool) and version == PORTFOLIO_VERSION):
        report.add("bad_version", SECTIONS[1], line, "portfolio_version",
                   f"portfolio_version {describe(version)} is not {PORTFOLIO_VERSION}, the version this Gantry "
                   "reads, so the settings block is not read")
        return None, key_lines
    return raw, key_lines


_ABSENT = object()  # a key that is not written
_BAD = object()  # a key that is written and reported


class _Settings:
    """Checks the settings table against the registry; one entry per problem."""

    def __init__(self, raw, key_lines, report, brs, has_brs, br_namespace):
        self.raw = raw
        self.key_lines = key_lines
        self.report = report
        self.brs = brs
        self.has_brs = has_brs
        self.br_namespace = br_namespace
        self.refs = []

    def add(self, kind, parts, message):
        self.report.add(kind, SECTIONS[1], _line(self.key_lines, parts), _path(parts), message)

    def bad(self, parts, rule, value, fallback):
        self.add("bad_value", parts, f"must be {rule}, not {describe(value)}; {fallback}")

    def known(self, table, parts, pattern, allowed=None):
        """`table` without its unknown keys, each reported once."""
        allowed = _allowed(pattern) if allowed is None else allowed
        kept = {}
        for name, value in table.items():
            if name in allowed:
                kept[name] = value
            else:
                self.add("unknown_key", parts + (name,), "unknown key; it is ignored")
        return kept

    def table(self, value, parts, pattern):
        """A table's known keys, or {} with bad_value when it is not a table."""
        if isinstance(value, dict):
            return self.known(value, parts, pattern)
        self.add("bad_value", parts, f"must be a table, not {describe(value)}; it is ignored")
        return {}

    def get(self, table, parts, pattern, fallback=None):
        """The value of a registry key, _ABSENT, or _BAD (reported)."""
        name = parts[-1]
        if name not in table:
            return _ABSENT
        value = table[name]
        if _CHECKS[pattern](value):
            return value
        key = _KEYS[pattern]
        self.bad(parts, key.rule, value, fallback or _fallback(key.default))
        return _BAD

    def value(self, table, parts, pattern):
        """A registry key's value, or its default when absent or bad."""
        value = self.get(table, parts, pattern)
        default = _KEYS[pattern].default
        return value if value not in (_ABSENT, _BAD) else (None if default in (REQUIRED, ()) else default)

    # --- the sections ---

    def read(self):
        root = self.known({k: v for k, v in self.raw.items() if k != "portfolio_version"}, (), "")
        capacity = self.capacity(self.table(root["capacity"], ("capacity",), "capacity") if "capacity" in root else {})
        milestone = self.milestone(root["milestone"]) if "milestone" in root else None
        thresholds = self.thresholds(
            self.table(root["thresholds"], ("thresholds",), "thresholds") if "thresholds" in root else {})
        allocations = self.allocations(root["allocation"]) if "allocation" in root else []
        serves = {}
        if "site" in root:
            sites = root["site"] if isinstance(root["site"], dict) else None  # any project name
            if sites is None:
                self.add("bad_value", ("site",), f"must be a table, not {describe(root['site'])}; it is ignored")
            for project, table in (sites or {}).items():
                parts = ("site", project)
                if not isinstance(table, dict):
                    self.add("bad_value", parts, f"must be a table, not {describe(table)}; it is ignored")
                    continue
                self.refs.append((project, parts, "site"))
                serves[project] = self.serves(self.known(table, parts, "site.<project>"), parts)
        return allocations, capacity, milestone, thresholds, serves

    def allocations(self, value):
        """The valid allocations in file order, a repeated from dropped."""
        if not (isinstance(value, list) and all(isinstance(m, dict) for m in value)):
            self.bad(("allocation",), _KEYS["allocation"].rule, value, "it is empty")
            return []
        valid = []  # (n, Allocation)
        for n, member in enumerate(value, 1):
            at = ("allocation", n)
            member = self.known(member, at, "allocation[]")
            day = self.from_date(member, at)
            shares = self.shares(member, at)
            if day is not None and shares is not None:
                valid.append((n, Allocation(day, shares, _line(self.key_lines, at))))
        kept = []
        for n, a in valid:
            first = next((k for k in kept if k.from_date == a.from_date), None)
            if first is None:
                kept.append(a)
            else:
                self.report.add("duplicate_allocation_date", SECTIONS[1], _line(self.key_lines, ("allocation", n, "from")),
                                _path(("allocation", n, "from")),
                                f"another allocation (line {first.line}) has from {a.from_date.isoformat()}; "
                                "the first is used")
        return kept

    def from_date(self, member, at):
        parts = at + ("from",)
        if "from" not in member:
            self.add("undated_allocation", parts, "no from date; this allocation is not used")
            return None
        value = member["from"]
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        self.add("undated_allocation", parts,
                 f"from must be a TOML local date (YYYY-MM-DD), not {describe(value)}; this allocation is not used")
        return None

    def shares(self, member, at):
        parts = at + ("shares",)
        if "shares" not in member:
            self.add("bad_allocation", parts, "no shares table; this allocation is not used")
            return None
        value = member["shares"]
        if not isinstance(value, dict):
            self.add("bad_allocation", parts,
                     f"shares must be a table, not {describe(value)}; this allocation is not used")
            return None
        ok = True
        for name, share in value.items():
            self.refs.append((name, parts + (name,), "share"))
            if not (_finite(share) and share >= 0):
                self.add("bad_allocation", parts + (name,),
                         f"share {_seg(name)} must be a number, 0 or more, not {describe(share)}; "
                         "this allocation is not used")
                ok = False
        if not ok:
            return None
        total = _total(list(value.values()))
        # A float sum of in-tolerance shares can land a hair outside 0.01.
        if abs(total - 100) > SHARE_TOLERANCE + 1e-9:
            self.add("bad_allocation", parts, f"the shares sum to {_num(total)}, not 100; this allocation is not used")
            return None
        return dict(value)

    def capacity(self, table):
        p = ("capacity",)
        hours = self.attention_hours(table)
        lanes = self.value(table, p + ("agent_lanes",), "capacity.agent_lanes")
        reserved = self.reserved(table)
        minimum = self.value(table, p + ("block_min_hours",), "capacity.block_min_hours")
        # block_default_hours is at least the minimum in use; so is its default.
        in_use = minimum if minimum > 2 else 2
        default = table.get("block_default_hours", _ABSENT)
        if default is _ABSENT:
            default = in_use
        elif not (_finite(default) and default >= minimum):
            self.bad(p + ("block_default_hours",), _KEYS["capacity.block_default_hours"].rule, default,
                     f"the default {_num(in_use)} is used")
            default = in_use
        cost = self.value(table, p + ("block_start_cost_hours",), "capacity.block_start_cost_hours")
        mapping = self.value(table, p + ("attention_mapping",), "capacity.attention_mapping")
        capacity = Capacity(hours, lanes, reserved, minimum, default, cost,
                            tuple(mapping) if mapping is not None else None)
        for key in ("attention_hours", "agent_lanes"):
            if getattr(capacity, key) is None:
                self.add("capacity_missing", p + (key,),
                         f"no capacity.{key}, so the week planner has no capacity to plan into")
        if hours is not None:
            self.exceeds(hours, reserved)
        return capacity

    def attention_hours(self, table):
        parts = ("capacity", "attention_hours")
        if "attention_hours" not in table:
            return None
        value = table["attention_hours"]
        if not isinstance(value, dict):
            self.bad(parts, _KEYS["capacity.attention_hours"].rule, value, "it is not set")
            return None
        hours = {}
        for day, h in self.known(value, parts, None, allowed=WEEKDAYS).items():
            if _finite(h) and 0 <= h <= 24:
                hours[day] = h
            else:
                self.bad(parts + (day,), _DAY_RULE, h, "the day reads 0")
        return {day: hours.get(day, 0) for day in WEEKDAYS}

    def reserved(self, table):
        parts = ("capacity", "reserved")
        value = table.get("reserved", [])
        if not (isinstance(value, list) and all(isinstance(m, dict) for m in value)):
            self.bad(parts, _KEYS["capacity.reserved"].rule, value, "it is empty")
            return ()
        kept = []
        for n, member in enumerate(value, 1):
            at = parts + (n,)
            member = self.known(member, at, "capacity.reserved[]")
            fields, ok = {}, True
            for name in ("day", "start", "hours", "label"):
                pattern = f"capacity.reserved[].{name}"
                got = self.get(member, at + (name,), pattern, fallback="the reserved block is set aside")
                if got is _ABSENT and _KEYS[pattern].default is REQUIRED:
                    self.add("bad_value", at + (name,),
                             f"missing; must be {_KEYS[pattern].rule}; the reserved block is set aside")
                    ok = False
                elif got is _BAD:
                    ok = False
                fields[name] = None if got in (_ABSENT, _BAD) else got
            if ok:
                kept.append(Reserved(fields["day"], fields["start"], fields["hours"], fields["label"],
                                     _line(self.key_lines, at)))
        return tuple(kept)

    def exceeds(self, hours, reserved):
        for day in WEEKDAYS:
            on_day = [r for r in reserved if r.day == day]
            total = _total([r.hours for r in on_day])
            if on_day and total > hours[day]:
                total_text = _num(total)
                if total_text == "a very large number":
                    total_text += " of"
                self.report.add("reserved_exceeds_capacity", SECTIONS[1], on_day[0].line, "capacity.reserved",
                                f"reserved blocks on {day} total {total_text} hours, more than its "
                                f"{_num(hours[day])} attention hours")

    def milestone(self, value):
        p = ("milestone",)
        table = self.table(value, p, "milestone")
        if not isinstance(value, dict):
            return None
        if isinstance(table.get("project"), str):
            self.refs.append((table["project"], p + ("project",), "milestone"))
        fields, ok = {}, True
        for name in ("project", "issue"):
            got = self.get(table, p + (name,), f"milestone.{name}", fallback="the milestone is not set")
            if got is _ABSENT:
                self.add("bad_value", p + (name,),
                         f"missing; must be {_KEYS['milestone.' + name].rule}; the milestone is not set")
            if got in (_ABSENT, _BAD):
                ok = False
            fields[name] = got
        issue = fields["issue"]
        if isinstance(issue, str) and issue:
            if not ID_TOKEN_RE.fullmatch(issue):
                self.add("bad_milestone", p + ("issue",),
                         f"{issue!r} is not an issue id (it must match {ID_TOKEN_RE.pattern})")
                ok = False
            elif OUTLINE_RE.fullmatch(issue):
                self.add("bad_milestone", p + ("issue",),
                         f"{issue!r} is an outline position: name the leaf by its issue id; "
                         "outline positions move at wbs update")
                ok = False
        label = self.value(table, p + ("label",), "milestone.label")
        if not ok:
            return None
        return Milestone(fields["project"], issue, label, _line(self.key_lines, p))

    def thresholds(self, table):
        return Thresholds(*(self.value(table, ("thresholds", f.name), f"thresholds.{f.name}")
                            for f in fields(Thresholds)))

    def serves(self, table, parts):
        """The resolved BR IDs a site serves, each once; the rest reported."""
        parts = parts + ("serves",)
        value = table.get("serves", [])
        if not isinstance(value, list):
            self.bad(parts, _KEYS["site.<project>.serves"].rule, value, "it is empty")
            return []
        kept = []
        for n, item in enumerate(value, 1):
            at = parts + (n,)
            rid = parse_id(item, self.br_namespace) if isinstance(item, str) else None
            if rid is None:
                got = repr(item) if isinstance(item, str) else describe(item)
                self.add("bad_value", at, f"must be {_SERVES_ITEM_RULE}, not {got}; it is not read")
            elif not self.has_brs:
                continue  # one cause, one entry: no_br_section
            elif rid.namespace != self.br_namespace:
                self.add("misplaced_priority", at,
                         f"{rid} is not in the installation namespace {self.br_namespace!r}; it is not read")
            elif rid.code != "BR":
                self.add("misplaced_priority", at, f"{rid} is a {rid.code} ID; serves names business requirements (BR)")
            elif str(rid) not in self.brs.requirements:
                self.add("undefined_priority", at, f"{rid} names no business requirement in this file")
            elif rid not in kept:
                kept.append(rid)
        return kept


def intended_shares(portfolio, day: date) -> Allocation | None:
    """The allocation that applies on `day`: the valid one with the latest
    from on or before it, or None. `day` is the build's as-of date, never the
    clock."""
    applying = [a for a in portfolio.allocations if a.from_date <= day]
    return max(applying, key=lambda a: a.from_date) if applying else None


def check_portfolio(portfolio, site_names, issues_by_site=None) -> list:
    """The entries that need the installation: unknown_project against
    `site_names` (exact and case-sensitive) and, when `issues_by_site` maps a
    site to its leaves' issue: values (None: the site was not read), the
    milestone leaf. Nothing when the settings were not read."""
    if not portfolio.settings_read:
        return []
    names = set(site_names)
    out = []

    def add(kind, parts, message):
        out.append(PortfolioEntry(kind, SECTIONS[1], _line(portfolio.key_lines, parts), _path(parts), message))

    for name, parts, what in portfolio.project_refs:
        if name not in names:
            tail = "; its share still counts toward the allocation's 100" if what == "share" else ""
            add("unknown_project", parts, f"{name!r} is not a site in the devagent config{tail}")
    m = portfolio.milestone
    if issues_by_site is not None and m is not None and m.project in names:
        issues = issues_by_site.get(m.project)
        if issues is None:
            add("milestone_no_leaf", ("milestone", "issue"),
                f"site {shown(m.project)} could not be read, so the milestone leaf cannot be found")
        elif m.issue not in issues:
            add("milestone_no_leaf", ("milestone", "issue"), f"no leaf on {shown(m.project)} has issue {m.issue!r}")
    return sorted(out, key=_sort_key)


def format_entry(e) -> str:
    """One entry as one line: [line N: ]kind[ (advisory)]: [key path: ]message."""
    kind = f"{e.kind} (advisory)" if e.kind in ADVISORY_KINDS else e.kind
    return (f"line {e.line}: " if e.line is not None else "") + f"{kind}: " +         (f"{e.key_path}: " if e.key_path is not None else "") + e.message


def _read_text(text, source, br_namespace):
    text = text.removeprefix(_BOM)
    # split("\n"), never splitlines(), as #15's reader.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    report = _Report()
    found = _sections(lines, report)
    brs = _read_brs(lines, found, source, br_namespace, report)
    block = _settings_block(lines, found, report)
    raw, key_lines = (None, {}) if block is None else _load_settings(lines, block, report)
    p = _empty(br_namespace, [])
    if raw is not None:
        settings = _Settings(raw, key_lines, report, brs, BR_HEADING in found, br_namespace)
        allocations, capacity, milestone, thresholds, serves = settings.read()
        p = replace(p, allocations=allocations, capacity=capacity, milestone=milestone, thresholds=thresholds,
                    serves=serves, project_refs=tuple(settings.refs))
    return replace(p, brs=brs, settings_read=raw is not None, key_lines=key_lines,
                   report=sorted(report.entries, key=_sort_key))
