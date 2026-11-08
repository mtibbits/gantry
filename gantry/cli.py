"""The gantry command line: `gantry build --config FILE [--site NAME]` and
`gantry portfolio --config FILE`.

`gantry build` builds every configured site's page and the index; with
`--site` it builds that one page and no index. `gantry portfolio` checks the
portfolio file the config names, without building (#22). There is no default
config location: `--config` is required, and nothing here reads the
environment or the home folder to find one. Exit codes:

- 0: the full build wrote the index and at least one site page; `--site`
  wrote its page; `portfolio` found no report entry;
- 1: a config error; the full build wrote no site page (the index is still
  written) or cannot write the index or the output folder; `--site` names no
  site, a case clash, a site that cannot be built, a render error or a page
  that cannot be written; `portfolio` found an entry (an advisory one
  included), or the config sets no portfolio_file;
- 2: a usage error (argparse's own).
"""

import argparse
import sys

from gantry.build import (
    build_all,
    clashes,
    read_site,
    render_reason,
    render_site,
    site_list,
    write_all,
    write_page,
)
from gantry.config import ConfigError, error_reason, load_config
from gantry.portfolio_file import PORTFOLIO_LABEL, SECTIONS, format_entry, read_portfolio_file, report_entries
from gantry.sites import shown
from gantry.stamp import config_roots, stamp_text


def _parser():
    parser = argparse.ArgumentParser(
        prog="gantry",
        description="Build planning pages from devagent WBS files.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    build = commands.add_parser(
        "build",
        help="build every site's worksite page and the index, or one site's page",
        description="Build every site an installation config names: each site's page to "
        "<output_dir>/sites/<name>.html, then <output_dir>/index.html. A site that cannot be "
        "built is listed on the index with its reason. With --site, build only that site's "
        "page and no index.",
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
        help="build only that site's worksite page, and no index",
    )
    portfolio = commands.add_parser(
        "portfolio",
        help="check the portfolio file the config names, without building",
        description="Read the portfolio file an installation config names and print one line per "
        "report entry, grouped by section, then a count. Exit 0 with no entries, 1 otherwise.",
    )
    portfolio.add_argument(
        "--config",
        required=True,
        metavar="FILE",
        help="the installation config (a TOML file); there is no default",
    )
    return parser


def _utf8_streams():
    # The same bytes on every host: UTF-8, LF. A caller's StringIO has no
    # reconfigure and is left alone.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")


def _build_site(config, sites, name):
    def fail(message):
        print(f"gantry: error: --site {shown(name)}: {message}", file=sys.stderr)
        return 1

    site = next((s for s in sites if s.name == name), None)
    if site is None:
        return fail(f"no such site in {config.devagent_config}")
    roots = config_roots(config, sites)
    # The full build's read: its case-clash rule, and a parse error stops only this site.
    inputs, problem = read_site(site, config, clashes(sites).get(site.name), roots=roots)
    if problem is not None:
        return fail(f"cannot build: {problem}")
    try:
        text, _ = render_site(site.name, inputs, config, stamp_text(config, sites))
    except Exception as e:
        # One line, naming no configured root by its path.
        return fail(render_reason(e, roots))
    folder = config.output_dir / "sites"
    page = folder / f"{site.name}.html"
    try:
        write_page(folder, site.name, text)
    except OSError as e:
        return fail(f"cannot write {page}: {error_reason(e)}")
    print(page)
    return 0


def _warn(warnings):
    # In this issue the build only warns; reading the files is the trace's (#19).
    for warning in warnings:
        print(f"gantry: warning: {warning.message}", file=sys.stderr)


def _build_all(config):
    try:
        result = build_all(config)
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return 1
    _warn(result.warnings)
    result, written, index_error = write_all(result)
    index = config.output_dir / "index.html"
    for path in written:
        print(path)
    for s in result.summaries:
        if s.status != "built":
            print(f"gantry: warning: site {shown(s.name)} is not built: {s.reason}", file=sys.stderr)
    if index_error is not None:
        # The index, or the output folder itself, cannot be written.
        print(f"gantry: error: cannot write {index}: {index_error}", file=sys.stderr)
        return 1
    if not result.pages:
        print("gantry: error: no site page was written", file=sys.stderr)
        return 1
    return 0


def _build(args):
    _utf8_streams()
    try:
        config = load_config(args.config)
        if args.site is None:
            return _build_all(config)
        sites, warnings = site_list(config)
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return 1
    _warn(warnings)
    return _build_site(config, sites, args.site)


# True until the build reads the portfolio file (#23 or #61); that issue
# rewords it, with docs/portfolio-file.md.
MILESTONE_NOTE = ("note: the milestone leaf is not checked here (this command reads no WBS.md); "
                  "it is checked once the build reads the portfolio file")


def _portfolio(args):
    _utf8_streams()
    try:
        config = load_config(args.config)
        sites, _ = site_list(config)  # the site list's own warnings belong to the build
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return 1
    if config.portfolio_file is None:
        print(f"gantry: error: {config.path}: portfolio_file is not set; add portfolio_file = \"<path>\" "
              "to check a portfolio file (docs/portfolio-file.md)", file=sys.stderr)
        return 1
    portfolio = read_portfolio_file(config.portfolio_file, br_namespace=config.requirements.br_namespace)
    # It reads no WBS.md, so no issues_by_site: the milestone leaf is not checked.
    entries = report_entries(portfolio, [s.name for s in sites])
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


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "portfolio":
        return _portfolio(args)
    return _build(args)
