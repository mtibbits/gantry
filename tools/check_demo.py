"""Check the demo output folder, or the Pages source setting (#45).

    python tools/check_demo.py <output folder>
    python tools/check_demo.py --pages-source <file>

The first form checks a folder `tools/demo_build.py` wrote against the
expected list (EXPECTED) and the demo's rules: every page linked from the
index, every link landing, one page per built sample site, the sample stamp
and no build time, nothing loaded from outside, and the sample hostile title
shown as text. It prints every finding as `check_demo: <file>: <problem>`
(file relative to the folder) and exits 1, or prints one `ok:` line and
exits 0.

The second form reads a saved `GET repos/<owner>/<repo>/pages` response and
exits 0 only when its build_type is "workflow" (Settings, Pages, Source:
GitHub Actions); it prints one line and never the rest of the response.

Exactly one form is given; anything else is a usage error, exit 2.
docs/demo.md states the rules and the expected list.
"""

import argparse
import json
import posixpath
import re
import sys
from collections import namedtuple
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
# page_safety and sample_roles live in tests/; their import contract is that
# a non-pytest consumer puts <root>/tests on sys.path.
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from page_safety import _scan, assert_no_external_resources, assert_page_safe  # noqa: E402
from sample_roles import HOSTILE_SITE, HOSTILE_TITLE, NOT_BUILT, SMALL  # noqa: E402

PROG = "check_demo"
MUST = '; it must be "workflow" (Settings, Pages, Source: GitHub Actions)'
SAMPLE_CONFIG = ("samples", "gantry.sample.toml")
DEVAGENT_CONFIG = ("samples", "devagent", "config.toml")

# --- the expected list ----------------------------------------------------------------

# kind: view (a page), sites (the site pages), overlay (an element by id on
# a page), data (a non-page file). state: required (must be present) or
# pending (its issue is open; must be absent until that issue marks it
# required in the same change). docs/demo.md has the table.
Entry = namedtuple("Entry", "kind path element issue state")
EXPECTED = (
    Entry("view", "index.html", None, 11, "required"),
    Entry("sites", "sites/*.html", None, 11, "required"),
    Entry("overlay", f"sites/{SMALL}.html", "trace", 20, "required"),
    Entry("view", "strategic.html", None, 23, "pending"),
    Entry("view", "daily.html", None, 34, "pending"),
    Entry("view", "week.html", None, 38, "pending"),
    Entry("data", "week-plan.json", None, 61, "required"),
)
PENDING = "pending on the expected list (#{}) but present; mark its entry required in the same change"

STAMP_TEXT = "sample data as of {}"
BUILT_RE = re.compile(r"built \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC; data as of")
SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")
# The markup the hostile title holds; a page whose source holds it unescaped
# shows the title as markup (page_safety's tag rules allow <b>).
MARKUP_CORE = re.search(r"<(\w+)>[^<]*</\1>", HOSTILE_TITLE).group(0)
MIN_PREFIX = 10
ELLIPSIS = "…"
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
                  "track", "wbr"})
RAW_TEXT = frozenset({"script", "style"})


# --- one page ---------------------------------------------------------------------------

class Page:
    """What the rules read from one page: ids, links, visible text, stamps, list items."""

    def __init__(self, source):
        self.source = source
        self.ids, self.links, self.texts, self.stamps, self.items = set(), [], [], [], []
        self.built_at = False
        raw = None  # the open script or style, whose text is not visible
        stamp = None  # [tag, nesting, direct text] of the open stamp element
        items = []  # [text, has a link] per open li
        for e in _scan(source):
            if e.is_start:
                attrs = dict(e.attrs)
                if attrs.get("id") is not None:
                    self.ids.add(attrs["id"])
                if "data-built-at" in attrs:
                    self.built_at = True
                if e.tag == "a" and attrs.get("href") is not None:
                    self.links.append(attrs["href"])
                    if items:
                        items[-1][1] = True
                if e.kind == "start" and e.tag in RAW_TEXT:
                    raw = e.tag
                if stamp is not None:
                    stamp[1] += e.kind == "start" and e.tag not in VOID
                elif "stamp" in (attrs.get("class") or "").split():
                    if e.kind == "start" and e.tag not in VOID:
                        stamp = [e.tag, 0, ""]
                    else:
                        self.stamps.append("")
                if e.kind == "start" and e.tag == "li":
                    items.append(["", False])
            elif e.kind == "end":
                if e.tag == raw:
                    raw = None
                if stamp is not None:
                    if stamp[1] == 0 and e.tag == stamp[0]:
                        self.stamps.append(" ".join(stamp[2].split()))
                        stamp = None
                    elif stamp[1] > 0:
                        stamp[1] -= 1
                if e.tag == "li" and items:
                    self.items.append(tuple(items.pop()))
            elif e.kind == "text" and raw is None:
                self.texts.append(e.data)
                if stamp is not None and stamp[1] == 0:
                    stamp[2] += e.data
                for item in items:
                    item[0] += e.data
        if stamp is not None:
            self.stamps.append(" ".join(stamp[2].split()))
        self.items += [tuple(item) for item in items]

    def shows_title(self) -> bool:
        """A text node holds the hostile title, or a prefix of it (10+ characters) cut with an ellipsis."""
        for text in self.texts:
            if HOSTILE_TITLE in text:
                return True
            if ELLIPSIS in text and any(HOSTILE_TITLE[:k] + ELLIPSIS in text
                                        for k in range(MIN_PREFIX, len(HOSTILE_TITLE))):
                return True
        return False

    def names_unlinked(self, name) -> bool:
        """A list item with no link whose visible text holds `name` as a word."""
        word = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
        return any(not linked and word.search(text) for text, linked in self.items)


