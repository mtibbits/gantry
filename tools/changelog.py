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
HEADING = re.compile(r"#{1,6}(?:[ \t]|$)")  # match: an ATX heading
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
NAME_RULE = "it must be <key>.<kind>.md"
CHANGELOG = "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"


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


def line_role(line, in_item):
    """'blank', 'item' or 'more' (a continuation line), or None: no part of a list.

    An item starts `- ` and a non-space; a continuation line starts with two
    spaces, is not blank, and directly follows an item or continuation line; a
    blank line ends an item. Entry files and ## [Unreleased] share this rule."""
    if not line.strip():
        return "blank"
    if ITEM.match(line):
        return "item"
    if line.startswith("  ") and in_item:
        return "more"
    return None


def list_items(lines):
    """(items, None) or (None, index of the first line that is no part of an item).

    Each item is its list of lines."""
    items, current = [], None
    for i, line in enumerate(lines):
        role = line_role(line, current is not None)
        if role == "blank":
            current = None
        elif role == "item":
            current = [line]
            items.append(current)
        elif role == "more":
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


# --- CHANGELOG.md -----------------------------------------------------------------

class Refused(Exception):
    """A reason to exit 1, changing nothing: one or more message lines."""

    def __init__(self, *lines, check_failed=False):
        super().__init__(*lines)
        self.lines = list(lines)
        self.check_failed = check_failed  # the caller adds its own `<command> refused` line


def read_changelog(root):
    """CHANGELOG.md's lines, read as UTF-8 with CRLF as LF, or Refused (R0)."""
    path = Path(root) / CHANGELOG
    if not path.is_file():
        raise Refused(f"{CHANGELOG}: missing")
    try:
        return _lf(path.read_bytes().decode("utf-8")).split("\n")
    except UnicodeDecodeError:
        raise Refused(f"{CHANGELOG}: not UTF-8") from None


def unfenced(lines):
    """The lines with each line of a ``` or ~~~ fence replaced by None."""
    out, fence = [], None
    for line in lines:
        if fence is None:
            m = FENCE.match(line)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
                out.append(None)
            else:
                out.append(line)
            continue
        out.append(None)
        close = line.strip()
        if (close and set(close) == {fence[0]} and len(close) >= len(fence)
                and len(line) - len(line.lstrip(" ")) <= 3):
            fence = None
    return out


def unreleased_span(lines):
    """(index of the ## [Unreleased] line, index of the next ## line or len(lines)), or Refused (R7)."""
    plain = unfenced(lines)
    if UNRELEASED not in plain:
        raise Refused(f"{CHANGELOG} has no '{UNRELEASED}' line")
    start = plain.index(UNRELEASED)
    end = next((i for i in range(start + 1, len(plain))
                if plain[i] is not None and plain[i].startswith("## ")), len(lines))
    return start, end


def _no_items():
    return {kind: [] for kind in KINDS}


def carried_items(lines, start, end):
    """The items under ## [Unreleased] per kind, in document order, or Refused (R6).

    Blank lines, kind headings (repeated or not) and, before the first kind
    heading, the pointer line are placed; anything else is refused."""
    items, kind, current = _no_items(), None, None
    for i in range(start + 1, end):
        line, where = lines[i], f"{CHANGELOG} line {i + 1}: {UNRELEASED} holds"
        role = line_role(line, current is not None)
        if role == "blank":
            current = None
        elif line in HEADINGS:
            kind, current = KINDS[HEADINGS.index(line)], None
        elif HEADING.match(line):
            raise Refused(f"{where} a heading other than the six kinds: {line!r}")
        elif role == "item":
            if kind is None:
                raise Refused(f"{where} an item under no kind heading")
            current = [line]
            items[kind].append(current)
        elif role == "more":
            current.append(line)
        elif line == POINTER and kind is None:
            current = None
        else:
            raise Refused(f"{where} prose other than the pointer line; "
                          "move it into an entry file or delete it")
    return items


def _key_order(path):
    key = path.name[:-3].rsplit(".", 1)[0]
    return (0, len(key), key) if NUMBER_KEY.fullmatch(key) else (1, 0, key)


def waiting(root):
    """(items per kind, the entry files used): carried items first, in document
    order, then each entry file's items, issue numbers before slugs; or Refused
    (R0, R7, R5, R6)."""
    lines = read_changelog(root)
    start, end = unreleased_span(lines)
    problems = check(root)
    if problems:
        raise Refused(*problems, check_failed=True)
    items = carried_items(lines, start, end)
    files = sorted(_entries(root), key=_key_order)
    for path in files:
        _, kind, entry_items, _ = read_entry(path)
        items[kind].extend(entry_items)
    return items, files


def render(heading, items):
    """`heading`, then each kind that has items, in KINDS order: a blank line,
    its heading, a blank line and its item lines, none between items."""
    out = [heading]
    for kind, kind_heading in zip(KINDS, HEADINGS):
        if items.get(kind):
            out += ["", kind_heading, ""]
            for item in items[kind]:
                out += item
    return "\n".join(out) + "\n"


def preview(root):
    """The ## [Unreleased] section release would write, or Refused as release is."""
    return render(UNRELEASED, waiting(root)[0])


def build_parser():
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0],
                                     allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="report entry files that break the rules", allow_abbrev=False)
    sub.add_parser("preview", help="print the ## [Unreleased] section release would write",
                   allow_abbrev=False)
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
    if args.command == "check":
        problems = check(root)
        if problems:
            _say(problems, sys.stderr)
            return 1
        _say([f"check: no problems in {_plural(len(_entries(root)), 'entry file')}"], sys.stdout)
        return 0
    try:
        sys.stdout.write(preview(root))
    except Refused as e:
        trailer = [f"{args.command} refused: fix the changelog.d/ problems above"]
        _say(e.lines + (trailer if e.check_failed else []), sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
