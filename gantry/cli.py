"""The gantry command line: `gantry build --config FILE [--site NAME]`.

There is no default config location: `--config` is required, and nothing here
reads the environment or the home folder to find one. Exit codes: 0 success,
1 a config, data, render or write error, 2 a usage error (argparse's own).
"""

import argparse
import sys

from gantry.build import site_data, write_page
from gantry.config import ConfigError, error_reason, load_config
from gantry.model import REPORT_KINDS, SiteData
from gantry.requirements import in_trace_sites
from gantry.sites import load_sites, shown
from gantry.wbs_stopgap import read_wbs


def _parser():
    parser = argparse.ArgumentParser(
        prog="gantry",
        description="Build planning pages from devagent WBS files.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    build = commands.add_parser(
        "build",
        help="list the sites an installation config names, or write one site's worksite page",
        description="List the sites an installation config names, with each WBS.md's "
        "leaf and report counts; with --site, write that site's worksite page to "
        "<output_dir>/sites/<name>.html instead.",
    )
    build.add_argument(
        "--config",
        required=True,
        metavar="FILE",
        help="the installation config (a TOML file); there is no default",
    )
    build.add_argument(
        "--site",
        metavar="NAME",
        help="write that site's worksite page instead of listing",
    )
    return parser


def _utf8_streams():
    # The same bytes on every host: UTF-8, LF. A caller's StringIO has no
    # reconfigure and is left alone.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")


def status_text(data: SiteData) -> str:
    """A read site's third field: leaf counts, then each reported kind's count."""
    unreadable = [e for e in data.report if e.kind == "site_unreadable"]
    if unreadable:
        return f"cannot read: {unreadable[0].message}"
    n_open = sum(leaf.open for leaf in data.leaves)
    text = f"open {n_open}, finished {len(data.leaves) - n_open}"
    counts = {kind: 0 for kind in REPORT_KINDS}
    for entry in data.report:
        counts[entry.kind] += 1
    found = [f"{kind} {count}" for kind, count in counts.items() if count]
    return f"{text}; {', '.join(found)}" if found else text


def _build_site(config, sites, name):
    def fail(message):
        print(f"gantry: error: --site {shown(name)}: {message}", file=sys.stderr)
        return 1

    site = next((s for s in sites if s.name == name), None)
    if site is None:
        return fail(f"no such site in {config.devagent_config}")
    data, problem = site_data(site)
    if problem is not None:
        return fail(f"cannot build: {problem}")
    # The view and the inputs are imported only here: listing sites never loads them.
    from gantry.inputs import apply_inputs
    from gantry.views import worksite

    try:
        inputs = apply_inputs(site, data, config)
        text = worksite.render_page(site.name, inputs.data, default_unit_hours=config.default_unit_hours,
                                    inputs=inputs)
    except Exception as e:
        # The text may quote a record value: escape it so the message stays one line.
        return fail(f"render error: {type(e).__name__}: {ascii(str(e))[1:-1]}")
    folder = config.output_dir / "sites"
    page = folder / f"{site.name}.html"
    try:
        write_page(folder, site.name, text)
    except OSError as e:
        return fail(f"cannot write {page}: {error_reason(e)}")
    print(page)
    return 0


def _build(args):
    _utf8_streams()
    try:
        config = load_config(args.config)
        sites, warnings = load_sites(config)
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return 1
    # In this issue the build only warns; reading the files is the trace's (#19).
    warnings += in_trace_sites(config, sites)[1]
    for warning in warnings:
        print(f"gantry: warning: {warning.message}", file=sys.stderr)
    if args.site is not None:
        return _build_site(config, sites, args.site)
    # One tab-separated line per site; the listing writes nothing to disk.
    for site in sites:
        name = shown(site.name)
        where = str(site.planning_dir) if site.planning_dir is not None else "-"
        if site.problem is None:
            status = status_text(read_wbs(site.planning_dir / "WBS.md", site.name))
        else:
            status = f"cannot read: {site.problem}"
        print(f"{name}\t{where}\t{status}")
    return 0


def main(argv=None):
    args = _parser().parse_args(argv)
    return _build(args)
