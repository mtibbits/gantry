"""The gantry command line: `gantry build --config FILE [--site NAME]`,
`gantry portfolio --config FILE` and `gantry fires --config FILE`.

`gantry build` builds every configured site's page, the week view
(<output_dir>/week.html, #38), the strategic page
(<output_dir>/strategic.html, #23) and the index, plans the week and writes
the plan to <output_dir>/week-plan.json (#61); with
`--site` it builds that one page, no week view, no strategic page, no index and no plan, reading that site's
WBS.md and inputs, every requirements file and the portfolio file. Both
print each written file's path, then a `trace <site>:` line for each written
page whose site is in the trace or whose trace report has an entry (#20);
the full build then prints one `fires:` line, the count of fires and of
sources not fully read (#32, docs/daily-screen.md). `gantry portfolio` checks the
portfolio file the config names, without building (#22); `gantry fires`
checks the fires list, without building (#30). There is no default
config location: `--config` is required, and nothing here reads the
environment or the home folder to find one. Exit codes:

- 0: the full build wrote the week view, the strategic page, the index and
  at least one site page; `--site` wrote its page; `portfolio` found no
  report entry;
- 1: a config error; the full build wrote no site page (the index is still
  written) or cannot write the index or the output folder, the week page
  cannot be rendered or written, the plan file cannot be written, or
  strategic.html cannot be made or written; `--site` names no
  site, a case clash, a site that cannot be built, a render error or a page
  that cannot be written; `portfolio` found an entry (an advisory one
  included), or the config sets no portfolio_file; `fires` read the fires
  list with a report entry, could not read it, or the config sets no
  fires_list (`fires` exits 0 only for status `read`);
- 2: a usage error (argparse's own).
"""

import argparse
import sys
from datetime import datetime

from gantry.build import (
    FIRES_STEP,
    build_all,
    clashes,
    read_site,
    render_reason,
    render_site,
    site_list,
    write_all,
    write_page,
    why_not_written,
)
from gantry.config import ConfigError, error_reason, load_config
from gantry.fires_list import entry_age, fires_reference, read_fires_list, still_a_fire
from gantry.planner import PLAN_FILE, plan_to_json, write_plan
from gantry.portfolio_file import PORTFOLIO_LABEL, SECTIONS, format_entry, read_portfolio_file, report_entries
from gantry.roots import OutsideRoots
from gantry.sites import config_roots, shown
from gantry.stamp import stamp_text
from gantry.trace import counts_line, load_catalog
from gantry.views.strategic import STRATEGIC_FILE

# The full build's last stdout line (#32): how many fires, and how many sources
# were not fully read. It names no fire; docs/daily-screen.md#fires states it.
FIRES_LINE = "fires: {n}; sources not fully read: {m}"


def _parser():
    parser = argparse.ArgumentParser(
        prog="gantry",
        description="Build planning pages from devagent WBS files.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    build = commands.add_parser(
        "build",
        help="build every site's worksite page, the week view, the strategic page and the index, or one site's page",
        description="Build every site an installation config names: each site's page to "
        "<output_dir>/sites/<name>.html, then the week view to <output_dir>/week.html, then "
        "<output_dir>/strategic.html, then <output_dir>/index.html, then the week plan to "
        "<output_dir>/week-plan.json. A site that cannot be built is listed on the index and the "
        "strategic page with its reason. With --site, build only that site's page: no week view, no "
        "strategic page, no index and no plan.",
    )
    _add_config(build)
    build.add_argument(
        "--site",
        metavar="NAME",
        help="build only that site's worksite page, and no week view, no strategic page, no index and no plan",
    )
    portfolio = commands.add_parser(
        "portfolio",
        help="check the portfolio file the config names, without building",
        description="Read the portfolio file an installation config names and print one line per "
        "report entry, grouped by section, then a count. Exit 0 with no entries, 1 otherwise.",
    )
    fires = commands.add_parser(
        "fires",
        help="check the fires list the config names, without building",
        description="Read the fires list an installation config names and print one line per "
        "fire, the counts, one line per report entry and the status. Exit 0 when the status "
        "is read, 1 otherwise.",
    )
    _add_config(portfolio)
    _add_config(fires)
    return parser


def _add_config(command):
    command.add_argument(
        "--config",
        required=True,
        metavar="FILE",
        help="the installation config (a TOML file); there is no default",
    )


