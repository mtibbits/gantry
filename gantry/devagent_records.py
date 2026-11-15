"""devagent's per-issue records, read (#31). docs/devagent-formats.md states the rules.

The one Gantry module that reads devagent's STUCK markers, checklists and
plans. The three text readers (read_stuck, read_checklist, read_plan) take
text and never raise on any str. Record text comes back verbatim: escaping
happens once, where a page renders it.
"""

import re
from dataclasses import dataclass
from datetime import datetime

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
    if lines and _key(lines[-1], "Created") is not None:
        has_created = True
        created, created_format = parse_created(_key(lines.pop(), "Created"))
    last_good = None
    if lines and _key(lines[-1], "Last good") is not None:
        value = _key(lines.pop(), "Last good")
        last_good = None if value == NO_LAST_GOOD else value

    step = step_name = None
    if lines and _key(lines[0], "Step") is not None:
        match = STUCK_STEP_RE.fullmatch(_key(lines.pop(0), "Step"))
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
