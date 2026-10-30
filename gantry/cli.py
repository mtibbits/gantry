"""The gantry command line: `gantry build --config FILE`.

There is no default config location: `--config` is required, and nothing here
reads the environment or the home folder to find one. Exit codes: 0 success,
1 a config or data error, 2 a usage error (argparse's own).
"""

import argparse
import sys

from gantry.config import ConfigError, load_config


def _parser():
    parser = argparse.ArgumentParser(
        prog="gantry",
        description="Build planning pages from devagent WBS files.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    build = commands.add_parser(
        "build",
        help="list the sites an installation config names",
        description="List the sites an installation config names.",
    )
    build.add_argument(
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


def _build(args):
    _utf8_streams()
    try:
        load_config(args.config)
    except ConfigError as e:
        print(f"gantry: error: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    args = _parser().parse_args(argv)
    return _build(args)