def _utf8_streams():
    # The same bytes on every host: UTF-8, LF. A caller's StringIO has no
    # reconfigure and is left alone.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")


def _build_site(config, roots, sites, name):
    def fail(message):
        print(f"gantry: error: --site {shown(name)}: {message}", file=sys.stderr)
        return 1

    site = next((s for s in sites if s.name == name), None)
    if site is None:
        return fail(f"no such site in {config.devagent_config}")
    # The full build's read: its case-clash rule, and a parse error stops only this site.
    inputs, problem = read_site(site, config, clashes(sites).get(site.name), roots=roots)
    if problem is not None:
        return fail(f"cannot build: {problem}")
    # The trace reads every requirements file and the portfolio file (#20).
    catalog = load_catalog(roots, config, sites)
    try:
        text, m = render_site(site.name, inputs, config, stamp_text(config, roots=roots), catalog=catalog)
    except Exception as e:
        # One line, naming no configured root by its path.
        return fail(render_reason(e, roots))
    folder = config.output_dir / "sites"
    page = folder / f"{site.name}.html"
    try:
        write_page(roots, folder, site.name, text)
    except (OSError, OutsideRoots) as e:
        # A refusal's own text names output_dir.
        return fail(f"cannot write {page}: {error_reason(e)}")
    print(page)
    line = counts_line(site.name, m["trace"])
    if line is not None:
        print(line)
    return 0


def _warn(warnings):
    # The site list's and the trace's warnings; the trace reads the files it names (#20).
    for warning in warnings:
        print(f"gantry: warning: {warning.message}", file=sys.stderr)


def _write_plan(result, text):
    """Write the plan file: (its path, or None when not written; why it
    could not be written, or None).

    Not attempted when write_all wrote nothing (the output folder could not
    be made: that one error stands). An index that failed on its own still
    gets the plan written, so an older plan never stands in for this one.
    """
    if result.wrote_nothing:
        return None, None
    path = result.config.output_dir / PLAN_FILE
    try:
        write_plan(result.plan, result.config.output_dir, roots=result.roots, text=text)
    except (OSError, OutsideRoots) as e:
        return None, why_not_written(e, result.roots)
    return path, None


def _build_all(config):
    try:
        result = build_all(config)
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return 1
    _warn(result.warnings)
    # The plan's text is made here, before any view is rendered from the result.
    plan_text = plan_to_json(result.plan)
    result, written, index_error = write_all(result)
    index = config.output_dir / "index.html"
    plan_path, plan_error = _write_plan(result, plan_text)
    for path in written:
        print(path)
    if plan_path is not None:
        print(plan_path)
    # Then each written page's trace line, in config order (#20).
    for s in result.summaries:
        if s.trace_line is not None and config.output_dir / "sites" / f"{s.name}.html" in written:
            print(s.trace_line)
    # Last, the fires line (#32): counts only, whether or not anything was written.
    print(FIRES_LINE.format(n=len(result.fires.fires), m=result.fires.not_fully_read))
    # A source that could not be read at all is named here: the line's counts alone cannot tell it from a
    # healthy build with no fires list (#32).
    for s in result.fires.sources:
        if s.site is None and s.status == "not_read":
            what = "fires not collected" if s.source == FIRES_STEP else f"fires source {s.source} is not read"
            print(f"gantry: warning: {what}: {s.reason}", file=sys.stderr)
    for s in result.summaries:
        if s.status != "built":
            print(f"gantry: warning: site {shown(s.name)} is not built: {s.reason}", file=sys.stderr)
    if result.week_error is not None:
        print(f"gantry: error: {result.week_error}", file=sys.stderr)
    if plan_error is not None:
        print(f"gantry: error: cannot write {PLAN_FILE}: {plan_error}", file=sys.stderr)
    if result.strategic_error is not None:
        # One form for a model, render or write failure; the index then has no headline, shares or link (#23).
        print(f"gantry: error: cannot write {STRATEGIC_FILE}: {result.strategic_error}", file=sys.stderr)
    if index_error is not None:
        # The index, or the output folder itself, cannot be written.
        print(f"gantry: error: cannot write {index}: {index_error}", file=sys.stderr)
        return 1
    if not result.pages:
        print("gantry: error: no site page was written", file=sys.stderr)
        return 1
    return 0 if plan_error is None and result.week_error is None and result.strategic_error is None else 1


