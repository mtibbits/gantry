"""Keep the changelog as one file per change, and compile it at release (#160).

    python3 tools/changelog.py check
    python3 tools/changelog.py preview
    python3 tools/changelog.py release <version> <date>
    python3 tools/changelog.py release <version> --fold

Each user-visible change adds its entry as changelog.d/<key>.<kind>.md, so
two open pull requests never edit the same CHANGELOG.md lines. <key> is the
issue number (positive, no leading zero) or, for a change with no issue, a
slug that starts with a letter and holds lowercase letters, digits and single
hyphens. <kind> is one of Keep a Changelog's six: added, changed, deprecated,
removed, fixed, security. The body is one or more Markdown list items, wrapped
lines indented two spaces, with blank lines between items allowed.

`check` reports every entry in changelog.d/ whose name or body breaks those
rules. Its universe is every directory entry of changelog.d/ on disk, not
git's list, because release deletes from disk and an uncommitted new entry
must count; so an untracked junk file there reds test_check_repository_root
in a local checkout, never in CI. A missing changelog.d/ counts as empty.
`check` does not require a pull request to add an entry: review enforces that.

Files are read as bytes, CRLF is read as LF, and CHANGELOG.md is written UTF-8
with LF. Exit 0 when done or clean; 1 with `changelog: <reason>` lines on
stderr for a problem or a refusal, which changes nothing; 2 for a usage error.
README's "Versioning & releases" says when to run each command.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROG = "changelog"
FOLDER = "changelog.d"

KINDS = ("added", "changed", "deprecated", "removed", "fixed", "security")  # Keep a Changelog order
HEADINGS = tuple(f"### {kind.capitalize()}" for kind in KINDS)
POINTER = 'New entries are files in changelog.d/; README\'s "Versioning & releases" says how.'

NUMBER_KEY = re.compile(r"[0-9]+")  # fullmatch: a key read as an issue number
ISSUE_NUMBER = re.compile(r"[1-9][0-9]*")  # fullmatch
SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")  # fullmatch
ITEM = re.compile(r"- \S")  # match: the first line of a list item
NAME_RULE = "it must be <key>.<kind>.md"


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _lf(text):
    return text.replace("\r\n", "\n")


def _shown(name):
    """An entry's name as messages spell it, whatever the platform."""
    return f"{FOLDER}/{name}"


def split_name(name):
    """(key, kind, None), or (None, None, the name problem)."""
    if not name.endswith(".md"):
        return None, None, f"the name does not end in .md; {NAME_RULE}"
    stem = name[:-3]
    if "." not in stem:
        return None, None, f"the name has no kind; {NAME_RULE}"
    key, kind = stem.rsplit(".", 1)
    if kind not in KINDS:
        return None, None, f"unknown kind {kind!r}; it must be one of {', '.join(KINDS)}"
    if NUMBER_KEY.fullmatch(key):
        if not ISSUE_NUMBER.fullmatch(key):
            return None, None, f"the issue number {key!r} must be positive with no leading zero"
    elif not SLUG.fullmatch(key):
        return None, None, (f"the slug {key!r} must start with a letter and hold only lowercase "
                            "letters, digits and single hyphens")
    return key, kind, None


def list_items(lines):
    """(items, None) or (None, index of the first line that is no part of an item).

    An item starts `- ` and a non-space; a continuation line starts with two
    spaces, is not blank, and directly follows an item or continuation line; a
    blank line ends an item. Each item is its list of lines."""
    items, current = [], None
    for i, line in enumerate(lines):
        if not line.strip():
            current = None
        elif ITEM.match(line):
            current = [line]
            items.append(current)
        elif line.startswith("  ") and current is not None:
            current.append(line)
        else:
            return None, i
    return items, None


def _entries(root):
    folder = Path(root) / FOLDER
    return sorted(folder.iterdir(), key=lambda p: p.name) if folder.is_dir() else []


def read_entry(path):
    """(key, kind, items, None), or (None, None, None, the entry's one problem)."""
    if not path.is_file():
        return None, None, None, "not a file; changelog.d/ holds only entry files"
    key, kind, problem = split_name(path.name)
    if problem:
        return None, None, None, problem
    try:
        text = _lf(path.read_bytes().decode("utf-8-sig"))
    except UnicodeDecodeError:
        return None, None, None, "not UTF-8"
    lines = text.split("\n")
    if not any(line.strip() for line in lines):
        return None, None, None, "empty; the body must be one or more list items"
    items, bad = list_items(lines)
    if items is None:
        return None, None, None, f"line {bad + 1} is not part of a list item"
    return key, kind, items, None


def check(root):
    """Every problem in changelog.d/, one per entry at most, by entry name."""
    problems = []
    for path in _entries(root):
        problem = read_entry(path)[3]
        if problem:
            problems.append(f"{_shown(path.name)}: {problem}")
    return problems


def build_parser():
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0],
                                     allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="report entry files that break the rules", allow_abbrev=False)
    return parser


def parse_args(argv):
    return build_parser().parse_args(argv)  # a usage error exits 2


def _say(lines, stream):
    for line in lines:
        print(f"{PROG}: {line}", file=stream)


def main(argv=None, root=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    args = parse_args(argv)
    root = Path(ROOT if root is None else root)
    problems = check(root)
    if problems:
        _say(problems, sys.stderr)
        return 1
    _say([f"check: no problems in {_plural(len(_entries(root)), 'entry file')}"], sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
