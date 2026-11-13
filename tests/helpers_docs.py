"""Markdown rules for the docs tests (#46): GitHub heading anchors, relative
links, the docs index and the README's view lines.

A plain module, not a test module; tests/test_docs.py drives it against the
real tree and against planted trees, so a guard and its probe share one
predicate. Text is normalised to LF here, whatever the caller passes, since
README.md and docs/ are CRLF in a Windows working tree.

The universe of docs is docs/**/*.md as git sees it in a checkout (tracked,
plus untracked files that are not ignored, so a new doc counts before its
commit and an ignored scratch file never does), or every docs/**/*.md in a
planted tree that is not a git work tree, or with no git.

This is a small re-implementation of GitHub's Markdown, and it diverges at
its edges. Handled: ATX headings with an optional closing sequence; ``` and
~~~ fences (a backtick opener whose info string holds a backtick is no
fence; an unclosed fence runs to the end of the file); code spans, which
never cross a blank line, a list item or a table row; inline links and
images with a <destination> or a "title"; reference definitions. Blind
spots, never checked: raw-HTML <a href>, <img src> and <a id> anchors;
setext headings; indented code blocks (a link inside one is still read);
links inside link text; reference-style link uses ([text][ref]), whose
definitions are checked instead.
"""

import os
import re
from pathlib import Path
from urllib.parse import unquote

from test_samples import decode_bytes, git, git_work_tree

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
LINK = re.compile(r'!?\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+"[^"]*")?\s*\)')
REF_DEF = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s*(<[^>]*>|\S+)", re.M)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
BLOCK_START = re.compile(r"^\s*(?:[-*+]|\d+[.)])(?:\s|$)|^\s*\|")
INDEX_ITEM = re.compile(r"^\s*[-*+]\s+\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)")
PRIVACY = "## Your data stays private"


def _lf(text):
    return text.replace("\r\n", "\n")


def _unwrap(dest):
    return dest[1:-1] if dest.startswith("<") and dest.endswith(">") else dest


def markdown_text(path):
    """The file's text, UTF-8, with CRLF read as LF."""
    return _lf(Path(path).read_bytes().decode("utf-8"))


def unfenced_lines(text):
    """The text's lines with each fenced line replaced by None."""
    lines, fence = [], None
    for line in _lf(text).split("\n"):
        if fence is None:
            m = FENCE.match(line)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
                lines.append(None)
            else:
                lines.append(line)
            continue
        lines.append(None)
        close = line.strip()
        if close and set(close) == {fence[0]} and len(close) >= len(fence) and len(line) - len(line.lstrip(" ")) <= 3:
            fence = None
    return lines


def slug(title):
    """GitHub's heading anchor: link text kept, lower case, every character but
    word characters (letters, digits, _), '-' and ' ' dropped, each space to '-'."""
    title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
    return re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")


def heading_anchors(text):
    """Every heading's anchor in document order; a repeated slug s gains -1, -2, ..."""
    anchors, seen = [], {}
    for line in unfenced_lines(text):
        m = line is not None and HEADING.match(line)
        if not m:
            continue
        base = slug(m.group(2) or "")
        n = seen.get(base, 0)
        seen[base] = n + 1
        anchors.append(base if n == 0 else f"{base}-{n}")
    return anchors


def _blocks(text):
    """Unfenced text split into blocks: a blank or fenced line ends one, a list
    item or table row starts one, so a code span never crosses them."""
    blocks, current = [], []
    for line in unfenced_lines(text):
        if line is None or not line.strip() or BLOCK_START.match(line):
            if current:
                blocks.append("\n".join(current))
            current = [line] if line is not None and line.strip() else []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _absolute(dest):
    return dest.startswith("//") or bool(SCHEME.match(dest))


def relative_links(text):
    """Every relative link and image destination, as written (a <dest> unwrapped)."""
    found = []
    for block in _blocks(text):
        block = CODE_SPAN.sub("", block)
        dests = [(m.start(), m.group(1)) for m in LINK.finditer(block)]
        dests += [(m.start(), m.group(1)) for m in REF_DEF.finditer(block)]
        for _, dest in sorted(dests):
            dest = _unwrap(dest)
            if dest and not _absolute(dest):
                found.append(dest)
    return found


def doc_files(root):
    """docs/**/*.md under root, posix-relative, sorted (see the module docstring)."""
    root = Path(root)
    if git_work_tree(root):
        result = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "docs/")
        assert result.returncode == 0, decode_bytes(result.stderr)
        found = decode_bytes(result.stdout).split("\0")
        return sorted(n for n in found if n.endswith(".md") and (root / n).is_file())
    return sorted(p.relative_to(root).as_posix() for p in (root / "docs").rglob("*.md"))


