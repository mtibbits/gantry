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
stderr for a problem, a refusal or a file that cannot be read, none of which
changes anything except a failed delete after CHANGELOG.md is written, whose
message says so; 2 for a usage error.
README's "Versioning & releases" says when to run each command.
"""

import argparse
import datetime
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
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # fullmatch, then a real calendar date
# Copied from tests/test_plugin_versioning.py, which a tool may not import;
# tests/test_changelog.py keeps the two copies in agreement.
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")  # fullmatch only; ASCII digits
RELEASE_HEADING = re.compile(r"## \[(?P<version>[^\]]+)\](?: — (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}))?")  # fullmatch


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
    opened = _fence_scan(lines)[1]
    if opened is not None:
        # Left open in CHANGELOG.md, it would hide every older section.
        return None, None, None, f"line {opened + 1} opens a code fence that it never closes"
    return key, kind, items, None


def _read_entries(root):
    """(path, read_entry(path)) for each entry in changelog.d/, by name."""
    return [(path, read_entry(path)) for path in _entries(root)]


def _problems(entries):
    return [f"{_shown(path.name)}: {entry[3]}" for path, entry in entries if entry[3]]


def check(root):
    """Every problem in changelog.d/, one per entry at most, by entry name."""
    return _problems(_read_entries(root))


# --- CHANGELOG.md -----------------------------------------------------------------

class Refused(Exception):
    """A reason to exit 1: one or more message lines. Only _write's failed
    delete comes after a change, and its message says so."""


class CheckFailed(Refused):
    """Refused for changelog.d/ problems; main adds its `<command> refused` line."""


def read_changelog(root):
    """CHANGELOG.md's lines, read as UTF-8 with CRLF as LF, or Refused."""
    path = Path(root) / CHANGELOG
    if not path.is_file():
        raise Refused(f"{CHANGELOG}: missing")
    try:
        return _lf(path.read_bytes().decode("utf-8")).split("\n")
    except UnicodeDecodeError:
        raise Refused(f"{CHANGELOG}: not UTF-8") from None


def _fence_scan(lines):
    """(unfenced lines, index of the line opening a fence left open at the end, or None)."""
    out, fence, opened = [], None, None
    for i, line in enumerate(lines):
        if fence is None:
            m = FENCE.match(line)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence, opened = m.group(1), i
                out.append(None)
            else:
                out.append(line)
            continue
        out.append(None)
        close = line.strip()
        if (close and set(close) == {fence[0]} and len(close) >= len(fence)
                and len(line) - len(line.lstrip(" ")) <= 3):
            fence = None
    return out, (opened if fence is not None else None)


def unfenced(lines):
    """The lines with each line of a ``` or ~~~ fence replaced by None."""
    return _fence_scan(lines)[0]


def _section_end(plain, start):
    """The index of the first unfenced ## line after `start`, or len(plain)."""
    return next((i for i in range(start + 1, len(plain))
                 if plain[i] is not None and plain[i].startswith("## ")), len(plain))


def unreleased_span(plain):
    """(index of the ## [Unreleased] line, index of the next ## line or the end)
    in `plain`, the unfenced() lines; or Refused."""
    if UNRELEASED not in plain:
        raise Refused(f"{CHANGELOG} has no '{UNRELEASED}' line")
    start = plain.index(UNRELEASED)
    return start, _section_end(plain, start)


def _no_items():
    return {kind: [] for kind in KINDS}


def carried_items(lines, start, end):
    """The items under ## [Unreleased] per kind, in document order, or Refused.

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


def _key_order(key):
    return (0, len(key), key) if NUMBER_KEY.fullmatch(key) else (1, 0, key)


def waiting(root, lines, start, end):
    """(items per kind, the entry files used) for CHANGELOG.md's `lines`, whose
    ## [Unreleased] spans [start, end): carried items first, in document order,
    then each entry file's items, issue numbers before slugs; or Refused."""
    entries = _read_entries(root)
    problems = _problems(entries)
    if problems:
        raise CheckFailed(*problems)
    items = carried_items(lines, start, end)
    entries.sort(key=lambda pair: _key_order(pair[1][0]))
    for _, (_, kind, entry_items, _) in entries:
        items[kind].extend(entry_items)
    return items, [path for path, _ in entries]


