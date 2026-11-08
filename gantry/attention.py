"""A leaf's attention need and rate (#36): operator hours per agent hour.

A leaf's `h:` score, if it is one ASCII digit from 0 to 4 written as a
scalar, is the leaf's need. check_h reports every `h:` that is not, on any
node in any state, as `bad_h`. docs/week-plan.md states the rules.
"""

from dataclasses import replace

# Calls go through `model.meta_values`, never a name bound at import, so #12's
# parser change and a test's spy both reach this module.
from gantry import model
from gantry.model import ReportEntry, report_order

H_DIGITS = ("0", "1", "2", "3", "4")
MESSAGES = {
    "bad_h": "WBS.md line {line}: h: {raw!r} on {key!r} is not one digit from 0 to 4; it is not used",
    # A message, not a report kind: the entry's kind is bad_h.
    "h_on_row": "WBS.md line {line}: h: {raw!r} on {key!r} is on a row with children, not a leaf; it is not used",
}


def read_h(leaf) -> int | None:
    """The leaf's `h:` score, or None when it has none or it is not one digit from 0 to 4.

    Never raises: a value that is not a string (a #12 list, or anything else)
    or is bracket text (#8's list form) is not a scalar.
    """
    raw = leaf.meta.get("h")
    if raw is None or not isinstance(raw, str) or raw.startswith("["):
        return None
    values = model.meta_values(raw)
    return int(values[0]) if len(values) == 1 and values[0] in H_DIGITS else None


def check_h(data, site: str):
    """`data` with one `bad_h` entry per unusable `h:`, its report re-sorted.

    Every leaf is checked whatever its state, and every row that holds `h:`
    is reported: `h:` belongs on a leaf. Not idempotent: a second call adds
    the entries again.
    """
    new = []
    for leaf in data.leaves:
        if "h" in leaf.meta and read_h(leaf) is None:
            new.append(ReportEntry(site, leaf.line, "bad_h", MESSAGES["bad_h"].format(
                line=leaf.line, raw=leaf.meta["h"], key=leaf.key)))
    for row in data.rows:
        if "h" in row.meta:
            new.append(ReportEntry(site, row.line, "bad_h", MESSAGES["h_on_row"].format(
                line=row.line, raw=row.meta["h"], key=row.key)))
    return replace(data, report=tuple(sorted(data.report + tuple(new), key=report_order)))
