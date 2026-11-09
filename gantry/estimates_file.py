"""A site's estimates file: found in its planning directory and read (#10).

A site's `[site.<name>] estimates` key names its file or, set to `false`,
turns discovery off. Otherwise the planning directory's top level is searched
for `estimates-YYYY-MM-DD*.md`, and the latest date wins. The file's tables
whose header starts with `#` give each row's effort, hours, model tiers and
flags; no other column is read. Nothing here raises: every problem is a report
entry, and none names an absolute path. docs/worksite-page.md states the rules.
"""

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath

from gantry.config import error_reason
from gantry.est_rules import NUM
from gantry.model import ReportEntry
from gantry.roots import OutsideRoots, list_confined, read_bytes_confined, stat_confined

DATED_RE = re.compile(r"estimates-(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}).*\.md", re.DOTALL)
ANY_RE = re.compile(r"estimates-.*\.md", re.DOTALL)
ROW_ID_RE = re.compile(r"#(?P<n>[0-9]{1,9})")
HOURS_RE = re.compile(rf"(?P<lo>{NUM})(?:-(?P<hi>{NUM}))?")
READ_COLUMNS = ("#", "effort", "hours", "thinking", "checking", "flags")

MESSAGES = {
    "no_file": "no estimates file",
    "off": "no estimates file: site.{site}.estimates is false",
    "ignored": "estimates file {name!r} is ignored: {why}",
    "later": "{read!r} is later",
    "same_date": "{read!r} has the same date and sorts later",
    "undated": "its name has no date",
    "missing": "site.{site}.estimates: the file does not exist",
    "unlisted": "the planning directory cannot be listed: {reason}",
    "cannot_read": "estimates file {name!r} cannot be read: {reason}",
    "not_utf8": "estimates file {name!r} is not valid UTF-8 at byte {at}",
    "column_missing": "estimates file {name!r}: a table has no {column!r} column (first on line {line})",
    "cell_bad": "estimates file {name!r} line {line}: hours {cell!r} is not a number or a range above 0; "
                "#{n} gives no hours",
    "duplicate": "estimates file {name!r} line {line}: a second row for #{n}; the row on line {first} is used",
}


@dataclass(frozen=True)
class Found:
    path: PurePath | None  # None: no file is read
    name: str | None  # the file's base name
    date: date | None  # from the name; None when it has none
    report: tuple[ReportEntry, ...]
    key: str = "estimates file"  # how a refusal names it: a configured file by its key


@dataclass(frozen=True)
class EstimatesRow:
    line: int
    effort: str | None  # None: an empty cell
    lo_h: float | None  # None: the hours cell gave no hours
    hi_h: float | None
    thinking: str | None
    checking: str | None
    flags: str | None


@dataclass(frozen=True)
class EstimatesFile:
    rows: dict[str, EstimatesRow]  # keyed Issue-<digits as written>
    report: tuple[ReportEntry, ...]


def _entry(site, kind, message):
    return ReportEntry(site, None, kind, message)


def _name_date(name):
    """The date a file name gives, or None."""
    m = DATED_RE.fullmatch(name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group("date"))
    except ValueError:
        return None


