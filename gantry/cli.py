"""The gantry command line: `gantry build --config FILE`.

There is no default config location: `--config` is required, and nothing here
reads the environment or the home folder to find one.
"""

import argparse


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


def main(argv=None):
    args = _parser().parse_args(argv)
    return 0
