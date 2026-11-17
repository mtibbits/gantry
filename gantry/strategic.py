"""The strategic page's model (#23): each site's backlog share against its
intended share, grouped under the business requirements it serves.

built_sites is the one definition of "built" every strategic figure uses
(summary status `built`, in config order). backlog_shares gives each built
site's open work as a share of all built sites' open work; strategic_model
adds the allocation that applies on the as-of date, the comparable intended
share (rescaled over the built sites), the gap between the two, the groups
and the headline. Everything is in config order or the business
requirements' file order, so the page is byte-repeatable. Shares and gaps
stay unrounded here: the view rounds. Nothing here reads a file: the model is
made from a BuildResult and the portfolio file the build read once.
docs/strategic-page.md states the rules.
"""

import math
from dataclasses import dataclass

from gantry.est_rules import classify_est
from gantry.inputs import scheduled
from gantry.links import page_href
from gantry.portfolio_file import intended_shares


def built_sites(result) -> tuple[str, ...]:
    """The names of the sites with summary status `built`, in config order."""
    return tuple(s.name for s in result.summaries if s.status == "built")


@dataclass(frozen=True)
class SiteBacklog:
    """A built site's open work: its hours, its share of all built sites'
    hours (percent, unrounded; None when that total is 0) and the counts the
    page qualifies them with."""

    hours: float
    share: float | None
    unestimated_count: int
    non_time: dict  # as Totals.non_time: {unit: {"quantity", "leaves"}}
    non_time_split: dict  # {unit: {"default": n, "estimates_file": m}}, n + m == non_time[unit]["leaves"]


@dataclass(frozen=True)
class Panel:
    site: str
    built: bool
    href: str | None  # the site's page from the output folder's top; None when not built
    reason: str | None  # why not built
    open_leaves: int | None
    held_out: int  # open leaves held out by a cycle: open, but in no hour figure
    backlog: SiteBacklog | None
    estimates: str | None  # the estimates file read, by name; None when none was read
    intended: int | float | None  # the applying allocation's share, as given; None when it has none
    comparable: float | None  # intended rescaled over the built sites' shares; None when not comparable
    gap: float | None  # |backlog share - comparable|, unrounded
    serves: tuple[str, ...]  # the business requirement IDs the portfolio file's serves lists, as text


@dataclass(frozen=True)
class Group:
    kind: str  # "br", "no_requirement", "no_share" or "not_built"
    br_id: str | None
    text: str | None  # the business requirement's text only
    sites: tuple[str, ...]
    intended_sum: int | float | None  # the members' shares as given; None when no member has one
    backlog_sum: float | None  # the built members' backlog shares; None when none is shown
    unestimated: int  # the built members' unestimated leaves


@dataclass(frozen=True)
class StrategicModel:
    as_of: object  # the build's as-of date
    configured: bool  # whether the config names a portfolio file
    allocation: object  # the Allocation that applies on as_of, or None
    panels: dict  # {site: Panel}, config order
    groups: tuple[Group, ...]
    total_hours: float
    total_unestimated: int
    off_not_built: int | float  # intended share on sites that were not built
    off_not_in_config: int | float  # intended share on names that are not sites
    headline: Panel | None  # the built site with the largest gap; the first in config order on a tie
    report: tuple  # the build's portfolio entries, in report order

    @property
    def projects(self) -> tuple[str, ...]:
        return tuple(self.panels)

    @property
    def backlog(self) -> dict:
        """{built site: SiteBacklog}, config order."""
        return {name: p.backlog for name, p in self.panels.items() if p.built}


def _split(data) -> dict:
    """{unit: {"default": n, "estimates_file": m}} over the scheduled leaves
    whose est: is a quantity in the site's own unit."""
    split = {}
    for leaf in scheduled(data):
        if leaf.estimate is None or leaf.estimate.est_kind != "non_time":
            continue
        unit = classify_est(leaf.meta.get("est")).unit
        counts = split.setdefault(unit, {"default": 0, "estimates_file": 0})
        counts["default" if leaf.estimate.source == "default" else "estimates_file"] += 1
    return split


