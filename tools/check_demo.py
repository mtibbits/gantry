"""Check the demo output folder, or the Pages source setting (#45).

    python tools/check_demo.py <output folder>
    python tools/check_demo.py --pages-source <file>

The second form reads a saved `GET repos/<owner>/<repo>/pages` response and
exits 0 only when its build_type is "workflow" (Settings, Pages, Source:
GitHub Actions); it prints one line and never the rest of the response.
Exactly one form is given; anything else is a usage error, exit 2.
docs/demo.md states the rules.
"""

import argparse
import json
import sys
from pathlib import Path

PROG = "check_demo"
MUST = '; it must be "workflow" (Settings, Pages, Source: GitHub Actions)'


def pages_source_problem(data: bytes):
    """None when the response's build_type is "workflow", else the problem."""
    if not data.strip():
        return "the response is empty" + MUST
    try:
        value = json.loads(data.decode("utf-8"))
    except ValueError:
        return "the response is not JSON" + MUST
    if not isinstance(value, dict):
        return "the response is not a JSON object" + MUST
    if "build_type" not in value:
        return "the response has no build_type" + MUST
    if value["build_type"] != "workflow":
        return f"build_type is {json.dumps(value['build_type'])[:40]}" + MUST
    return None


def _pages_source(file) -> int:
    try:
        data = Path(file).read_bytes()
    except OSError:
        problem = "cannot read the file" + MUST
    else:
        problem = pages_source_problem(data)
    if problem is not None:
        print(f"{PROG}: pages source: {problem}", file=sys.stderr)
        return 1
    print(f'{PROG}: pages source: build_type is "workflow"')
    return 0


def _parser():
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", nargs="?", help="the demo output folder")
    parser.add_argument("--pages-source", metavar="FILE", help="a saved GET repos/<repo>/pages response")
    return parser


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if (args.folder is None) == (args.pages_source is None):
        parser.error("give exactly one of <output folder> or --pages-source <file>")
    if args.pages_source is not None:
        if not args.pages_source:
            parser.error("--pages-source needs a file")
        return _pages_source(args.pages_source)
    if not args.folder:
        parser.error("the output folder is empty")
    print(f"{PROG}: the output-folder check is not written yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(newline="\n")
    sys.exit(main())