def _assertion_lines(check, *args):
    try:
        check(*args)
    except AssertionError as e:
        return [line.strip() for line in str(e).splitlines()[1:] if line.strip()]
    return []


# --- the folder ---------------------------------------------------------------------------

def _matches(path, pattern) -> bool:
    """Whole-path glob match, `*` within one segment, case-exact."""
    p, q = path.split("/"), pattern.split("/")
    return len(p) == len(q) and all(fnmatchcase(a, b) for a, b in zip(p, q))


def _listing(folder):
    files, dirs = set(), set()
    for p in folder.rglob("*"):
        rel = p.relative_to(folder).as_posix()
        (dirs if p.is_dir() else files).add(rel)
    return files, dirs


def _read_pages(folder, files, findings):
    pages = {}
    for rel in sorted(f for f in files if f.endswith(".html")):
        try:
            pages[rel] = Page((folder / rel).read_bytes().decode("utf-8"))
        except UnicodeDecodeError:
            findings.append(f"{rel}: is not UTF-8")
        except OSError as e:
            findings.append(f"{rel}: cannot be read ({e.strerror or type(e).__name__})")
    return pages


def _link_problem(page_rel, href, files, dirs, pages):
    """None when a page-relative href lands; else its problem. Hrefs with a
    scheme or a leading slash are the external-resource rule's."""
    path, _, fragment = href.partition("#")
    if not path:
        target = page_rel
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), unquote(path)))
        if target == ".." or target.startswith("../"):
            return f"link {ascii(href)} leaves the folder"
        if target not in files or target in dirs or path.endswith("/"):
            return f"link {ascii(href)} names no file in the folder"
    if fragment and fragment not in (pages[target].ids if target in pages else set()):
        return f"link {ascii(href)} names no anchor in {target}"
    return None


def _is_external(href) -> bool:
    return bool(SCHEME_RE.match(href.strip())) or href.strip().startswith(("/", "\\"))


def _links(files, dirs, pages):
    """[(page, href, problem or None)] for every page-relative a[href], in page order."""
    found = []
    for rel, page in sorted(pages.items()):
        for href in page.links:
            if not _is_external(href):
                found.append((rel, href, _link_problem(rel, href, files, dirs, pages)))
    return found


def links(folder):
    """[(page, href, problem or None)] for every page-relative link under `folder`."""
    folder = Path(folder)
    files, dirs = _listing(folder)
    return _links(files, dirs, _read_pages(folder, files, []))


