"""Gantry's own WBS.md reader, a stopgap until devagent exports one (#8).

The grammar is devagent's (scripts/lib/wbs-parser.py), ported rule by rule and
never called: fences, node lines, nesting, title and metadata. On top of it
this module names every node, reports what a page cannot use, and leaves
dependency resolution to gantry.deps. docs/worksite-page.md states the rules.
"""

import re

from gantry.deps import classify, resolve
from gantry.model import (
    FINISHED_GLYPHS,
    REPORT_KINDS,
    STATE_GLYPHS,
    Leaf,
    ReportEntry,
    Row,
    SiteData,
    meta_value,
    meta_values,
)
from gantry.priority import is_valid

# devagent's _BULLET_RE and _FENCE_RE, verbatim.
NODE_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<glyph>[ x\-!~?P])\]\s+(?P<rest>.+)$")
FENCE_RE = re.compile(r"^[ \t]*(?P<run>`{3,}|~{3,})(?P<info>.*)$")
# A line that looks like a node but is not one: reported as skipped_line.
SKIP_RE = re.compile(r"^\s*- \[(?P<glyph>[^\]]?)\]")

MESSAGES = {
    "skipped_line": "WBS.md line {line}: not a node: {why}",
    "why_glyph": "glyph {glyph!r} is not one of ' ', 'x', '-', '!', '~', '?', 'P'",
    "why_text": "the glyph needs whitespace and text after it",
    "no_nodes": "WBS.md has no node lines",
    "no_issue": "WBS.md line {line}: leaf has no issue: id; it is named {key!r}",
    "duplicate_issue": "WBS.md line {line}: issue {issue!r} is also on line {first}; "
                       "references resolve to line {first}, and this node is named {key!r}",
    "key_collision": "WBS.md line {line}: outline position {outline!r} is the issue: id on "
                     "line {other}; this node is named {key!r}",
    "bad_priority": "WBS.md line {line}: priority {raw!r} is not P1, P2, P3 or P4; it counts as P3",
}


def _body(line):
    """A line without its terminator (`splitlines` decides what ends a line)."""
    parts = line.splitlines()
    return parts[0] if parts else ""


def _fenced(bodies):
    """Which lines belong to a fence (opener, inside or closer).

    devagent's pairing: an opener pairs with the first later line of the same
    character, a run at least as long and no info text; an opener with no
    closer is plain text.
    """
    n = len(bodies)
    fenced = [False] * n
    i = 0
    while i < n:
        m = FENCE_RE.match(bodies[i])
        if not m or (m.group("run")[0] == "`" and "`" in m.group("info")):
            i += 1
            continue
        run = m.group("run")
        close = None
        for j in range(i + 1, n):
            c = FENCE_RE.match(bodies[j])
            if c and c.group("run")[0] == run[0] and len(c.group("run")) >= len(run) \
                    and not c.group("info").strip():
                close = j
                break
        if close is None:
            i += 1
            continue
        for j in range(i, close + 1):
            fenced[j] = True
        i = close + 1
    return fenced


def split_title_meta(rest: str) -> tuple[str, str | None]:
    """(title, metadata body or None): the trailing `{...}` block, if any.

    The same split as devagent's `\\s*\\{(?P<body>[^{}]*)\\}\\s*$` search, in
    linear time: that search is quadratic on a long whitespace run.
    """
    s = rest.rstrip()
    if s.endswith("}"):
        j = s.rfind("{")
        if j >= 0 and "}" not in s[j + 1:-1]:
            return s[:j].rstrip(), s[j + 1:-1]
    return s.strip(), None


def split_meta(body: str) -> dict[str, str]:
    """A metadata body's keys and raw value texts (devagent's _parse_meta scan).

    A list keeps its brackets and a quoted value its quotes, so meta_value can
    read the text as devagent does; a later duplicate key wins.
    """
    out = {}
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] in " ,\t":
            i += 1
        if i >= n:
            break
        key_start = i
        while i < n and body[i] != ":":
            i += 1
        key = body[key_start:i].strip()
        if i >= n or not key:
            break
        i += 1
        while i < n and body[i] in " \t":
            i += 1
        if i < n and body[i] == "[":
            end = body.find("]", i)
            if end == -1:
                out[key] = body[i:].strip()
                break
            out[key] = body[i:end + 1]
            i = end + 1
        elif i < n and body[i] in "\"'":
            start = i
            i += 1
            while i < n and body[i] != body[start]:
                i += 1
            if i < n:
                i += 1
            out[key] = body[start:i]
        else:
            start = i
            while i < n and body[i] != ",":
                i += 1
            out[key] = body[start:i].strip()
    return out


