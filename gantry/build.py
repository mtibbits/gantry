"""The full build: read every site, then render every read site (#11).

build_all reads each site in devagent's config order, then renders each site
it read, each in its own `try`, and returns one BuildResult that every view
reads. It writes nothing; write_all writes the pages and the index. A site
that cannot be built gets a reason that names its config key or file kind,
never an absolute path. docs/worksite-page.md states the build.
"""

import stat
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from gantry.attention import check_h
from gantry.config import Config, error_reason
from gantry.crosssite import resolve_foreign
from gantry.inputs import Inputs, apply_inputs
from gantry.model import ForeignOutcome
from gantry.requirements import in_trace_sites
from gantry.roots import OutsideRoots, Roots, mkdir_confined, stat_confined, write_confined
from gantry.sites import SiteEntry, SiteWarning, config_roots, load_sites
from gantry.stamp import public_text, stamp_text
from gantry.trace import build_site_trace, counts_line, load_catalog, page_trace
from gantry.views import worksite
from gantry.views.index import render_index
from gantry.wbs_stopgap import read_wbs

CLASH = "name clashes with {earlier} on a case-insensitive file system"
RENDER_ERROR = "render error: {type}: {message}"
RENDER_ERROR_BARE = "render error: {type}"
WRITE_ERROR = "cannot write sites/{name}.html: {why}"
NOT_A_FOLDER = "{what} is not a folder"


@dataclass(frozen=True)
class SiteSummary:
    """One site's line on the index, in the issue's field order; None where not built."""

    name: str
    status: str  # "built" or "not_built"
    reason: str | None = None
    open_leaves: int | None = None
    finish_day: int | None = None  # the working day the plan ends in, at the default lanes
    finish_date: date | None = None
    unestimated_count: int | None = None
    report_count: int | None = None
    hours: float | None = None  # not on the index; carried for Epic #3
    non_time: dict | None = None
    trace_line: str | None = None  # the build's `trace <site>:` line (#20); None when it prints none


@dataclass(frozen=True)
class BuildResult:
    config: Config
    sites: tuple[SiteEntry, ...]
    warnings: tuple[SiteWarning, ...]
    inputs: dict[str, Inputs]  # every read site; Inputs.data is its SiteData
    pages: dict[str, str]
    summaries: tuple[SiteSummary, ...]
    stamp: str
    roots: Roots  # the run's roots (#59): every read, the stamp and every write
    # {waiting site: its foreign entries' outcomes}, every read site in config order (#68)
    foreign: dict[str, tuple[ForeignOutcome, ...]] = field(default_factory=dict)


def site_list(config, roots=None):
    """(sites, warnings): the site list's warnings, then the trace's. ConfigError propagates."""
    sites, warnings = load_sites(config, roots)
    return sites, warnings + in_trace_sites(config, sites)[1]


def clashes(sites) -> dict[str, str]:
    """{name: earlier} for each site whose name differs only in case from an earlier site's."""
    first, found = {}, {}
    for site in sites:
        folded = site.name.lower()
        if folded in first:
            found[site.name] = first[folded]
        else:
            first[folded] = site.name
    return found


def render_reason(e: Exception, roots) -> str:
    """A render error's one-line reason, naming no root by its path."""
    message = error_reason(e) if isinstance(e, OSError) else str(e)
    # The labels go in before ascii(), so a Windows path still matches.
    message = ascii(public_text(message, roots))[1:-1]
    if not message:
        return RENDER_ERROR_BARE.format(type=type(e).__name__)
    return RENDER_ERROR.format(type=type(e).__name__, message=message)


def site_data(roots, site):
    """(data, None) for a site a page can be built from, else (None, why not)."""
    if site.problem is not None:
        return None, site.problem
    data = read_wbs(roots, site.planning_dir / "WBS.md", site.name)
    unreadable = [e for e in data.report if e.kind == "site_unreadable"]
    if unreadable:
        return None, unreadable[0].message
    return data, None