def backlog_shares(result) -> dict[str, SiteBacklog]:
    """{built site: SiteBacklog}, in config order, from the same totals the
    site's summary took its hours from."""
    built = built_sites(result)
    total = sum(result.inputs[name].totals.hours for name in built)
    out = {}
    for name in built:
        inputs = result.inputs[name]
        totals = inputs.totals
        out[name] = SiteBacklog(totals.hours, totals.hours / total * 100 if total else None,
                                totals.unestimated_count, totals.non_time, _split(inputs.data))
    return out


def _panel(result, summary, backlog, shares, built_sum, portfolio) -> Panel:
    name = summary.name
    intended = shares.get(name)
    serves = tuple(str(rid) for rid in portfolio.serves.get(name, ())) if portfolio is not None else ()
    if summary.status != "built":
        return Panel(name, False, None, summary.reason, None, 0, None, None, intended, None, None, serves)
    inputs = result.inputs[name]
    site = backlog[name]
    comparable = intended / built_sum * 100 if intended is not None and built_sum > 0 else None
    gap = abs(site.share - comparable) if site.share is not None and comparable is not None else None
    return Panel(name, True, page_href(name, from_top=True), None, summary.open_leaves,
                 summary.open_leaves - len(scheduled(inputs.data)), site,
                 inputs.estimates.name if inputs.estimates is not None else None, intended, comparable, gap, serves)


def _group(kind, br_id, text, sites, panels) -> Group:
    given = [panels[s].intended for s in sites if panels[s].intended is not None]
    built = [panels[s].backlog for s in sites if panels[s].built]
    # A built site's share is None only when no built site has open hours, so all of them are or none are.
    site_shares = [b.share for b in built if b.share is not None]
    return Group(kind, br_id, text, tuple(sites), sum(given) if given else None,
                 sum(site_shares) if site_shares else None, sum(b.unestimated_count for b in built))


def strategic_model(result, as_of=None) -> StrategicModel:
    """The strategic page's model for a BuildResult, from the build's one
    read of the portfolio file and its report entries, at `as_of` (the
    config's as-of date when None)."""
    portfolio = result.portfolio
    as_of = as_of if as_of is not None else result.config.as_of
    projects = tuple(s.name for s in result.summaries)
    backlog = backlog_shares(result)
    built = tuple(backlog)
    allocation = intended_shares(portfolio, as_of) if portfolio is not None else None
    shares = allocation.shares if allocation is not None else {}
    built_sum = sum(shares[name] for name in built if name in shares)
    panels = {s.name: _panel(result, s, backlog, shares, built_sum, portfolio) for s in result.summaries}

    groups, placed = [], set()
    if portfolio is not None:
        for key, req in portfolio.brs.requirements.items():
            sites = [name for name in built if key in panels[name].serves]
            placed.update(sites)
            groups.append(_group("br", key, req.text, sites, panels))
    unplaced = [name for name in built if name not in placed]
    fixed = (("no_requirement", [name for name in unplaced if name in shares]),
             ("no_share", [name for name in unplaced if name not in shares]),
             ("not_built", [name for name in projects if name not in built]))
    groups += [_group(kind, None, None, sites, panels) for kind, sites in fixed if sites]

    headline = None
    for name in built:
        gap = panels[name].gap
        # Gaps a float hair apart are a tie (two built sites always tie), and a tie keeps config order.
        if gap is not None and (headline is None or (gap > headline.gap and not math.isclose(
                gap, headline.gap, rel_tol=1e-9, abs_tol=1e-9))):
            headline = panels[name]
    return StrategicModel(
        as_of=as_of, configured=portfolio is not None, allocation=allocation, panels=panels,
        groups=tuple(groups), total_hours=sum(b.hours for b in backlog.values()),
        total_unestimated=sum(b.unestimated_count for b in backlog.values()),
        off_not_built=sum(v for name, v in shares.items() if name in projects and name not in built),
        off_not_in_config=sum(v for name, v in shares.items() if name not in projects),
        headline=headline, report=tuple(result.portfolio_entries))