def _build(args):
    _utf8_streams()
    try:
        config = load_config(args.config)
        if args.site is None:
            return _build_all(config)
        roots = config_roots(config)
        sites, warnings = site_list(config, roots)
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return 1
    _warn(warnings)
    return _build_site(config, roots, sites, args.site)


# This command reads no WBS.md, so it cannot find the milestone leaf; the full
# build reads every site and runs report_entries with each site's issue:
# values, and the strategic page lists the result (#23, docs/portfolio-file.md).
MILESTONE_NOTE = ("note: the milestone leaf is not checked here (this command reads no WBS.md); a full gantry build "
                  "checks it and lists the result on the strategic page")


def _load_for_check(args):
    """(config, roots, site names) for a command that checks one file; None
    after printing the config error."""
    _utf8_streams()
    try:
        config = load_config(args.config)
        roots = config_roots(config, purpose="check")  # it writes nothing
        sites, _ = site_list(config, roots)  # the site list's own warnings belong to the build
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return None
    return config, roots, [s.name for s in sites]


def _portfolio(args):
    loaded = _load_for_check(args)
    if loaded is None:
        return 1
    config, roots, site_names = loaded
    if config.portfolio_file is None:
        print(f"gantry: error: {config.path}: portfolio_file is not set; add portfolio_file = \"<path>\" "
              "to check a portfolio file (docs/portfolio-file.md)", file=sys.stderr)
        return 1
    portfolio = read_portfolio_file(roots, config.portfolio_file, br_namespace=config.requirements.br_namespace)
    # It reads no WBS.md, so no issues_by_site: the milestone leaf is not checked.
    entries = report_entries(portfolio, site_names)
    for section in (None, *SECTIONS):
        group = [e for e in entries if e.section == section]
        if group:
            print(section if section is not None else PORTFOLIO_LABEL)
            for e in group:
                print("  " + format_entry(e))
    if portfolio.milestone is not None:
        print(MILESTONE_NOTE)
    print(f"{len(entries)} {'entry' if len(entries) == 1 else 'entries'}")
    return 0 if not entries else 1


def _fire_line(entry, reference, check_days):
    """`line N: SITE, WHEN, AGE[, still a fire?][, unreadable]`; the reason is never printed."""
    if entry.site is None:
        site = "no site"
    else:
        site = "@" + shown(entry.site) + ("" if entry.site_known else " (not a site)")
    age = entry_age(entry, reference)
    if entry.date is None:
        when, shown_age = "no date", "age unknown"
    elif entry.time is None:
        when, shown_age = entry.date.isoformat(), f"{age.days}d"
    else:
        when = f"{entry.date.isoformat()} {entry.time.strftime('%H:%M')}"
        shown_age = f"{age.days}d {age.seconds // 3600}h"
    parts = [f"line {entry.line}: {site}", when, shown_age]
    if still_a_fire(entry, reference, check_days):
        parts.append("still a fire?")
    if entry.state == "unreadable":
        parts.append("unreadable")
    return ", ".join(parts)


def _fires(args):
    loaded = _load_for_check(args)
    if loaded is None:
        return 1
    config, roots, site_names = loaded
    # fires_reference is the rule the full build uses too (#32): as_of at
    # 00:00 when the config sets it, else now.
    reference = fires_reference(config, datetime.now())
    result = read_fires_list(roots, config.fires_list, site_names, reference)
    if result.status == "not_configured":
        print(f"gantry: error: {config.path}: fires_list is not set; add fires_list = \"<path>\" "
              "to check a fires list (docs/fires-list.md)", file=sys.stderr)
    if result.status in ("read", "read_with_problems"):
        fires = [e for e in result.entries if not e.ended]
        for entry in fires:
            print(_fire_line(entry, reference, config.fires_check_days))
        print(f"fires: {len(fires)}, ended: {len(result.entries) - len(fires)}")
        print(f"skipped_empty: {result.skipped_empty}, skipped_in_fences: {result.skipped_in_fences}")
    for e in result.report:
        print(("" if e.line is None else f"line {e.line}: ") + f"{e.kind}: {e.message}")
    print(f"status: {result.status}")
    return 0 if result.status == "read" else 1


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "portfolio":
        return _portfolio(args)
    if args.command == "fires":
        return _fires(args)
    return _build(args)
