"""The full build: read every site, then render every read site (#11).

build_all reads each site in devagent's config order, then renders each site
it read, each in its own `try`, and returns one BuildResult that every view
reads. It writes nothing; write_all writes the pages and the index. A site
that cannot be built gets a reason that names its config key or file kind,
never an absolute path. docs/worksite-page.md states the build.
"""

import os
import tempfile
from dataclasses import dataclass, replace
from datetime import date, timedelta

from gantry.config import Config, error_reason
from gantry.inputs import Inputs, apply_inputs
from gantry.requirements import in_trace_sites
from gantry.sites import SiteEntry, SiteWarning, load_sites
from gantry.stamp import config_roots, public_text, stamp_text
from gantry.views import worksite
from gantry.views.index import render_index
from gantry.wbs_stopgap import read_wbs

CLASH = "name clashes with {earlier} on a case-insensitive file system"
RENDER_ERROR = "render error: {type}: {message}"
RENDER_ERROR_BARE = "render error: {type}"
WRITE_ERROR = "cannot write sites/{name}.html: {why}"


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


@dataclass(frozen=True)
class BuildResult:
    config: Config
    sites: tuple[SiteEntry, ...]
    warnings: tuple[SiteWarning, ...]
    inputs: dict[str, Inputs]  # every read site; Inputs.data is its SiteData
    pages: dict[str, str]
    summaries: tuple[SiteSummary, ...]
    stamp: str


def site_list(config):
    """(sites, warnings): the site list's warnings, then the trace's. ConfigError propagates."""
    sites, warnings = load_sites(config)
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


def site_data(site):
    """(data, None) for a site a page can be built from, else (None, why not)."""
    if site.problem is not None:
        return None, site.problem
    data = read_wbs(site.planning_dir / "WBS.md", site.name)
    unreadable = [e for e in data.report if e.kind == "site_unreadable"]
    if unreadable:
        return None, unreadable[0].message
    return data, None


def read_site(site, config, earlier, *, roots):
    """(inputs, None) for a site that can be rendered, else (None, reason)."""
    if earlier is not None:
        return None, CLASH.format(earlier=earlier)
    # Parsing too is per site: a parser that throws on one WBS.md stops only that site.
    try:
        data, problem = site_data(site)
        if problem is not None:
            return None, public_text(problem, roots)
        return apply_inputs(site, data, config), None
    except Exception as e:
        return None, render_reason(e, roots)


def render_site(name, inputs, config, stamp):
    """(html, page model) of one read site; it may raise."""
    m = worksite.page_model(name, inputs.data, default_unit_hours=config.default_unit_hours, inputs=inputs)
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


def render_sites(config, inputs, stamp, *, roots):
    """({name: html}, {name: page model}, {name: reason}) over the read sites, each in its own try."""
    pages, models, reasons = {}, {}, {}
    for name, site_inputs in inputs.items():
        try:
            pages[name], models[name] = render_site(name, site_inputs, config, stamp)
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
        hours=totals.hours, non_time=totals.non_time,
    )


def not_built(name, reason) -> SiteSummary:
    return SiteSummary(name, "not_built", reason)


def build_all(config, *, now=None, samples=None) -> BuildResult:
    """Read every site, then render every read site; write nothing.

    The gap between the two phases is #68's seam: it resolves foreign
    entries across the read sites there, then calls render_sites again on
    the sites whose pages change.
    """
    sites, warnings = site_list(config)
    stamp = stamp_text(config, sites, now=now, samples=samples)
    roots = config_roots(config, sites)
    inputs, reasons = read_sites(config, sites, roots=roots)
    pages, models, failed = render_sites(config, inputs, stamp, roots=roots)
    reasons.update(failed)
    summaries = tuple(_summary(s.name, inputs[s.name], models[s.name], config.as_of) if s.name in pages
                      else not_built(s.name, reasons[s.name]) for s in sites)
    return BuildResult(config, tuple(sites), tuple(warnings), inputs, pages, summaries, stamp)


def _why(e: OSError) -> str:
    return error_reason(e) or "unknown error"


def write_all(result):
    """Write each page, then the index; delete nothing.

    Returns (result, written paths, index error). A page that cannot be
    written turns its site into a not-built one in the returned result,
    which the index is rendered from. When the output folder itself cannot
    be made, nothing is written and the index error says why.
    """
    out = result.config.output_dir
    folder = out / "sites"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return result, [], _why(e)
    written, pages, failed = [], {}, {}
    for name, text in result.pages.items():
        try:
            write_page(folder, name, text)
        except OSError as e:
            failed[name] = WRITE_ERROR.format(name=name, why=_why(e))
            continue
        pages[name] = text
        written.append(folder / f"{name}.html")
    summaries = tuple(not_built(s.name, failed[s.name]) if s.name in failed else s for s in result.summaries)
    result = replace(result, pages=pages, summaries=summaries)
    try:
        write_page(out, "index", render_index(result))
    except OSError as e:
        return result, written, _why(e)
    return result, written + [out / "index.html"], None


def write_page(folder, name, text):
    """Write <folder>/<name>.html through a temporary file and a rename: no partial page."""
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        # mkstemp makes the file 0600; a page gets the mode any new file
        # gets, so a server on the private network can read it.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, folder / f"{name}.html")
    except BaseException:
        # fdopen closed the descriptor, on success or failure.
        os.unlink(tmp)
        raise
