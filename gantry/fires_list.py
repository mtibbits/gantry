"""The fires list: an installation's small private Markdown file of fires
that are not leaves yet (#30). docs/fires-list.md states the rules.

The module owns its report kinds and its report entry, never
gantry.model's: the fire collector (#32) merges the two reports. Every
`line` is a 1-based file line. No message holds a path: the file is always
"the fires list", named by its config key.
"""

import re
from dataclasses import dataclass
from datetime import date as Date
from datetime import time as Time

from gantry.requirements import FENCE_RE, fence_spans, split_lines
from gantry.sites import shown

# Whitespace here is space and tab only: never split(), strip() or
# splitlines() with no argument, \s or \d, which also match U+2028, U+0085,
# a lone CR or non-ASCII digits. Every pattern is linear on a long line.
THEMATIC_RE = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")  # fullmatch
TOP_ITEM_RE = re.compile(r"(?P<indent> {0,3})[-*+][ \t]+(?P<text>.*)")  # fullmatch; "- " alone has empty text
ENTRY_LIKE_RE = re.compile(r"[ \t]*(?:(?:[-*+]|[0-9]+[.)])[ \t]*)?(?P<text>(?:\[.\]|[0-9]{4}-).*)")  # fullmatch
BOX_RE = re.compile(r"\[(?P<glyph>.)\]")  # match, at the start of the text
DATE_TRY_RE = re.compile(r"[0-9]{4}-")  # match, at the start of a token
_BLANKS = " \t"

UNCLOSED_FENCE = "a fence opened here is never closed; the lines after it are still read"
NOT_TOP_LEVEL = "not a top-level `- ` item"

REPORT_KINDS = ("fires_list_unreadable", "fires_bad_entry", "fires_unclosed_fence", "fires_unknown_site",
                "fires_future_date")
_KIND_ORDER = {kind: i for i, kind in enumerate(REPORT_KINDS)}


@dataclass(frozen=True)
class FiresReportEntry:  # the fires list's own; never gantry.model's
    site: None  # always None: the file is not a site
    line: int | None  # None only for fires_list_unreadable
    kind: str
    message: str


def report_order(entries) -> list:
    """The entries in report order: the whole-file entry first, then by line,
    then by REPORT_KINDS order."""
    return sorted(entries, key=lambda e: (e.line is not None, e.line or 0, _KIND_ORDER[e.kind]))


@dataclass(frozen=True)
class FireEntry:
    line: int
    state: str  # open, resolved, promoted or unreadable
    ended: bool  # the box parsed as resolved or promoted, readable or not
    date: Date | None
    time: Time | None
    site: str | None  # as written, never trimmed or escaped
    site_known: bool
    reason: str | None  # as written, never trimmed or escaped
    raw: str  # the line as split: no BOM, no CR or LF


@dataclass(frozen=True)
class FiresList:
    entries: tuple  # FireEntry, in file order
    status: str  # read, read_with_problems, not_read or not_configured
    reason: str | None  # for not_read and not_configured
    report: tuple  # FiresReportEntry, in report order
    skipped_empty: int  # items that held nothing
    skipped_in_fences: int  # entry-shaped lines inside closed fences


# --- lines -----------------------------------------------------------------------


def _blank(text):
    return text.strip(_BLANKS) == ""


def _holds_nothing(text):
    """Empty, only spaces and tabs, or a box shape then only spaces and tabs."""
    if BOX_RE.match(text):
        text = text[3:]
    return _blank(text)


def _starts_entry_shape(text):
    return BOX_RE.match(text) is not None or DATE_TRY_RE.match(text) is not None


def _opens_fence(line):
    """#8's opener: a fence run, but not a backtick run whose info holds a backtick."""
    m = FENCE_RE.fullmatch(line)
    return m is not None and not (m["run"][0] == "`" and "`" in m["info"])


def _classify(line, chain):
    """("blank" | "break" | "empty" | "note" | "entry" | "entry_like" | "prose", text)."""
    if _blank(line):
        return "blank", None
    if THEMATIC_RE.fullmatch(line):
        return "break", None
    m = TOP_ITEM_RE.fullmatch(line)
    if m is not None:
        text = m["text"]
        if _holds_nothing(text):
            return "empty", None
        if len(m["indent"]) >= 2 and not _starts_entry_shape(text) and chain:
            return "note", None
        return "entry", text
    m = ENTRY_LIKE_RE.fullmatch(line)
    if m is not None:
        text = m["text"]
        if _holds_nothing(text):
            return "empty", None
        return "entry_like", text
    return "prose", None