def _sample_inputs(root):
    import tomllib

    def load(parts):
        return tomllib.loads(root.joinpath(*parts).read_bytes().decode("utf-8"))

    try:
        return load(SAMPLE_CONFIG)["as_of"], set(load(DEVAGENT_CONFIG)["project"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _entry_present(entry, files, pages) -> bool:
    if entry.kind == "overlay":
        return entry.path in pages and entry.element in pages[entry.path].ids
    return any(_matches(f, entry.path) for f in files)


def _expected_findings(expected, files, pages):
    findings = []
    for e in expected:
        present = _entry_present(e, files, pages)
        if e.kind == "overlay":
            if e.state == "required" and not present:
                findings.append(f"{e.path}: element {e.element} required by the expected list (#{e.issue}) is missing")
            elif e.state == "pending" and present:
                findings.append(f"{e.path}: element {e.element} is " + PENDING.format(e.issue))
        elif e.state == "required" and not present:
            findings.append(f"{e.path}: required by the expected list (#{e.issue}) and missing")
        elif e.state == "pending" and present:
            findings += [f"{f}: " + PENDING.format(e.issue) for f in sorted(files) if _matches(f, e.path)]
    unlisted = sorted(f for f in files
                      if (not f.endswith(".html") or (f != "index.html" and not _matches(f, "sites/*.html")))
                      and not any(e.kind != "overlay" and _matches(f, e.path) for e in expected))
    findings += [f"{f}: on no entry of the expected list" for f in unlisted]
    return findings


def _site_findings(sites, files, pages):
    findings = []
    built = sites - NOT_BUILT
    for name in sorted(built):
        if f"sites/{name}.html" not in files:
            findings.append(f"sites/{name}.html: no page for the sample site {name}")
    for f in sorted(files):
        parts = f.split("/")
        if len(parts) == 2 and parts[0] == "sites" and parts[1].endswith(".html"):
            name = parts[1][:-len(".html")]
            if name in NOT_BUILT:
                findings.append(f"{f}: {name} is a NOT_BUILT site and has a page")
            elif name not in sites:
                findings.append(f"{f}: no sample site is named {name}")
    index = pages.get("index.html")
    targets = set()
    if index is not None:
        targets = {posixpath.normpath(unquote(h.partition("#")[0])) for h in index.links if not _is_external(h)}
        for name in sorted(NOT_BUILT & sites):
            if not index.names_unlinked(name):
                findings.append(f"index.html: does not name the NOT_BUILT site {name}")
            if f"sites/{name}.html" in targets:
                findings.append(f"index.html: links a page for the NOT_BUILT site {name}")
    findings += [f"{rel}: not linked from index.html" for rel in sorted(pages)
                 if rel != "index.html" and rel not in targets]
    return findings


def _page_findings(rel, page, as_of):
    findings = []
    stamp = STAMP_TEXT.format(as_of)
    if page.stamps != [stamp]:
        findings.append(f'{rel}: no stamp "{stamp}"')
    if BUILT_RE.search(page.source):
        findings.append(f"{rel}: carries a build time (built stamp)")
    if page.built_at:
        findings.append(f"{rel}: carries a build time (data-built-at)")
    lines = _assertion_lines(assert_no_external_resources, page.source)
    if lines:
        findings.append(f"{rel}: may load an external resource: " + "; ".join(lines))
    shows = page.shows_title()
    lines = _assertion_lines(assert_page_safe, page.source, [HOSTILE_TITLE] if shows else [])
    if lines:
        planted = " with the sample hostile title planted" if shows else ""
        findings.append(f"{rel}: breaks the page rules{planted}: " + "; ".join(lines))
    if MARKUP_CORE in page.source:
        findings.append(f"{rel}: shows the sample hostile title as markup")
    if rel == f"sites/{HOSTILE_SITE}.html" and not shows:
        findings.append(f"{rel}: does not show the sample hostile title")
    return findings


def check(folder, *, root=None, expected=None) -> list:
    """Every finding for the demo output folder, sorted; [] when it passes."""
    root = ROOT if root is None else Path(root)
    expected = EXPECTED if expected is None else expected
    folder = Path(folder)
    files, dirs = _listing(folder) if folder.is_dir() else (set(), set())
    if not any(f.endswith(".html") for f in files):
        return [".: the folder holds no page"]
    inputs = _sample_inputs(root)
    if inputs is None:
        return [".: cannot read as_of or the sample sites from the sample config"]
    as_of, sites = inputs
    findings = []
    pages = _read_pages(folder, files, findings)
    findings += [f"{rel}: {problem}" for rel, _, problem in _links(files, dirs, pages) if problem is not None]
    findings += _site_findings(sites, files, pages)
    findings += _expected_findings(expected, files, pages)
    for rel, page in pages.items():
        findings += _page_findings(rel, page, as_of)
    return sorted(set(findings))


def _folder(folder, root=None) -> int:
    findings = check(folder, root=root)
    if findings:
        for f in findings:
            print(f"{PROG}: {f}", file=sys.stderr)
        print(f"{PROG}: {len(findings)} problem(s)", file=sys.stderr)
        return 1
    files, _ = _listing(Path(folder))
    pages = [f for f in files if f.endswith(".html")]
    sites = [f for f in pages if _matches(f, "sites/*.html")]
    print(f"{PROG}: ok: {len(pages)} pages, {len(sites)} site pages, {len(EXPECTED)} expected entries")
    return 0


# --- the Pages source ------------------------------------------------------------------

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
    return _folder(args.folder)


if __name__ == "__main__":
    sys.stdout.reconfigure(newline="\n")
    sys.exit(main())