def _resolve(root, base, path):
    """(resolved path, reason) for a link path from folder `base`; reason is None when it exists."""
    if path.startswith("/"):
        return None, "leaves the tree"
    joined = os.path.normpath(os.path.join(base, path))
    rel = os.path.relpath(joined, root)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None, "leaves the tree"
    here = Path(root)
    for part in [] if rel == os.curdir else Path(rel).parts:
        names = os.listdir(here) if here.is_dir() else []
        if part not in names:
            if part.lower() in {n.lower() for n in names}:
                return None, "no such file or folder (exact case)"
            return None, "no such file or folder"
        here = here / part
    return here, None


def _link_problem(root, source, dest):
    path, _, fragment = dest.partition("#")
    path, fragment = unquote(path), unquote(fragment)
    target, reason = (source, None) if not path else _resolve(root, source.parent, path)
    if reason:
        return reason
    if not fragment:
        return None
    if not (target.is_file() and target.suffix == ".md"):
        return "a fragment needs a Markdown target"
    if fragment not in heading_anchors(markdown_text(target)):
        return "no heading with that anchor"
    return None


def link_report(root):
    """(links checked, problems) over README.md and every doc; each problem
    `<file>: <destination as written>: <reason>`."""
    root = Path(root)
    files = (["README.md"] if (root / "README.md").is_file() else []) + doc_files(root)
    checked, problems = 0, []
    for rel in files:
        source = root / rel
        for dest in relative_links(markdown_text(source)):
            checked += 1
            reason = _link_problem(root, source, dest)
            if reason:
                problems.append(f"{rel}: {dest}: {reason}")
    return checked, problems


def index_links(text):
    """Each list item's leading link destination in the docs index, in order."""
    found = []
    for line in unfenced_lines(text):
        m = line is not None and INDEX_ITEM.match(line)
        if m:
            found.append(_unwrap(m.group(1)))
    return found


def index_problems(root):
    """Each doc under docs/ must be the leading link of exactly one item in docs/README.md."""
    root = Path(root)
    index = root / "docs" / "README.md"
    if not index.is_file():
        return ["docs/README.md: missing"]
    counts = {}
    for dest in index_links(markdown_text(index)):
        path = unquote(dest.partition("#")[0])
        rel = os.path.relpath(os.path.normpath(os.path.join(index.parent, path)), root)
        key = Path(rel).as_posix()
        counts[key] = counts.get(key, 0) + 1
    problems = []
    for doc in doc_files(root):
        if doc == "docs/README.md":
            continue
        n = counts.get(doc, 0)
        if n == 0:
            problems.append(f"{doc}: not listed in docs/README.md")
        elif n > 1:
            problems.append(f"{doc}: listed {n} times in docs/README.md")
    return problems


def target_exists(root, target):
    """True when the target's file exists (exact case) and, with an anchor, has that
    heading: the link rule, read from the root."""
    root = Path(root)
    return (root / target.partition("#")[0]).is_file() and _link_problem(root, root / "README.md", target) is None


def view_item(readme_text, label):
    """Every list item whose first line starts `- **<label>**`, joined with its
    indented continuation lines."""
    items, current = [], None
    start = f"- **{label}**"
    for line in _lf(readme_text).split("\n"):
        if current is not None and line.strip() and line[:1] in (" ", "\t"):
            current.append(line.strip())
            continue
        if current is not None:
            items.append(" ".join(current))
            current = None
        if line.startswith(start):
            current = [line]
    if current is not None:
        items.append(" ".join(current))
    return items


def view_line_problems(root, view_map):
    """Each view's README line links its target when the target exists and reads
    "(planned)", with no link, when it does not."""
    readme = _readme(root)
    problems = []
    for label, target in view_map.items():
        items = view_item(readme, label)
        if len(items) != 1:
            problems.append(f"{label}: README.md needs exactly one '- **{label}**' item, found {len(items)}")
            continue
        item = items[0]
        if target_exists(root, target):
            if f"]({target})" not in item or "(planned)" in item:
                problems.append(f"{label}: {target} exists, so the line must link it")
        elif "(planned)" not in item or "](" in item:
            problems.append(f"{label}: {target} does not exist, so the line must read (planned) with no link")
    return problems


def _readme(root):
    path = Path(root) / "README.md"
    return markdown_text(path) if path.is_file() else ""


def section(text, heading):
    """The lines under a whole-line `heading` up to the next `## ` heading, or None without it."""
    lines = _lf(text).split("\n")
    if heading not in lines:
        return None
    start = lines.index(heading) + 1
    end = next((n for n in range(start, len(lines)) if lines[n].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def privacy_link_problems(root, doc="docs/refresh-and-serve.md"):
    """The README's privacy section links `doc` when it exists and never names it before."""
    text = section(_readme(root), PRIVACY)
    if text is None:
        return [f"README.md: no '{PRIVACY}' section"]
    name = PRIVACY.removeprefix("## ")
    if (Path(root) / doc).is_file():
        if f"]({doc})" not in text:
            return [f"{name}: {doc} exists, so the section must link it"]
    elif Path(doc).name in text:
        return [f"{name}: {doc} does not exist, so the section must not link it"]
    return []
