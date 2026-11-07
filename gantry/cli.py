"""The gantry command line: `gantry build --config FILE [--site NAME]`.

`gantry build` builds every configured site's page and the index; with
`--site` it builds that one page and no index. There is no default config
location: `--config` is required, and nothing here reads the environment or
the home folder to find one. Exit codes:

- 0: the full build wrote the index and at least one site page; `--site`
  wrote its page;
- 1: a config error; the full build wrote no site page (the index is still
  written) or cannot write the index or the output folder; `--site` names no
  site, a case clash, a site that cannot be built, a render error or a page
  that cannot be written;
- 2: a usage error (argparse's own).
"""

import argparse
import sys

from gantry.build import (
    CLASH,
    build_all,
    clashes,
    render_reason,
    render_site,
    site_data,
    site_list,
    write_all,
    write_page,
)
from gantry.config import ConfigError, error_reason, load_config
from gantry.inputs import apply_inputs
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
    # The full build's one rule: a later name that differs only in case is not built.
    earlier = clashes(sites).get(site.name)
    if earlier is not None:
        return fail(f"cannot build: {CLASH.format(earlier=earlier)}")
    data, problem = site_data(site)
    if problem is not None:
        return fail(f"cannot build: {problem}")
    try:
        inputs = apply_inputs(site, data, config)
        text, _ = render_site(site.name, inputs, config, stamp_text(config, sites))
    except Exception as e:
        # One line, naming no configured root by its path.
        return fail(render_reason(e, config_roots(config, sites)))
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
    if index_error is not None and not written:
        # The output folder itself cannot be written: one error line, nothing written.
        print(f"gantry: error: cannot write {index}: {index_error}", file=sys.stderr)
        return 1
    code = 0
    if index_error is not None:
        print(f"gantry: error: cannot write {index}: {index_error}", file=sys.stderr)
        code = 1
    if not result.pages:
        print("gantry: error: no site page was written", file=sys.stderr)
        code = 1
    return code


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


def main(argv=None):
    args = _parser().parse_args(argv)
    return _build(args)