def read_site(site, config, earlier, *, roots):
    """(inputs, None) for a site that can be rendered, else (None, reason).

    Its data is read, given its inputs, then checked for bad `h:` values (#36).
    """
    if earlier is not None:
        return None, CLASH.format(earlier=earlier)
    # Parsing too is per site: a parser that throws on one WBS.md stops only that site.
    try:
        data, problem = site_data(roots, site)
        if problem is not None:
            return None, public_text(problem, roots)
        inputs = apply_inputs(roots, site, data, config)
        return replace(inputs, data=check_h(inputs.data, site.name)), None
    except Exception as e:
        return None, render_reason(e, roots)


def render_site(name, inputs, config, stamp, *, catalog):
    """(html, page model) of one read site, its trace built from the run's
    `catalog` (#20); it may raise, and the caller's `try` makes a fault in
    the trace this site's render error."""
    trace = page_trace(build_site_trace(name, inputs.data.leaves, catalog), catalog, inputs.data)
    m = worksite.page_model(name, inputs.data, default_unit_hours=config.default_unit_hours, inputs=inputs,
                            trace=trace)
    return worksite.render_model(m, stamp=stamp), m


def read_sites(config, sites, *, roots):
    """({name: inputs}, {name: reason}) over every site, in config order."""
    earlier = clashes(sites)
    inputs, reasons = {}, {}
    for site in sites:
        found, reason = read_site(site, config, earlier.get(site.name), roots=roots)
        if found is not None:
            inputs[site.name] = found
        else:
            reasons[site.name] = reason
    return inputs, reasons


def render_sites(config, inputs, stamp, *, roots, catalog):
    """({name: html}, {name: page model}, {name: reason}) over the read sites, each in its own try."""
    pages, models, reasons = {}, {}, {}
    for name, site_inputs in inputs.items():
        try:
            pages[name], models[name] = render_site(name, site_inputs, config, stamp, catalog=catalog)
        except Exception as e:
            reasons[name] = render_reason(e, roots)
    return pages, models, reasons


def working_day(as_of: date, n: int) -> date:
    """The date of working day `n`, counting the as-of date as day 1 and
    Monday to Friday after it; day 0 is the as-of date too. No floor: a
    weekend as-of date is its own day 1."""
    day, left = as_of, max(n - 1, 0)
    while left:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def _summary(name, inputs, m, as_of):
    totals = inputs.totals
    day = m["finish_day"]
    return SiteSummary(
        name=name, status="built", reason=None,
        open_leaves=sum(leaf.open for leaf in inputs.data.leaves),
        finish_day=day, finish_date=working_day(as_of, day) if day is not None else None,
        unestimated_count=totals.unestimated_count, report_count=len(m["panel"]),
        hours=totals.hours, non_time=totals.non_time, trace_line=counts_line(name, m["trace"]),
    )


def not_built(name, reason) -> SiteSummary:
    return SiteSummary(name, "not_built", reason)


def rerender_failed(config, raw, sites, reasons, pages, models, failed, stamp, *, roots, catalog):
    """Re-resolve and re-render after render failures, until no new page fails (#68).

    `failed` is the last render's {site: reason}. Each pass adds the newly
    failed sites to `reasons`, re-resolves from the raw read `raw`, and
    re-renders only the built pages that hold an entry naming one of them.
    A page that fails here is dropped from `pages` and `models`. Changes
    `reasons`, `pages` and `models` in place; returns (resolved, foreign).
    """
    resolved, foreign = resolve_foreign(sites, raw, reasons)
    passes = 0
    new = {name: why for name, why in failed.items() if name not in reasons}
    while new:
        passes += 1
        assert passes <= len(sites), "the render-failure passes did not settle"
        reasons.update(new)
        for name in new:
            pages.pop(name, None)
            models.pop(name, None)
        resolved, foreign = resolve_foreign(sites, raw, reasons)
        waiting = {name: resolved[name] for name in pages
                   if any(o.target_site in new for o in foreign[name])}
        again, remodels, new = render_sites(config, waiting, stamp, roots=roots, catalog=catalog)
        pages.update(again)
        models.update(remodels)
    return resolved, foreign