def render(heading, items):
    """`heading`, then each kind that has items, in KINDS order: a blank line,
    its heading, a blank line and its item lines, none between items."""
    out = [heading]
    for kind, kind_heading in zip(KINDS, HEADINGS):
        if items[kind]:
            out += ["", kind_heading, ""]
            for item in items[kind]:
                out += item
    return "\n".join(out) + "\n"


def preview(root):
    """The ## [Unreleased] section release would write, or Refused as release is."""
    lines = read_changelog(root)
    start, end = unreleased_span(unfenced(lines))
    return render(UNRELEASED, waiting(root, lines, start, end)[0])


# --- release ------------------------------------------------------------------------

def _check_version(version):
    if not SEMVER.fullmatch(version):
        raise Refused(f"{version!r} is not a plain X.Y.Z version")


def _check_date(date):
    try:
        if DATE.fullmatch(date) and datetime.date.fromisoformat(date):
            return
    except ValueError:
        pass
    raise Refused(f"{date!r} is not a YYYY-MM-DD date")


def _count(items):
    return sum(len(kind_items) for kind_items in items.values())


def _waiting_items(root, lines, start, end):
    """waiting(), or Refused when it holds no item."""
    items, files = waiting(root, lines, start, end)
    if not _count(items):
        raise Refused(f"nothing is waiting: no entry files in {FOLDER}/ and no items under {UNRELEASED}")
    return items, files


def _with_section(lines, start, section, rest):
    """The file: lines before ## [Unreleased], its pointer lines, `section`,
    then, after a blank line, lines[rest:] when any remain."""
    out = lines[:start] + [UNRELEASED, "", POINTER, ""] + section.rstrip("\n").split("\n")
    if rest < len(lines):
        return "\n".join(out + [""] + lines[rest:])
    return "\n".join(out) + "\n"


def _write(root, text, files):
    """Write CHANGELOG.md, then delete `files`: Refused when the write fails,
    and Refused naming the files left, CHANGELOG.md written, when a delete fails.

    A write can fail after open() has truncated the file (a full disk), so a
    failed write puts the original bytes back before saying nothing changed."""
    path = Path(root) / CHANGELOG
    original = path.read_bytes()
    try:
        path.write_bytes(text.encode("utf-8"))
    except OSError as e:
        reason = e.strerror or type(e).__name__
        try:
            if path.read_bytes() != original:
                path.write_bytes(original)
        except OSError:
            raise Refused(f"{CHANGELOG}: could not write ({reason}), nor restore it; "
                          f"no entry file was deleted; restore {CHANGELOG} from git") from None
        raise Refused(f"{CHANGELOG}: could not write ({reason}); nothing changed") from None
    left = []
    for entry in files:
        try:
            entry.unlink()
        except OSError:
            left.append(_shown(entry.name))
    if left:
        raise Refused(f"{CHANGELOG} now holds these items; delete these files by hand, "
                      f"do not fold them: {', '.join(left)}")
    return len(files)


def release(root, version, date):
    """Write ## [<version>] — <date> from what is waiting, above the newest
    dated section, and delete the entry files used: (items, files deleted, heading)."""
    _check_version(version)
    _check_date(date)
    lines = read_changelog(root)
    plain = unfenced(lines)
    start, end = unreleased_span(plain)
    for line in plain:
        m = RELEASE_HEADING.fullmatch(line) if line is not None else None
        if m and m.group("version") == version:
            raise Refused(f"{CHANGELOG} already has a section for {version}; "
                          "add to it with --fold before it is tagged, or release a new version")
    items, files = _waiting_items(root, lines, start, end)
    heading = f"## [{version}] — {date}"
    text = _with_section(lines, start, render(heading, items), end)
    return _count(items), _write(root, text, files), heading