def _shaped_in_fence(line):
    """Whether a fenced line, read alone, is a marker line holding a box or a date."""
    kind, text = _classify(line, False)
    return kind == "entry_like" or (kind == "entry" and _starts_entry_shape(text))


# --- the file ----------------------------------------------------------------------


_BOX_STATES = {" ": "open", "x": "resolved", "X": "resolved", ">": "promoted"}
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # fullmatch
TIME_RE = re.compile(r"(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2})")  # fullmatch


class _Cursor:
    """The text after an item's marker, read a token at a time. A token is a
    maximal run of characters that are not a space or a tab."""

    def __init__(self, text):
        self.text, self.pos = text, 0

    def skip_blanks(self):
        while self.pos < len(self.text) and self.text[self.pos] in _BLANKS:
            self.pos += 1

    def token(self):
        end = self.pos
        while end < len(self.text) and self.text[end] not in _BLANKS:
            end += 1
        return self.text[self.pos:end]

    def consume(self, token):
        self.pos += len(token)
        self.skip_blanks()


def _read_date(token):
    if DATE_RE.fullmatch(token):
        try:
            # Never date.fromisoformat, which takes other shapes.
            return Date(int(token[0:4]), int(token[5:7]), int(token[8:10]))
        except ValueError:
            pass
    return None


def _read_entry(number, raw, text, entry_like):
    """(FireEntry, failure phrases). A token with a field's shape is consumed
    even when its value is invalid; a missing required field consumes nothing."""
    failures = [NOT_TOP_LEVEL] if entry_like else []
    cur = _Cursor(text)
    state = ended = None
    box = BOX_RE.match(text)
    if box is None:
        failures.append("no state box")
    else:
        glyph = box["glyph"]
        state = _BOX_STATES.get(glyph)
        if state is None:
            failures.append(f"state box [{shown(glyph)}] is not [ ], [x] or [>]")
        ended = state in ("resolved", "promoted")
        cur.pos = 3
        if cur.pos < len(text) and text[cur.pos] not in _BLANKS:
            failures.append("no space after the state box")
        cur.skip_blanks()

    day = None
    token = cur.token()
    if DATE_TRY_RE.match(token):
        day = _read_date(token)
        if day is None:
            failures.append(f"date {shown(token)} is not a real YYYY-MM-DD date")
        cur.consume(token)
    else:
        failures.append("no date")

    at = None
    token = cur.token()
    m = TIME_RE.fullmatch(token)
    if m is not None:
        hour, minute = int(m["hour"]), int(m["minute"])
        if hour <= 23 and minute <= 59:
            at = Time(hour, minute)
        else:
            failures.append(f"time {shown(token)} is not a time from 0:00 to 23:59")
        cur.consume(token)

    site = None
    token = cur.token()
    if token.startswith("@"):
        if token == "@":
            failures.append("an empty @ names no site")
        else:
            site = token[1:]
        cur.consume(token)

    reason = text[cur.pos:]
    if _blank(reason):
        reason = None
        failures.append("no reason")

    if failures:
        state = "unreadable"
    return FireEntry(number, state, bool(ended), day, at, site, False, reason, raw), failures


def parse_fires_list(text, site_names, reference) -> FiresList:
    """Read a fires list's text. `site_names` is every name in the site list,
    built or not; `reference` is a naive local datetime from the caller (this
    module never reads the clock)."""
    lines = split_lines(text)
    fenced = [False] * len(lines)
    skipped_in_fences = 0
    for opener, closer in fence_spans(lines):
        for i in range(opener, closer + 1):
            fenced[i] = True
        skipped_in_fences += sum(1 for i in range(opener + 1, closer) if _shaped_in_fence(lines[i]))
    entries, report = [], []
    skipped_empty = 0
    chain = False
    for i, line in enumerate(lines):
        number = i + 1
        if fenced[i]:
            chain = False
            continue
        if _opens_fence(line):
            report.append(FiresReportEntry(None, number, "fires_unclosed_fence", UNCLOSED_FENCE))
        kind, item = _classify(line, chain)
        if kind == "blank":
            continue
        if kind in ("entry", "entry_like"):
            entry, failures = _read_entry(number, line, item, kind == "entry_like")
            entries.append(entry)
            if failures:
                report.append(FiresReportEntry(None, number, "fires_bad_entry", "; ".join(failures)))
        elif kind == "empty":
            skipped_empty += 1
        chain = kind in ("entry", "entry_like", "note")
    report = tuple(report_order(report))
    status = "read_with_problems" if report else "read"
    return FiresList(tuple(entries), status, None, report, skipped_empty, skipped_in_fences)
