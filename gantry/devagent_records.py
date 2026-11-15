"""devagent's per-issue records, read (#31). docs/devagent-formats.md states the rules.

The one Gantry module that reads devagent's STUCK markers, checklists and
plans. The three text readers (read_stuck, read_checklist, read_plan) take
text and never raise on any str; read_checklist also reads a committed
version passed in as text. The two file-level readers (read_issue_records,
read_site_records) read through gantry.roots and follow no link. Callers:
the fire collector (#32) reads each site once per build and hands the same
SiteRecords to the warm-up list (#33) and the time thieves (#29). Record
text comes back verbatim: escaping happens once, where a page renders it.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime

from gantry.config import error_reason
from gantry.roots import OutsideRoots, list_confined, read_bytes_confined

# Whitespace here is space and tab only, and every digit run that reaches
# int() is at most nine ASCII digits: never \s, \d, split() with no argument
# or str.splitlines(), which also match U+2028, U+2029, a lone CR or
# non-ASCII digits.
KEY_RE = re.compile(r"(Step|Reason|Last good|Created):[ \t]*(.*)")  # fullmatch
STUCK_STEP_RE = re.compile(r"([0-9]{1,9})[ \t]+(.+)")  # fullmatch
LOCAL_MINUTES_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")  # fullmatch
ISO_OFFSET_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}")  # fullmatch

LOCAL_MINUTES = "local_minutes"
ISO_OFFSET = "iso_offset"
UNPARSEABLE = "unparseable"
NO_LAST_GOOD = "(none)"

STUCK_NO_STEP = "stuck_no_step"
STUCK_NO_REASON = "stuck_no_reason"
STUCK_NO_CREATED = "stuck_no_created"
STUCK_CREATED_UNPARSEABLE = "stuck_created_unparseable"


def text_lines(text: str) -> list[str]:
    """The lines of a record's text: one leading U+FEFF dropped, split on
    "\\n" only, one trailing "\\r" dropped per line. A final newline leaves
    no empty last line."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _blank(line: str) -> bool:
    return line.strip(" \t") == ""


# --- STUCK markers ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StuckMarker:
    step: int | None
    step_name: str | None
    reason: str | None
    last_good: str | None
    created: datetime | None
    created_format: str
    problems: tuple[str, ...]


def _key(line: str, name: str) -> str | None:
    match = KEY_RE.fullmatch(line)
    return match.group(2) if match and match.group(1) == name else None


def parse_created(value: str) -> tuple[datetime | None, str]:
    """(created, created_format) for a STUCK `Created:` value."""
    try:
        if LOCAL_MINUTES_RE.fullmatch(value):
            return datetime.strptime(value, "%Y-%m-%d %H:%M"), LOCAL_MINUTES
        if ISO_OFFSET_RE.fullmatch(value):
            return datetime.fromisoformat(value), ISO_OFFSET
    except ValueError:
        pass
    return None, UNPARSEABLE


def read_stuck(text: str) -> StuckMarker:
    """A STUCK marker's fields. Both devagent writers put `Created:` last and
    `Last good:` (when written) just before it, so the trailer is read from
    the end: the last line, when a `Created:` line, is the key; then the line
    now last, when a `Last good:` line, is the key. `Step:` is the first
    line. The reason is the first `Reason:` line left and every line after
    it, kept as written, so a `Created:` line inside a reason is reason
    text."""
    lines = text_lines(text)
    while lines and _blank(lines[-1]):
        lines.pop()
    problems = []

    created, created_format = None, UNPARSEABLE
    has_created = False
    if lines and (value := _key(lines[-1], "Created")) is not None:
        lines.pop()
        has_created = True
        created, created_format = parse_created(value)
    last_good = None
    if lines and (value := _key(lines[-1], "Last good")) is not None:
        lines.pop()
        last_good = None if value == NO_LAST_GOOD else value

    step = step_name = None
    if lines and (value := _key(lines[0], "Step")) is not None:
        lines.pop(0)
        match = STUCK_STEP_RE.fullmatch(value)
        if match:
            step, step_name = int(match.group(1)), match.group(2)
    if step is None:
        problems.append(STUCK_NO_STEP)

    reason = None
    for i, line in enumerate(lines):
        value = _key(line, "Reason")
        if value is not None:
            reason = "\n".join([value, *lines[i + 1:]])
            break
    if reason is None:
        problems.append(STUCK_NO_REASON)

    if not has_created:
        problems.append(STUCK_NO_CREATED)
    elif created is None:
        problems.append(STUCK_CREATED_UNPARSEABLE)
    return StuckMarker(step, step_name, reason, last_good, created, created_format, tuple(problems))


