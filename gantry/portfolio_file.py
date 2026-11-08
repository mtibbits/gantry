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

import os
import re
import stat
from dataclasses import dataclass, replace

from gantry.config import error_reason
from gantry.requirements import RequirementSet, fence_spans, parse_requirements

PORTFOLIO_LABEL = "the portfolio file"
SECTIONS = ("business requirements", "settings")  # section None: the whole file (portfolio_unreadable only)
# An ATX heading, matched on the line with its trailing spaces and tabs removed,
# outside fences only. Greedy throughout, so it is linear on a long line.
HEADING_RE = re.compile(r"(?P<hashes>#{1,6})[ \t]+(?P<text>.*)")  # fullmatch
BR_HEADING, SETTINGS_HEADING = "Business requirements", "Settings"  # level 2, matched exactly
_BOM = "\N{BYTE ORDER MARK}"


@dataclass(frozen=True)
class PortfolioEntry:
    kind: str
    section: str | None
    line: int | None  # file line, 1-based
    key_path: str | None
    message: str  # one line; never a path
    source: str = PORTFOLIO_LABEL


@dataclass(frozen=True)
class Portfolio:
    brs: RequirementSet
    allocations: list
    serves: dict
    capacity: object
    milestone: object
    thresholds: object
    settings_read: bool
    report: list
    key_lines: dict


REPORT_KINDS = ("portfolio_unreadable", "no_br_section", "no_settings", "duplicate_section")
_SECTION_ORDER = {None: 0, SECTIONS[0]: 1, SECTIONS[1]: 2}


def _sort_key(e):
    order = REPORT_KINDS + _carried_kinds()
    return (_SECTION_ORDER[e.section], e.line is not None, e.line or 0, order.index(e.kind))


def _carried_kinds():
    from gantry import requirements

    return tuple(k for k in requirements.REPORT_KINDS if k != "unreadable")


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
        capacity=None,
        milestone=None,
        thresholds=None,
        settings_read=False,
        report=sorted(report, key=_sort_key),
        key_lines={},
    )


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


def _read_text(text, source, br_namespace):
    text = text.removeprefix(_BOM)
    # split("\n"), never splitlines(), as #15's reader.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    report = _Report()
    found = _sections(lines, report)
    brs = _read_brs(lines, found, source, br_namespace, report)
    p = _empty(br_namespace, report.entries)
    return replace(p, brs=brs)
