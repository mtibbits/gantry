"""A site's optional inputs joined onto its parsed WBS.md (#10).

apply_inputs gives every open leaf an estimate, model tiers and flags from
what the site records, and computes the site's totals:

- hours: a time est: on the leaf, then its estimates-file row's hours, then
  the default unit (`unestimated`);
- model tiers, effort and flags: the leaf's estimates-file row, if any.

The totals cover the leaves the schedule plans (`scheduled`): open and not
held out by a cycle. Every input that is missing or bad is a report entry.
docs/worksite-page.md states the rules and the totals contract.
"""

from dataclasses import dataclass, replace
from datetime import date

from gantry.est_rules import classify_est
from gantry.estimates_file import find_estimates_file, read_estimates_file
from gantry.model import Estimate, Leaf, Models, ReportEntry, SiteData, Totals, report_order

NO_TIERS = "no per-leaf model tiers recorded"
UNEST_WHAT = {"absent": "no est:", "placeholder": "a placeholder est:", "non_time": "a non-time est:",
              "unparseable": "an unparseable est:"}
MESSAGES = {
    "est_unparseable": "WBS.md line {line}: est {raw!r} on {key!r} is not a time, a placeholder or a quantity "
                       "with a unit; it is not used",
    "unestimated": "WBS.md line {line}: {key!r} has {what} and no estimates-file hours; it is scheduled at the "
                   "default unit",
    "no_priority": "{n} open leaves have no priority and count as P3",
    "no_priority_one": "1 open leaf has no priority and counts as P3",
    "unmatched": "estimates file {name!r} line {line}: #{n} names no WBS.md line",
}


@dataclass(frozen=True)
class EstimatesSource:
    name: str
    date: date | None


@dataclass(frozen=True)
class Inputs:
    data: SiteData
    totals: Totals
    estimates: EstimatesSource | None  # None: no estimates file was read


def scheduled(data) -> list[Leaf]:
    """The leaves a page schedules: open and not held out by a cycle."""
    return [leaf for leaf in data.leaves if leaf.open and leaf.key not in data.held_out]


def has_tiers(leaves) -> bool:
    """Whether any of `leaves` records a model tier."""
    return any(leaf.models is not None and (leaf.models.thinking or leaf.models.checking) for leaf in leaves)


def site_totals(leaves, unit) -> Totals:
    """The totals over `leaves`; a leaf with no estimate counts at `unit`."""
    hours = lo = hi = 0
    unestimated = 0
    non_time = {}
    for leaf in leaves:
        est = leaf.estimate
        if est is None or est.source == "default":
            unestimated += 1
        a, b = (est.lo_h, est.hi_h) if est is not None else (unit, unit)
        hours += (a + b) / 2
        lo += a
        hi += b
        value = classify_est(leaf.meta.get("est"))
        if value.kind == "non_time":
            entry = non_time.setdefault(value.unit, {"quantity": 0, "leaves": 0})
            entry["quantity"] += value.number
            entry["leaves"] += 1
    return Totals(hours, lo, hi, unestimated, non_time)


def apply_inputs(roots, site, data, config) -> Inputs:
    """`data` with each open leaf's inputs filled in, its totals and the file
    read; the estimates file is found and read under the run's `roots`."""
    found = find_estimates_file(roots, site, config)
    report = list(found.report)
    rows, source = {}, None
    if found.path is not None:
        read = read_estimates_file(roots, found.path, site.name, found.name, key=found.key)
        report += read.report
        if not any(e.kind == "estimates_unreadable" for e in read.report):
            rows, source = read.rows, EstimatesSource(found.name, found.date)
    unit = config.default_unit_hours

    holders = {node.key: node for node in (*data.rows, *data.leaves)}
    for key, row in rows.items():
        if key not in holders:
            report.append(ReportEntry(site.name, None, "estimates_row_unmatched", MESSAGES["unmatched"].format(
                name=found.name, line=row.line, n=key.removeprefix("Issue-"))))

    held = set(data.held_out)
    leaves = []
    for leaf in data.leaves:
        if not leaf.open:
            leaves.append(leaf)
            continue
        raw = leaf.meta.get("est")
        value = classify_est(raw)
        row = rows.get(leaf.key)
        effort = row.effort if row is not None else None
        if value.kind == "time":
            estimate = Estimate(value.lo_h, value.hi_h, "est", value.kind, effort)
        elif row is not None and row.lo_h is not None:
            estimate = Estimate(row.lo_h, row.hi_h, "estimates_file", value.kind, effort)
        else:
            estimate = Estimate(unit, unit, "default", value.kind, effort)
        models = None
        if row is not None and (row.thinking or row.checking):
            models = Models(row.thinking, row.checking, "estimates_file")
        leaves.append(replace(leaf, estimate=estimate, models=models, flags=row.flags if row else None))
        if value.kind == "unparseable":
            report.append(ReportEntry(site.name, leaf.line, "est_unparseable", MESSAGES["est_unparseable"].format(
                line=leaf.line, raw=raw, key=leaf.key)))
        if estimate.source == "default" and leaf.key not in held:
            report.append(ReportEntry(site.name, leaf.line, "unestimated", MESSAGES["unestimated"].format(
                line=leaf.line, key=leaf.key, what=UNEST_WHAT[value.kind])))

    no_priority = sum(leaf.open and leaf.priority is None for leaf in leaves)
    if no_priority:
        message = MESSAGES["no_priority_one"] if no_priority == 1 else MESSAGES["no_priority"].format(n=no_priority)
        report.append(ReportEntry(site.name, None, "no_priority", message))
    data = replace(data, leaves=tuple(leaves))
    work = scheduled(data)
    if not has_tiers(work):
        report.append(ReportEntry(site.name, None, "no_model_tiers", NO_TIERS))
    report = data.report + tuple(report)
    data = replace(data, report=tuple(sorted(report, key=report_order)))
    return Inputs(data, site_totals(work, unit), source)