# --- checklists ------------------------------------------------------------------------------

# devagent's step line (scripts/lib/checklist.sh:68 at the pin), with
# [[:space:]] as space, tab, form feed and vertical tab, and the step number
# bounded. The name is the token only, as devagent reads it.
STEP_RE = re.compile(r"- \[(.)\][ \t\f\v]+([0-9]{1,9})\.[ \t\f\v]+([A-Za-z][A-Za-z0-9_-]*)")  # match
REVISION_PREFIX = "## Revision "
REVISION_RE = re.compile(r"## Revision ([0-9]{1,9})(?![0-9])")  # match
GLYPHS = " x-!~?P"
DONE_GLYPHS = "x-"
PROGRESS_GLYPHS = "x~"
PARKED_GLYPH = "P"
STUCK_GLYPH = "!"

CHECKLIST_NO_STEPS = "checklist_no_steps"
CHECKLIST_ACTIVE_BLOCK_EMPTY = "checklist_active_block_empty"
CHECKLIST_BAD_GLYPH = "checklist_bad_glyph"


@dataclass(frozen=True)
class Step:
    number: int
    name: str
    glyph: str


@dataclass(frozen=True)
class ChecklistState:
    next_step: Step | None
    parked_step: Step | None
    stuck_step: Step | None
    all_done: bool
    shows_progress: bool
    revision: int | None
    problems: tuple[str, ...]

    @property
    def parked(self) -> bool:
        return self.parked_step is not None


def _steps(lines) -> list[Step]:
    steps = []
    for line in lines:
        match = STEP_RE.match(line)
        if match:
            steps.append(Step(int(match.group(2)), match.group(3), match.group(1)))
    return steps


def _first_glyph(active, everything, glyph) -> Step | None:
    """The first row carrying `glyph` in the active block, else in the whole
    file: devagent's checklist_find_glyph_line."""
    for steps in (active, everything):
        for step in steps:
            if step.glyph == glyph:
                return step
    return None


def read_checklist(text: str) -> ChecklistState:
    """A checklist's state, read as devagent reads it: the active block is
    every line after the last `## Revision ` heading (the whole file with
    none); the next step is its first row not `x` or `-`."""
    lines = text_lines(text)
    start, revision = 0, None
    for i, line in enumerate(lines):
        if line.startswith(REVISION_PREFIX):
            start = i + 1
            match = REVISION_RE.match(line)
            revision = int(match.group(1)) if match else None
    everything = _steps(lines)
    active = _steps(lines[start:])
    problems = []
    if not everything:
        problems.append(CHECKLIST_NO_STEPS)
    elif not active:
        problems.append(CHECKLIST_ACTIVE_BLOCK_EMPTY)
    if any(step.glyph not in GLYPHS for step in everything):
        problems.append(CHECKLIST_BAD_GLYPH)
    next_step = next((step for step in active if step.glyph not in DONE_GLYPHS), None)
    return ChecklistState(
        next_step=next_step,
        parked_step=_first_glyph(active, everything, PARKED_GLYPH),
        stuck_step=_first_glyph(active, everything, STUCK_GLYPH),
        all_done=bool(active) and next_step is None,
        shows_progress=any(step.glyph in PROGRESS_GLYPHS for step in everything),
        revision=revision,
        problems=tuple(problems),
    )


# --- plans -----------------------------------------------------------------------------------

TASKS_HEADING_RE = re.compile(r"## Tasks[ \t]*")  # fullmatch
CHECKBOX_RE = re.compile(r"- \[(.)\] (.*)")  # fullmatch; column 0 only
TASK_HEADING_RE = re.compile(r"#{2,4}[ \t]+(Task[ \t]+[0-9].*)")  # fullmatch; a digit after Task
NUMBERED_RE = re.compile(r"[0-9]+[.)][ \t]+(.*)")  # fullmatch
TICKED = "xX"

CHECKBOX = "checkbox"
HEADINGS = "headings"
NUMBERED = "numbered"
UNRECOGNISED = "unrecognised"
NONE = "none"
SHAPE_RES = ((CHECKBOX, CHECKBOX_RE), (HEADINGS, TASK_HEADING_RE), (NUMBERED, NUMBERED_RE))

PLAN_TASKS_UNRECOGNISED = "plan_tasks_unrecognised"


@dataclass(frozen=True)
class PlanState:
    shape: str
    next_task: str | None
    marks_done: bool
    problems: tuple[str, ...]


def _headings(lines) -> PlanState:
    for line in lines:
        match = TASK_HEADING_RE.fullmatch(line)
        if match:
            return PlanState(HEADINGS, match.group(1), False, ())
    return PlanState(NONE, None, False, ())