def build_all(config, *, now=None, samples=None) -> BuildResult:
    """Read every site, resolve foreign entries across them, then render every
    read site; write nothing.

    After a render failure, the pages that wait on the failed site are
    re-resolved and re-rendered (rerender_failed). A write failure is not
    re-resolved: write_all runs after the pages are fixed.
    """
    roots = config_roots(config)
    sites, warnings = site_list(config, roots)
    # Every requirements file and the portfolio file, once per run (#20); a
    # file that cannot be read is a report entry, never an exception.
    catalog = load_catalog(roots, config, sites)
    stamp = stamp_text(config, now=now, samples=samples, roots=roots)
    raw, reasons = read_sites(config, sites, roots=roots)
    resolved, _ = resolve_foreign(sites, raw, reasons)
    pages, models, failed = render_sites(config, resolved, stamp, roots=roots, catalog=catalog)
    resolved, foreign = rerender_failed(config, raw, sites, reasons, pages, models, failed, stamp,
                                        roots=roots, catalog=catalog)
    summaries = tuple(_summary(s.name, resolved[s.name], models[s.name], config.as_of) if s.name in pages
                      else not_built(s.name, reasons[s.name]) for s in sites)
    return BuildResult(config, tuple(sites), tuple(warnings), resolved, pages, summaries, stamp, roots, foreign)


def _is_file_shaped(roots, folder) -> bool:
    try:
        return not stat.S_ISDIR(stat_confined(roots, folder, key="output_dir").st_mode)
    except (OSError, ValueError, OutsideRoots):
        return False


def _why(e: Exception, roots, folder=None, what=None) -> str:
    if isinstance(e, OutsideRoots):
        return str(e)  # it names output_dir
    # mkdir on a file says "File exists", which names neither the file nor the fix.
    if folder is not None and _is_file_shaped(roots, folder):
        return NOT_A_FOLDER.format(what=what)
    return error_reason(e) or "unknown error"


def write_all(result):
    """Write each page, then the index; delete nothing.

    Returns (result, written paths, index error). A page that cannot be
    written turns its site into a not-built one in the returned result,
    which the index is rendered from. When the output folder itself cannot
    be made, nothing is written and the index error says why; when only
    sites/ cannot be, each page fails on its own and the index is written.
    """
    roots = result.roots
    out = result.config.output_dir
    folder = out / "sites"
    try:
        mkdir_confined(roots, out, key="output_dir")
    except (OSError, OutsideRoots) as e:
        return result, [], _why(e, roots, out, "output_dir")
    written, pages, failed = [], {}, {}
    for name, text in result.pages.items():
        try:
            write_page(roots, folder, name, text)
        except (OSError, OutsideRoots) as e:
            failed[name] = WRITE_ERROR.format(name=name, why=_why(e, roots, folder, "sites/"))
            continue
        pages[name] = text
        written.append(folder / f"{name}.html")
    summaries = tuple(not_built(s.name, failed[s.name]) if s.name in failed else s for s in result.summaries)
    result = replace(result, pages=pages, summaries=summaries)
    try:
        write_page(roots, out, "index", render_index(result))
    except (OSError, OutsideRoots) as e:
        return result, written, _why(e, roots)
    return result, written + [out / "index.html"], None


def write_page(roots, folder, name, text):
    """Write <folder>/<name>.html under the output root, through a temporary
    file and a rename (write_confined): no partial page."""
    mkdir_confined(roots, folder, key="output_dir")
    write_confined(roots, folder / f"{name}.html", text.encode("utf-8"), key="output_dir")