def section_items(lines):
    """A section's items per kind, read leniently: an item under no kind
    heading, or under an unknown heading, is left out, so render() of the
    result differs from a section that holds one."""
    items, kind, current = _no_items(), None, None
    for line in lines:
        role = line_role(line, current is not None)
        if line in HEADINGS:
            kind, current = KINDS[HEADINGS.index(line)], None
        elif HEADING.match(line):
            kind, current = None, None
        elif role == "item" and kind is not None:
            current = [line]
            items[kind].append(current)
        elif role == "more":
            current.append(line)
        else:
            current = None
    return items


def fold(root, version):
    """Add what is waiting to the newest dated section, which must be
    <version>'s and in the form release writes, and delete the entry files
    used: (items, files deleted, heading)."""
    _check_version(version)
    lines = read_changelog(root)
    plain = unfenced(lines)
    start, end = unreleased_span(plain)
    m = RELEASE_HEADING.fullmatch(lines[end]) if end < len(lines) else None
    if not (m and m.group("date") and m.group("version") == version):
        newest = "none" if end == len(lines) else m.group("version") if m else lines[end][3:]
        raise Refused(f"--fold adds only to the newest dated section, which is {newest}, "
                      f"not {version}; release a new version instead")
    heading = lines[end]
    rest = _section_end(plain, end)
    section = lines[end:rest]
    while len(section) > 1 and not section[-1].strip():
        section.pop()  # the blank lines before the next section are release's separator
    target = section_items(section[1:])
    expected = render(heading, target).rstrip("\n").split("\n")
    for i, (have, want) in enumerate(zip(section + [None], expected + [None])):
        if have != want:
            raise Refused(f"{CHANGELOG} line {end + i + 1}: "
                          f"{heading} is not in the form release writes, so --fold cannot add "
                          "to it; release a new version instead")
    items, files = _waiting_items(root, lines, start, end)
    for kind in KINDS:
        target[kind] += items[kind]
    text = _with_section(lines, start, render(heading, target), rest)
    return _count(items), _write(root, text, files), heading


def build_parser():
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0],
                                     allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="report entry files that break the rules", allow_abbrev=False)
    sub.add_parser("preview", help="print the ## [Unreleased] section release would write",
                   allow_abbrev=False)
    p = sub.add_parser("release", help="write a release's section and delete the entry files used",
                       allow_abbrev=False)
    p.add_argument("version", help="the release's X.Y.Z version")
    p.add_argument("date", nargs="?", help="the release's YYYY-MM-DD date; none with --fold")
    p.add_argument("--fold", action="store_true",
                   help="add what is waiting to <version>'s section, the newest dated one")
    return parser


def parse_args(argv):
    """The parsed arguments; a usage error exits 2."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "release" and args.fold and args.date is not None:
        parser.error("release --fold takes no date; the section keeps its own")
    if args.command == "release" and not args.fold and args.date is None:
        parser.error("release needs a date, or --fold")
    return args


def _say(lines, stream):
    for line in lines:
        print(f"{PROG}: {line}", file=stream)


def main(argv=None, root=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    args = parse_args(argv)
    root = Path(ROOT if root is None else root)
    try:
        if args.command == "check":
            entries = _read_entries(root)
            problems = _problems(entries)
            if problems:
                _say(problems, sys.stderr)
                return 1
            _say([f"check: no problems in {_plural(len(entries), 'entry file')}"], sys.stdout)
        elif args.command == "preview":
            sys.stdout.write(preview(root))
        elif args.fold:
            items, deleted, heading = fold(root, args.version)
            _say([f"folded {_plural(items, 'item')} into {heading}: "
                  f"{_plural(deleted, 'entry file')} deleted"], sys.stdout)
        else:
            items, deleted, heading = release(root, args.version, args.date)
            _say([f"wrote {heading}: {_plural(items, 'item')}, "
                  f"{_plural(deleted, 'entry file')} deleted"], sys.stdout)
    except Refused as e:
        trailer = [f"{args.command} refused: fix the changelog.d/ problems above"]
        _say(list(e.args) + (trailer if isinstance(e, CheckFailed) else []), sys.stderr)
        return 1
    except OSError as e:  # a failed read: _write reports its own failures, so nothing was written
        where = Path(e.filename).relative_to(root).as_posix() if e.filename else "a file"
        _say([f"{where}: could not read ({e.strerror or type(e).__name__})"], sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
