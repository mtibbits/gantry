"""Check and extract the README quickstart, and check what it built (#46).

    python3 tools/quickstart.py blocks <readme> <out>
    python3 tools/quickstart.py output <folder>

`blocks` reads the README's quickstart, between the lines
`<!-- quickstart:begin -->` and `<!-- quickstart:end -->`: exactly two
fenced blocks, the first exactly the "get the code" lines (GET_CODE), the
second the "build the demo" commands, which it writes to <out> as they are,
one per line, for `bash -e` to run. `output` checks the folder the demo
build printed last: it holds index.html and at least one sites/<name>.html.

A refusal prints one `quickstart: <reason>` line on stderr, naming only the
path as given, and exits 1; a usage error exits 2.
.github/workflows/quickstart.yml runs both on a fresh clone and
tests/test_docs.py imports them. Standard library only, importing no
sibling tool: it runs on the image's own python3 with nothing installed,
and the output folder comes as an argument.
"""

import argparse
import re
import sys
from pathlib import Path

BEGIN = "<!-- quickstart:begin -->"
END = "<!-- quickstart:end -->"
GET_CODE = ["git clone https://github.com/mtibbits/gantry.git", "cd gantry"]
FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")


class Refused(Exception):
    pass


def blocks(text):
    """(get-the-code lines, build-the-demo lines) from the README text."""
    lines = text.replace("\r\n", "\n").split("\n")
    stripped = [line.strip() for line in lines]
    if stripped.count(BEGIN) != 1 or stripped.count(END) != 1 or stripped.index(BEGIN) > stripped.index(END):
        raise Refused(f'the README needs one "{BEGIN}" line and one "{END}" line after it')
    found, fence, current = [], None, []
    for line in lines[stripped.index(BEGIN) + 1:stripped.index(END)]:
        if fence is None:
            m = FENCE.match(line)
            if m:
                fence, current = m.group(1), []
            continue
        if line and set(line) == {fence[0]} and len(line) >= len(fence):
            found.append(current)
            fence = None
        else:
            current.append(line)
    if fence is not None:
        raise Refused("a fenced block between the markers is not closed")
    if len(found) != 2:
        raise Refused(f"found {len(found)} fenced blocks between the markers, not 2")
    if found[0] != GET_CODE:
        raise Refused('the "get the code" block is not exactly: ' + " / ".join(GET_CODE))
    if not any(line.strip() for line in found[1]):
        raise Refused('the "build the demo" block is empty')
    return found[0], found[1]


def check_output(folder):
    """The number of sites/<name>.html in the folder, which must also hold index.html."""
    if not folder:
        raise Refused("no output folder was named")
    path = Path(folder)
    if not path.is_dir():
        raise Refused(f"{folder} is not a folder")
    if not (path / "index.html").is_file():
        raise Refused(f"{folder} has no index.html")
    sites = [p for p in (path / "sites").glob("*.html") if p.is_file()]
    if not sites:
        raise Refused(f"{folder} has no sites/<name>.html")
    return len(sites)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quickstart", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("blocks", help="write the build-the-demo block to <out>")
    b.add_argument("readme")
    b.add_argument("out")
    o = sub.add_parser("output", help="check the demo output folder")
    o.add_argument("folder")
    args = parser.parse_args(argv)
    try:
        if args.command == "blocks":
            _, build = blocks(Path(args.readme).read_bytes().decode("utf-8"))
            Path(args.out).write_bytes(("\n".join(build) + "\n").encode("utf-8"))
        else:
            n = check_output(args.folder)
            print(f"quickstart: ok: {args.folder} holds index.html and {n} site pages")
    except Refused as e:
        print(f"quickstart: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(newline="\n")
    sys.exit(main())