def read_plan(text: str) -> PlanState:
    """A plan's task list. In the `## Tasks` section (to the next line
    starting `## `) the first line of a task shape (check box, `Task N`
    heading, numbered item; column 0) fixes the shape, and only lines of that
    shape are tasks. With no section, or one with no task-shaped line that a
    level-2 `## Task N` heading ends, a `Task N` heading anywhere gives
    `headings`. Code fences are not interpreted."""
    lines = text_lines(text)
    start = next((i for i, line in enumerate(lines) if TASKS_HEADING_RE.fullmatch(line)), None)
    if start is None:
        return _headings(lines)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = lines[start + 1:end]
    found = next(((shape, pattern) for line in section for shape, pattern in SHAPE_RES if pattern.fullmatch(line)),
                 None)
    if found is None:
        if end < len(lines) and TASK_HEADING_RE.fullmatch(lines[end]):
            return _headings(lines)
        return PlanState(UNRECOGNISED, None, False, (PLAN_TASKS_UNRECOGNISED,))
    shape, pattern = found
    matches = [m for m in (pattern.fullmatch(line) for line in section) if m]
    if shape == CHECKBOX:
        open_task = next((m.group(2) for m in matches if m.group(1) not in TICKED), None)
        return PlanState(CHECKBOX, open_task, any(m.group(1) in TICKED for m in matches), ())
    return PlanState(shape, matches[0].group(1), False, ())


# --- issue folders and planning directories -----------------------------------------------

KEY = "devdoc_dir"
STUCK_FILE = "STUCK"
CHECKLIST_FILE = "checklist.md"
PLAN_FILE = "imPlan.md"
RECORD_FILES = ((STUCK_FILE, "stuck", read_stuck), (CHECKLIST_FILE, "checklist", read_checklist),
                (PLAN_FILE, "plan", read_plan))

PRESENT = "present"
ABSENT = "absent"
UNREADABLE = "unreadable"
READ = "read"
NOT_READ = "not_read"

UNKNOWN_ERROR = "unknown error"
FILE_IS_LINK = "{name} is a link"
FILE_NOT_FILE = "{name} is not a file"
FILE_CANNOT_READ = "{name} cannot be read: {error}"
FILE_NOT_UTF8 = "{name} is not valid UTF-8 at byte {offset}"
FOLDER_MISSING = "issue folder does not exist"
FOLDER_NOT_DIR = "issue folder is not a directory"
FOLDER_IS_LINK = "issue folder is a link"
FOLDER_CANNOT_READ = "issue folder cannot be read: {error}"
PLANNING_MISSING = "planning directory does not exist"
PLANNING_NOT_DIR = "planning directory is not a directory"
PLANNING_CANNOT_LIST = "planning directory cannot be listed: {error}"
SKIPPED_LINK = "is a link"
REASONS = (FILE_IS_LINK, FILE_NOT_FILE, FILE_CANNOT_READ, FILE_NOT_UTF8, FOLDER_MISSING, FOLDER_NOT_DIR,
           FOLDER_IS_LINK, FOLDER_CANNOT_READ, PLANNING_MISSING, PLANNING_NOT_DIR, PLANNING_CANNOT_LIST,
           SKIPPED_LINK)


@dataclass(frozen=True)
class Record:
    status: str  # PRESENT, ABSENT or UNREADABLE
    value: object  # the parsed StuckMarker, ChecklistState or PlanState when present
    reason: str | None  # why it is unreadable; never an absolute path


@dataclass(frozen=True)
class IssueRecords:
    issue: str  # the folder's name, as on disk
    stuck: Record
    checklist: Record
    plan: Record


@dataclass(frozen=True)
class SiteRecords:
    status: str  # READ or NOT_READ
    reason: str | None
    issues: dict  # folder name -> IssueRecords, in sorted name order
    skipped: dict  # folder name -> reason, in sorted name order


def _why(e: Exception) -> str:
    return error_reason(e) or UNKNOWN_ERROR


def _list(roots, path, site):
    """list_confined, tried twice on FileNotFoundError: an entry devagent
    renames away between the listing and its lstat must not read as a
    missing folder."""
    try:
        return list_confined(roots, path, key=KEY, site=site)
    except FileNotFoundError:
        return list_confined(roots, path, key=KEY, site=site)


def _all_unreadable(issue: str, reason: str) -> IssueRecords:
    record = Record(UNREADABLE, None, reason)
    return IssueRecords(issue, record, record, record)