def find_estimates_file(roots, site, config) -> Found:
    """The estimates file a site reads under `roots`, with its report entries."""
    override = config.sites.get(site.name)
    configured = override.estimates if override is not None else None
    if configured is False:
        return Found(None, None, None, (_entry(site.name, "no_estimates_file", MESSAGES["off"].format(site=site.name)),))
    if configured is not None:
        path = PurePath(configured)
        key = f"site.{site.name}.estimates"
        try:
            stat_confined(roots, path, key=key)
        except FileNotFoundError:
            return Found(None, None, None, (
                _entry(site.name, "estimates_unreadable", MESSAGES["missing"].format(site=site.name)),))
        except (OSError, ValueError, OutsideRoots):
            pass  # read_estimates_file reports why it cannot be opened
        return Found(path, path.name, _name_date(path.name), (), key)
    if site.planning_dir is None:
        return Found(None, None, None, ())
    try:
        entries = list_confined(roots, site.planning_dir, key="devdoc_dir", site=site.name)
        # A link out of the roots is kept, so reading it reports the refusal.
        names = sorted(e.name for e in entries if ANY_RE.fullmatch(e.name) and (e.is_file or e.leaves_roots))
    except (OSError, ValueError, OutsideRoots) as e:
        reason = error_reason(e) or "unknown error"
        return Found(None, None, None, (
            _entry(site.name, "estimates_unreadable", MESSAGES["unlisted"].format(reason=reason)),))
    dates = {n: _name_date(n) for n in names}
    dated = [(d, n) for n, d in dates.items() if d is not None]
    report = []
    read = max(dated) if dated else None
    for n, d in dates.items():
        if read is not None and n == read[1]:
            continue
        if d is None:
            why = MESSAGES["undated"]
        elif d < read[0]:
            why = MESSAGES["later"].format(read=read[1])
        else:
            why = MESSAGES["same_date"].format(read=read[1])
        report.append(_entry(site.name, "estimates_ignored", MESSAGES["ignored"].format(name=n, why=why)))
    if read is None:
        report.append(_entry(site.name, "no_estimates_file", MESSAGES["no_file"]))
        return Found(None, None, None, tuple(report))
    return Found(PurePath(site.planning_dir) / read[1], read[1], read[0], tuple(report))


def _cells(line):
    """A table line's cells: the outer pipes dropped, each cell trimmed."""
    body = line[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def _hours(cell):
    """(lo, hi) from an hours cell, or None."""
    m = HOURS_RE.fullmatch(cell or "")
    if not m:
        return None
    lo = float(m.group("lo"))
    hi = float(m.group("hi")) if m.group("hi") is not None else lo
    if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo <= hi):
        return None
    return lo, hi


def read_estimates_file(roots, path, site: str, name: str, *, key="estimates file") -> EstimatesFile:
    """The rows of the file at `path` under `roots`, called `name` in every
    message; `key` names it in a refusal."""
    try:
        # A configured file's key (site.<name>.estimates) names the site already.
        data = read_bytes_confined(roots, path, key=key, site=None if key.startswith("site.") else site)
    except (OSError, ValueError, OutsideRoots) as e:
        reason = error_reason(e) or "unknown error"
        return EstimatesFile({}, (_entry(site, "estimates_unreadable",
                                         MESSAGES["cannot_read"].format(name=name, reason=reason)),))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return EstimatesFile({}, (_entry(site, "estimates_unreadable",
                                         MESSAGES["not_utf8"].format(name=name, at=e.start)),))
    text = text.removeprefix("\ufeff")
    rows, report, missing = {}, [], set()
    columns = None  # {column: index} while inside a # table
    # Split on "\n" only: str.splitlines() would also split inside a cell (U2).
    for number, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")
        if not line.startswith("|"):
            columns = None
            continue
        cells = _cells(line)
        if cells[0] == "#":
            columns = {}
            for i, c in enumerate(cells):
                columns.setdefault(c, i)
            for column in READ_COLUMNS:
                if column not in columns and column not in missing:
                    missing.add(column)
                    report.append(_entry(site, "estimates_column_missing", MESSAGES["column_missing"].format(
                        name=name, column=column, line=number)))
            continue
        m = ROW_ID_RE.fullmatch(cells[0])
        if columns is None or not m:
            continue
        n = m.group("n")
        key = f"Issue-{n}"
        if key in rows:
            report.append(_entry(site, "estimates_row_duplicate", MESSAGES["duplicate"].format(
                name=name, line=number, n=n, first=rows[key].line)))
            continue

        def cell(column):
            i = columns.get(column)
            return (cells[i] or None) if i is not None and i < len(cells) else None

        hours = None
        if "hours" in columns:
            hours = _hours(cell("hours"))
            if hours is None:
                report.append(_entry(site, "estimates_cell_bad", MESSAGES["cell_bad"].format(
                    name=name, line=number, cell=cell("hours") or "", n=n)))
        lo, hi = hours if hours is not None else (None, None)
        rows[key] = EstimatesRow(number, cell("effort"), lo, hi, cell("thinking"), cell("checking"), cell("flags"))
    return EstimatesFile(rows, tuple(report))
