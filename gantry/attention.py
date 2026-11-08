"""A leaf's attention need and rate (#36): operator hours per agent hour.

A leaf's `h:` score, if it is one ASCII digit from 0 to 4 written as a
scalar, is the leaf's need. check_h reports every `h:` that is not, on any
node in any state, as `bad_h`. docs/week-plan.md states the rules.
"""

import math
from dataclasses import dataclass, replace

# Calls go through `model.meta_values`, never a name bound at import, so #12's
# parser change and a test's spy both reach this module.
from gantry import model
from gantry.model import ReportEntry, report_order

H_DIGITS = ("0", "1", "2", "3", "4")
# Operator hours per agent hour for need 0 to 4, unless the portfolio file sets its own.
DEFAULT_MAPPING = (0.0, 0.1, 0.25, 0.5, 1.0)
# No rate, in the mapping or as a site ratio, may be above this.
MAX_RATE = 24
MAPPING_KEY_PATH = "capacity.attention_mapping"
MAPPING_REJECTED = "the portfolio file's attention_mapping was rejected (see the portfolio file's report)"
MAPPING_NOT_FINITE = "the portfolio file's attention_mapping holds a value that is not finite"
MAPPING_ABOVE_MAX = f"the portfolio file's attention_mapping holds a value above {MAX_RATE}"
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


def rate_problem(value) -> str | None:
    """Why `value` cannot be a rate, or None: a number from 0 to MAX_RATE.

    Never raises: an int is never passed to math.isfinite, which overflows on
    a 400-digit one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "not a number"
    if isinstance(value, float) and not math.isfinite(value):
        return "not finite"
    if value < 0:
        return "negative"
    if value > MAX_RATE:
        return f"above {MAX_RATE}"
    return None


@dataclass(frozen=True)
class AttentionMapping:
    values: tuple[float, ...]  # the rate for need 0 to 4
    source: str  # "default" or "portfolio_file"
    rejected: str | None  # why the portfolio file's mapping is not the one in use


def mapping_in_use(portfolio) -> AttentionMapping:
    """The mapping a plan uses: the portfolio file's, if it set a usable one, else the default.

    `portfolio` is a read portfolio file or None; only its
    capacity.attention_mapping and its report are read.
    """
    if portfolio is None:
        return AttentionMapping(DEFAULT_MAPPING, "default", None)
    values = portfolio.capacity.attention_mapping
    if values is None:
        rejected = any(e.kind == "bad_value" and e.key_path == MAPPING_KEY_PATH for e in portfolio.report)
        return AttentionMapping(DEFAULT_MAPPING, "default", MAPPING_REJECTED if rejected else None)
    problems = [rate_problem(v) for v in values]
    if "not finite" in problems:
        return AttentionMapping(DEFAULT_MAPPING, "default", MAPPING_NOT_FINITE)
    if f"above {MAX_RATE}" in problems:
        return AttentionMapping(DEFAULT_MAPPING, "default", MAPPING_ABOVE_MAX)
    return AttentionMapping(tuple(float(v) for v in values), "portfolio_file", None)