def _record(roots, folder, entries, name, reader, site) -> Record:
    entry = entries.get(name)
    if entry is None:
        return Record(ABSENT, None, None)
    if entry.is_link:
        return Record(UNREADABLE, None, FILE_IS_LINK.format(name=name))
    if not entry.is_file:
        return Record(UNREADABLE, None, FILE_NOT_FILE.format(name=name))
    try:
        data = read_bytes_confined(roots, os.path.join(folder, name), key=KEY, site=site, follow_links=False)
    except (OSError, ValueError, OutsideRoots) as e:
        return Record(UNREADABLE, None, FILE_CANNOT_READ.format(name=name, error=_why(e)))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return Record(UNREADABLE, None, FILE_NOT_UTF8.format(name=name, offset=e.start))
    return Record(PRESENT, reader(text), None)


def _read_folder(roots, folder: str, issue: str, site) -> IssueRecords:
    """The three records of a folder already known not to be a link."""
    try:
        listed = _list(roots, folder, site)
    except FileNotFoundError:
        return _all_unreadable(issue, FOLDER_MISSING)
    except NotADirectoryError:
        return _all_unreadable(issue, FOLDER_NOT_DIR)
    except (OSError, ValueError, OutsideRoots) as e:
        return _all_unreadable(issue, FOLDER_CANNOT_READ.format(error=_why(e)))
    entries = {entry.name: entry for entry in listed}
    stuck, checklist, plan = (_record(roots, folder, entries, name, reader, site)
                              for name, _, reader in RECORD_FILES)
    return IssueRecords(issue, stuck, checklist, plan)


def _find(entries, name):
    """The entry named `name`; by the platform's case rule when no name matches exactly."""
    for entry in entries:
        if entry.name == name:
            return entry
    folded = os.path.normcase(name)
    return next((entry for entry in entries if os.path.normcase(entry.name) == folded), None)


def read_issue_records(roots, issue_dir, *, site=None) -> IssueRecords:
    """The STUCK, checklist.md and imPlan.md records of one issue folder.

    Every listing and read goes through gantry.roots under the devdoc_dir
    key and follows no link: a linked issue folder or record file is
    unreadable and its target is never opened. Never raises for a missing,
    odd or refused path."""
    folder = os.path.normpath(os.fspath(issue_dir))
    issue = os.path.basename(folder)
    parent = os.path.dirname(folder) or "."
    try:
        siblings = _list(roots, parent, site)
    except OutsideRoots:
        siblings = None  # the issue folder may be a root itself; it is listed below
    except FileNotFoundError:
        return _all_unreadable(issue, FOLDER_MISSING)
    except (OSError, ValueError) as e:
        return _all_unreadable(issue, FOLDER_CANNOT_READ.format(error=_why(e)))
    if siblings is not None:
        entry = _find(siblings, issue)
        if entry is None:
            return _all_unreadable(issue, FOLDER_MISSING)
        if entry.is_link:
            return _all_unreadable(issue, FOLDER_IS_LINK)
        if not entry.is_dir:
            return _all_unreadable(issue, FOLDER_NOT_DIR)
    return _read_folder(roots, folder, issue, site)


def read_site_records(roots, planning_dir, *, site=None) -> SiteRecords:
    """The records of every direct subfolder of a planning directory that
    holds at least one of the three files, keyed by its name on disk. A
    linked subfolder is not read and is listed in `skipped`; nothing deeper
    is listed."""
    try:
        listed = _list(roots, planning_dir, site)
    except FileNotFoundError:
        return SiteRecords(NOT_READ, PLANNING_MISSING, {}, {})
    except NotADirectoryError:
        return SiteRecords(NOT_READ, PLANNING_NOT_DIR, {}, {})
    except (OSError, ValueError, OutsideRoots) as e:
        return SiteRecords(NOT_READ, PLANNING_CANNOT_LIST.format(error=_why(e)), {}, {})
    issues, skipped = {}, {}
    for entry in sorted(listed, key=lambda e: e.name):
        if entry.is_link:
            if not entry.is_file:
                skipped[entry.name] = SKIPPED_LINK
            continue
        if not entry.is_dir:
            continue
        records = _read_folder(roots, os.path.join(os.fspath(planning_dir), entry.name), entry.name, site)
        if any(record.status != ABSENT for record in (records.stuck, records.checklist, records.plan)):
            issues[entry.name] = records
    return SiteRecords(READ, None, issues, skipped)


PROBLEM_KINDS = (STUCK_NO_STEP, STUCK_NO_REASON, STUCK_NO_CREATED, STUCK_CREATED_UNPARSEABLE, CHECKLIST_NO_STEPS,
                 CHECKLIST_ACTIVE_BLOCK_EMPTY, CHECKLIST_BAD_GLYPH, PLAN_TASKS_UNRECOGNISED)