def _issue(meta):
    """A node's issue: id: a non-empty scalar, else None."""
    raw = meta.get("issue")
    if raw is None:
        return None
    value = meta_value(raw)
    return value if isinstance(value, str) and value else None


class _Node:
    __slots__ = ("line", "glyph", "title", "meta", "parent", "outline", "children",
                 "issue", "key")

    def __init__(self, line, glyph, title, meta, parent, outline):
        self.line, self.glyph, self.title, self.meta = line, glyph, title, meta
        self.parent, self.outline = parent, outline
        self.children = 0
        self.issue = _issue(meta)
        self.key = None


def _scan(bodies, site, report):
    """The node lines, in file order, with parents and outlines."""
    fenced = _fenced(bodies)
    nodes = []
    stack = []  # (depth, node)
    root_children = 0
    for line, body in enumerate(bodies, 1):
        if fenced[line - 1]:
            continue
        m = NODE_RE.match(body)
        if not m:
            s = SKIP_RE.match(body)
            if s:
                glyph = s.group("glyph")
                why = (MESSAGES["why_text"] if glyph and glyph in STATE_GLYPHS
                       else MESSAGES["why_glyph"].format(glyph=glyph))
                report.append(ReportEntry(site, line, "skipped_line",
                                          MESSAGES["skipped_line"].format(line=line, why=why)))
            continue
        depth = len(m.group("indent").expandtabs(2)) // 2 + 1
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if parent is None:
            root_children += 1
            outline = str(root_children)
        else:
            parent.children += 1
            outline = f"{parent.outline}.{parent.children}"
        title, body_meta = split_title_meta(m.group("rest").rstrip())
        meta = split_meta(body_meta) if body_meta is not None else {}
        node = _Node(line, m.group("glyph"), title, meta, parent, outline)
        nodes.append(node)
        stack.append((depth, node))
    return nodes


def _name(nodes, site, report):
    """Key every node; return {issue: first holder's key}."""
    first = {}
    for node in nodes:
        if node.issue is not None and node.issue not in first:
            first[node.issue] = node
            node.key = node.issue
    for node in nodes:
        if node.key is not None:
            continue
        key = node.outline
        if key in first:
            other = first[key].line
            while key in first:
                key += f"~{node.line}"
            report.append(ReportEntry(site, node.line, "key_collision", MESSAGES["key_collision"].format(
                line=node.line, outline=node.outline, other=other, key=key)))
        node.key = key
        if node.issue is not None:
            report.append(ReportEntry(site, node.line, "duplicate_issue", MESSAGES["duplicate_issue"].format(
                line=node.line, issue=node.issue, first=first[node.issue].line, key=key)))
    return {issue: node.key for issue, node in first.items()}


def _top(node):
    while node.parent is not None:
        node = node.parent
    return node


def _build(nodes, site, report):
    leaves, rows = [], []
    for node in nodes:
        state = STATE_GLYPHS[node.glyph]
        priority = node.meta.get("priority")
        if priority is not None and not is_valid(priority):
            report.append(ReportEntry(site, node.line, "bad_priority", MESSAGES["bad_priority"].format(
                line=node.line, raw=priority)))
        parent = node.parent.key if node.parent is not None else None
        if node.children:
            rows.append(Row(node.key, node.issue, node.outline, node.title, node.glyph, state,
                            node.line, parent, node.meta))
            continue
        if node.issue is None:
            report.append(ReportEntry(site, node.line, "no_issue", MESSAGES["no_issue"].format(
                line=node.line, key=node.key)))
        deps = tuple(classify(e, site) for e in meta_values(node.meta.get("depends_on")))
        epic = _top(node).key if node.parent is not None else None
        leaves.append(Leaf(node.key, node.issue, node.outline, node.title, node.glyph, state,
                           node.glyph not in FINISHED_GLYPHS, priority, deps, node.meta, epic,
                           node.line))
    return leaves, rows


def parse_wbs(text: str, site: str) -> SiteData:
    """A WBS.md text's nodes and report, for the site named `site`."""
    bodies = [_body(line) for line in text.splitlines(keepends=True)]
    report = []
    nodes = _scan(bodies, site, report)
    by_issue = _name(nodes, site, report)
    leaves, rows = _build(nodes, site, report)
    if not nodes:
        report.append(ReportEntry(site, None, "no_nodes", MESSAGES["no_nodes"]))
    links, held_out, resolved = resolve(site, leaves, rows, by_issue)
    report += resolved
    report.sort(key=lambda e: (e.line is not None, e.line or 0, REPORT_KINDS.index(e.kind)))
    return SiteData(tuple(leaves), tuple(rows), links, held_out, tuple(report))
