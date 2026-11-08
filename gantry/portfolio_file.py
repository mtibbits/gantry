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
import os
import re
import stat
import tomllib
from dataclasses import dataclass, replace

from gantry import requirements
from gantry.config import describe, error_reason
from gantry.requirements import FENCE_RE, RequirementSet, fence_spans, parse_requirements

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

REPORT_KINDS = ("portfolio_unreadable", "no_br_section", "no_settings", "duplicate_section", "settings_invalid",
                "bad_version", "unknown_key")
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


_NO_CAPACITY = Capacity(None, None, (), None, None, None, None)
_NO_THRESHOLDS = Thresholds(None, None, None, None, None, None)


@dataclass(frozen=True)
class Portfolio:
    brs: RequirementSet
    allocations: list
    serves: dict  # project name -> [ReqId]
    capacity: Capacity
    milestone: object
    thresholds: Thresholds  # every field None when the settings are not read: consumers use DEFAULTS
    settings_read: bool
    report: list
    key_lines: dict  # key path -> file line, for the keys written in standard form


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


class _Settings:
    """Checks the settings table against the registry; one entry per problem."""

    def __init__(self, raw, key_lines, report):
        self.raw = raw
        self.key_lines = key_lines
        self.report = report

    def add(self, kind, parts, message):
        self.report.add(kind, SECTIONS[1], _line(self.key_lines, parts), _path(parts), message)

    def known(self, table, parts, pattern):
        """`table` without its unknown keys, each reported once."""
        allowed = _allowed(pattern)
        kept = {}
        for name, value in table.items():
            if name in allowed:
                kept[name] = value
            else:
                self.add("unknown_key", parts + (name,), "unknown key; it is ignored")
        return kept

    def table(self, value, parts):
        """A table's value, or None with bad_value when it is not a table."""
        if isinstance(value, dict):
            return value
        self.add("bad_value", parts, f"must be a table, not {describe(value)}; it is ignored")
        return None

    def read(self):
        raw = self.known({k: v for k, v in self.raw.items() if k != "portfolio_version"}, (), "")
        for name in ("capacity", "milestone", "thresholds"):
            if name in raw:
                table = self.table(raw[name], (name,))
                if table is not None:
                    self.known(table, (name,), name)
        if isinstance(raw.get("allocation"), list):
            for n, member in enumerate(raw["allocation"], 1):
                if isinstance(member, dict):
                    self.known(member, ("allocation", n), "allocation[]")
        if "site" in raw:
            sites = self.table(raw["site"], ("site",))
            for project, table in (sites or {}).items():
                table = self.table(table, ("site", project))
                if table is not None:
                    self.known(table, ("site", project), "site.<project>")


def _read_text(text, source, br_namespace):
    text = text.removeprefix(_BOM)
    # split("\n"), never splitlines(), as #15's reader.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    report = _Report()
    found = _sections(lines, report)
    brs = _read_brs(lines, found, source, br_namespace, report)
    block = _settings_block(lines, found, report)
    raw, key_lines = (None, {}) if block is None else _load_settings(lines, block, report)
    if raw is not None:
        _Settings(raw, key_lines, report).read()
    p = _empty(br_namespace, report.entries)
    return replace(p, brs=brs, settings_read=raw is not None, key_lines=key_lines)
