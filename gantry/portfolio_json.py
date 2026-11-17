"""devagent's portfolio JSON, read into Gantry's site model (#83).

devagent writes one JSON document over every configured project's WBS.md
(its `scripts/portfolio.sh`, schema 1.x). This module reads that document:

    read_portfolio(roots, path) -> PortfolioInput
    map_document(doc) -> PortfolioInput

read_portfolio loads the file under the run's roots and checks it;
map_document does the same checks and mapping on a document that is already
parsed. `gantry build` does not call either yet. The first callers are #12
(the build switch) and #84 (the test builder), and both signatures are
provisional until one of them binds.

Neither entry point raises anything but PortfolioLoadError, whatever the
file or the document holds: every walk here is a loop, never a recursion, and
no parent link is followed before it has been checked.
docs/worksite-page.md states the rules.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from gantry.config import error_reason
from gantry.deps import refs, resolve
from gantry.model import (
    FINISHED_GLYPHS,
    STATE_GLYPHS,
    Leaf,
    ParsedValue,
    ReportEntry,
    Row,
    SiteData,
    report_order,
)
from gantry.priority import is_valid
from gantry.roots import OutsideRoots, read_bytes_confined

SCHEMA_ID = "devagent-portfolio"
# The schema this reader was written against. Another major is refused; any
# minor of this major is read, and a field the reader does not use is ignored.
MAJOR, MINOR = 1, 1
MAX_DEPTH = 64  # the deepest a node may nest
STATUSES = ("read", "no_wbs", "no_devdoc_dir", "unreadable")
# Bounded digit groups: int() never sees a long run.
VERSION_RE = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)")

ROOTS_KEY = "portfolio_json"
DEFAULT_SOURCE = "the portfolio document"
LOAD_MESSAGES = {
    "missing": "{source}: devagent's portfolio JSON does not exist. Run devagent's aggregator first "
               "(/devagent:portfolio).",
    "unreadable": "{source}: cannot read devagent's portfolio JSON: {reason}",
    "outside": "{source}: devagent's portfolio JSON is outside the configured roots; add its folder to the "
               "installation's roots",
    "not_json": "{source}: devagent's portfolio JSON is not valid JSON: {reason}. Run devagent's aggregator again "
                "(/devagent:portfolio).",
    "version": '{source}: portfolio schema_version "{found}" is not supported: Gantry reads major '
               f"{MAJOR} (schema {MAJOR}.{MINOR})",
    "schema": "{source}: not a portfolio document Gantry can read: {where}: {problem}",
}
# Why a file is not JSON, in Gantry's words: the interpreter's own text differs by version and host.
NOT_JSON = {
    "syntax": "syntax error at line {line} column {column}",
    "utf8": "not UTF-8 at byte {byte}",
    "number": "a number is too long",
    "depth": "nesting is too deep",
    "constant": "NaN or Infinity is not JSON",
}
GLYPH_LIST = ", ".join(repr(glyph) for glyph in STATE_GLYPHS)
# The texts of gantry/wbs_stopgap.py's entries of the same kinds, copied: this
# module never imports the stopgap, which #12 deletes.
MESSAGES = {
    "key_collision": "WBS.md line {line}: outline position {outline!r} is the issue: id on "
                     "line {other}; this node is named {key!r}",
    "bad_priority": "WBS.md line {line}: priority {raw!r} is not P0, P1, P2, P3 or P4; it counts as P3",
    "no_nodes": "WBS.md has no node lines",
    "no_issue": "WBS.md line {line}: leaf has no issue: id; it is named {key!r}",
    "duplicate_issue": "WBS.md line {line}: issue {issue!r} is also on line {first}; "
                       "references resolve to line {first}, and this node is named {key!r}",
}


class PortfolioLoadError(Exception):
    """Why the document cannot be read. `kind` is "unreadable" (missing,
    outside the roots, or any other failed read), "not_json", "version" or
    "schema"; `source` is the path as given, or map_document's `source`."""

    def __init__(self, kind, source, message):
        super().__init__(message)
        self.kind, self.source = kind, source


@dataclass(frozen=True)
class DevagentEntry:
    """One entry of devagent's parse report that Gantry shows as devagent wrote it."""
    project: str
    line: int | None  # None: no line
    kind: str  # devagent's kind: never one of Gantry's REPORT_KINDS by contract
    message: str


@dataclass(frozen=True)
class ProjectInput:
    name: str
    status: str  # one of STATUSES, as written
    reason: str | None  # devagent's status_reason; None when read
    data: SiteData  # empty unless read; its report holds Gantry's kinds only


@dataclass(frozen=True)
class PortfolioInput:
    projects: tuple[ProjectInput, ...]  # in document order
    devagent_report: tuple[DevagentEntry, ...]  # the shown entries, in document order
    generated_at: datetime  # aware, UTC
    schema_version: str  # as written


def _error(kind, source, text=None, **fields):
    return PortfolioLoadError(kind, source, LOAD_MESSAGES[text or kind].format(source=source, **fields))


# --- load -------------------------------------------------------------------------------------


class _Constant(Exception):
    """NaN, Infinity or -Infinity in the text."""


def _refuse_constant(name):
    raise _Constant(name)


class _Repeated(dict):
    """A JSON object that used a key twice; `key` is the first key it repeated."""

    __slots__ = ("key",)


def _pairs(pairs):
    """An object's pairs as a dict; a _Repeated one when a key came twice."""
    out, repeated = {}, None
    for key, value in pairs:
        if repeated is None and key in out:
            repeated = key
        out[key] = value
    if repeated is None:
        return out
    marked = _Repeated(out)
    marked.key = repeated
    return marked


def _first_repeat(doc):
    """(where, key) of the first object, in document order, that used a key twice, or None."""
    stack = [("$", doc)]
    while stack:
        where, value = stack.pop()
        if isinstance(value, _Repeated):
            return where, value.key
        if isinstance(value, dict):
            stack.extend((f"{where}.{key}", child) for key, child in reversed(value.items()))
        elif isinstance(value, list):
            stack.extend((f"{where}[{i}]", child) for i, child in reversed(list(enumerate(value))))
    return None


def _loads(data, source):
    """The file's bytes as a parsed document: strict UTF-8, strict JSON, no key used twice."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise _error("not_json", source, reason=NOT_JSON["utf8"].format(byte=e.start)) from None
    try:
        doc = json.loads(text, parse_constant=_refuse_constant, object_pairs_hook=_pairs)
    except json.JSONDecodeError as e:
        reason = NOT_JSON["syntax"].format(line=e.lineno, column=e.colno)
    except _Constant:
        reason = NOT_JSON["constant"]
    except RecursionError:
        reason = NOT_JSON["depth"]
    except ValueError:
        # The integer-digit limit: the decoder raises a plain ValueError for it.
        reason = NOT_JSON["number"]
    else:
        repeat = _first_repeat(doc)
        if repeat is not None:
            raise _error("schema", source, where=repeat[0], problem=f"key {repeat[1]!r} used twice")
        return doc
    raise _error("not_json", source, reason=reason)


def read_portfolio(roots, path) -> PortfolioInput:
    """The portfolio document at `path`, read under the run's `roots` and mapped."""
    source = str(path)
    try:
        data = read_bytes_confined(roots, path, key=ROOTS_KEY)
    except FileNotFoundError:
        raise _error("unreadable", source, "missing") from None
    except OutsideRoots:
        raise _error("unreadable", source, "outside") from None
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        raise _error("unreadable", source, reason=error_reason(e)) from None
    return map_document(_loads(data, source), source=source)


# --- the checks -------------------------------------------------------------------------------


class _Problem(Exception):
    def __init__(self, where, problem):
        super().__init__(where, problem)
        self.where, self.problem = where, problem


def _is_line(value):
    """An integer of 1 or more; a bool is not an integer."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_filled(value):
    return isinstance(value, str) and value != ""


def _need(ok, where, problem):
    if not ok:
        raise _Problem(where, problem)


def _check_node(node, where, ids, lines):
    _need(isinstance(node, dict), where, "not an object")
    node_id = node.get("id")
    _need(_is_filled(node_id), f"{where}.id", "not a non-empty string")
    _need(node_id not in ids, f"{where}.id", "used twice")
    ids.add(node_id)
    parent = node.get("parent")
    _need(parent is None or isinstance(parent, str), f"{where}.parent", "not a string or null")
    _need(isinstance(node.get("text"), str), f"{where}.text", "not a string")
    glyph = node.get("glyph")
    _need(isinstance(glyph, str) and glyph in STATE_GLYPHS, f"{where}.glyph", f"not one of {GLYPH_LIST}")
    _need(node.get("state") == STATE_GLYPHS[glyph], f"{where}.state", "does not match the glyph")
    line = node.get("source_line")
    _need(_is_line(line), f"{where}.source_line", "not an integer of 1 or more")
    _need(line not in lines, f"{where}.source_line", "used twice in this project")
    lines.add(line)
    issue = node.get("issue")
    _need(issue is None or _is_filled(issue), f"{where}.issue", "not a non-empty string or null")
    meta = node.get("meta")
    _need(isinstance(meta, dict), f"{where}.meta", "not an object")
    for key, value in meta.items():
        ok = isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value))
        _need(ok, f"{where}.meta.{key}", "not a string or a list of strings")


def _check_shapes(doc, source):
    """Pass 1: every field the reader uses has its shape, in document order."""
    _need(isinstance(doc, dict), "$", "not an object")
    _need(doc.get("schema") == SCHEMA_ID and isinstance(doc.get("schema"), str), "$.schema", f'not "{SCHEMA_ID}"')
    version = doc.get("schema_version")
    match = VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    _need(match is not None, "$.schema_version", "not MAJOR.MINOR")
    if int(match.group(1)) != MAJOR:
        raise _error("version", source, found=version)
    when = doc.get("generated_at")
    problem = "not an RFC 3339 UTC time"
    _need(isinstance(when, str) and TIME_RE.fullmatch(when) is not None, "$.generated_at", problem)
    try:
        generated = datetime.fromisoformat(when)
    except ValueError:
        raise _Problem("$.generated_at", problem) from None
    projects = doc.get("projects")
    _need(isinstance(projects, list), "$.projects", "missing or not a list")
    names, ids = set(), set()
    for i, project in enumerate(projects):
        where = f"$.projects[{i}]"
        _need(isinstance(project, dict), where, "not an object")
        name = project.get("project")
        _need(_is_filled(name), f"{where}.project", "not a non-empty string")
        _need(name not in names, f"{where}.project", "used twice")
        names.add(name)
        status = project.get("status")
        _need(isinstance(status, str) and status in STATUSES, f"{where}.status", "not one of " + ", ".join(STATUSES))
        read = status == "read"
        reason = project.get("status_reason")
        if read:
            _need(reason is None, f"{where}.status_reason", "not null on a read project")
        else:
            _need(_is_filled(reason), f"{where}.status_reason", "not a non-empty string")
        nodes = project.get("nodes")
        _need(isinstance(nodes, list), f"{where}.nodes", "not a list")
        _need(read or not nodes, f"{where}.nodes", "not empty on a project that is not read")
        lines = set()
        for j, node in enumerate(nodes):
            _check_node(node, f"{where}.nodes[{j}]", ids, lines)
    report = doc.get("parse_report")
    _need(isinstance(report, list), "$.parse_report", "not a list")
    for k, entry in enumerate(report):
        where = f"$.parse_report[{k}]"
        _need(isinstance(entry, dict), where, "not an object")
        _need(isinstance(entry.get("project"), str), f"{where}.project", "not a string")
        line = entry.get("source_line")
        _need(line is None or _is_line(line), f"{where}.source_line", "not an integer of 1 or more or null")
        _need(_is_filled(entry.get("kind")), f"{where}.kind", "not a non-empty string")
        _need(isinstance(entry.get("message"), str), f"{where}.message", "not a string")
    return generated


def _check_parents(doc):
    """Pass 2: every parent is a node of the same project on an earlier line,
    and no node nests deeper than MAX_DEPTH. A parent cycle always holds a
    link that is not on an earlier line, so a checked document has none."""
    owner, by_id = {}, {}
    for project in doc["projects"]:
        for node in project["nodes"]:
            owner[node["id"]] = project["project"]
            by_id[node["id"]] = node
    for i, project in enumerate(doc["projects"]):
        nodes = project["nodes"]
        depth = {}
        for j in sorted(range(len(nodes)), key=lambda j: nodes[j]["source_line"]):
            node = nodes[j]
            parent, where = node["parent"], f"$.projects[{i}].nodes[{j}].parent"
            if parent is None:
                depth[node["id"]] = 1
                continue
            _need(parent in owner, where, "names no node")
            _need(owner[parent] == project["project"], where, f"names a node of project {owner[parent]!r}")
            _need(by_id[parent]["source_line"] < node["source_line"], where, "is not on an earlier line")
            depth[node["id"]] = depth[parent] + 1
            _need(depth[node["id"]] <= MAX_DEPTH, where, f"nests deeper than {MAX_DEPTH} levels")


# --- nodes into leaves and rows ---------------------------------------------------------------


class _Node:
    """One node of a read project while it is named and built."""

    __slots__ = ("line", "glyph", "title", "meta", "issue", "parent", "outline", "children", "key")

    def __init__(self, raw, parent, outline):
        self.line, self.glyph, self.title = raw["source_line"], raw["glyph"], raw["text"]
        self.issue, self.parent, self.outline = raw["issue"], parent, outline
        # New containers: the document is never changed. Key order is the document's.
        self.meta = {key: [ParsedValue(v) for v in value] if isinstance(value, list) else ParsedValue(value)
                     for key, value in raw["meta"].items()}
        self.children = 0
        self.key = None


class _Site:
    """A read project's nodes and Gantry's own findings about them."""

    __slots__ = ("name", "nodes", "first", "leaves", "rows", "links", "held_out", "report")


def _scan(raw_nodes):
    """The nodes in line order, each with its parent and outline position.

    A top-level node counts from 1; a child is its parent's outline, a dot and
    its place among that parent's children. Node ids only find the parent and
    array order decides nothing: neither is stable across runs.
    """
    by_id, nodes, top_level = {}, [], 0
    for raw in sorted(raw_nodes, key=lambda raw: raw["source_line"]):
        parent = None if raw["parent"] is None else by_id[raw["parent"]]
        if parent is None:
            top_level += 1
            outline = str(top_level)
        else:
            parent.children += 1
            outline = f"{parent.outline}.{parent.children}"
        node = _Node(raw, parent, outline)
        by_id[raw["id"]] = node
        nodes.append(node)
    return nodes


def _name(site):
    """Key every node as gantry/wbs_stopgap.py does: the first holder of each
    issue by the issue, every other node by its outline position."""
    first = {}
    for node in site.nodes:
        if node.issue is not None and node.issue not in first:
            first[node.issue] = node
            node.key = node.issue
    for node in site.nodes:
        if node.key is not None:
            continue
        key = node.outline
        if key in first:
            other = first[key].line
            while key in first:
                key += f"~{node.line}"
            _report(site, node.line, "key_collision", outline=node.outline, other=other, key=key)
        node.key = key
    site.first = first


def _report(site, line, kind, **fields):
    site.report.append(ReportEntry(site.name, line, kind, MESSAGES[kind].format(line=line, **fields)))


def _top(node):
    while node.parent is not None:  # at most MAX_DEPTH steps: the links are checked
        node = node.parent
    return node


def _build(site):
    leaves, rows = [], []
    for node in site.nodes:
        state = STATE_GLYPHS[node.glyph]
        priority = node.meta.get("priority")
        if priority is not None and not is_valid(priority):
            _report(site, node.line, "bad_priority", raw=priority)
        if node.children:
            parent = node.parent.key if node.parent is not None else None
            rows.append(Row(node.key, node.issue, node.outline, node.title, node.glyph, state, node.line, parent,
                            node.meta))
            continue
        epic = _top(node).key if node.parent is not None else None
        leaves.append(Leaf(node.key, node.issue, node.outline, node.title, node.glyph, state,
                           node.glyph not in FINISHED_GLYPHS, priority, refs(node.meta, site.name), node.meta, epic,
                           node.line))
    site.leaves, site.rows = tuple(leaves), tuple(rows)


def _read_site(name, raw_nodes):
    """A read project's nodes, named and built, with Gantry's own report entries."""
    site = _Site()
    site.name, site.report = name, []
    site.nodes = _scan(raw_nodes)
    _name(site)
    _build(site)
    if not site.nodes:
        site.report.append(ReportEntry(name, None, "no_nodes", MESSAGES["no_nodes"]))
    by_issue = {issue: node.key for issue, node in site.first.items()}
    site.links, site.held_out, resolved = resolve(name, site.leaves, site.rows, by_issue)
    # devagent reports a local entry that names nothing (dep_unresolved); Gantry's copy is dropped.
    site.report += [entry for entry in resolved if entry.kind != "unresolved_ref"]
    return site


def _site_data(site):
    return SiteData(site.leaves, site.rows, site.links, site.held_out, tuple(sorted(site.report, key=report_order)))


def map_document(doc, *, source=DEFAULT_SOURCE) -> PortfolioInput:
    """A parsed portfolio document as a PortfolioInput. `doc` is never changed;
    `source` names it in an error."""
    try:
        generated = _check_shapes(doc, source)
        _check_parents(doc)
    except _Problem as e:
        raise _error("schema", source, where=e.where, problem=e.problem) from None
    projects = []
    for raw in doc["projects"]:
        name, status, reason = raw["project"], raw["status"], raw["status_reason"]
        if status == "read":
            data = _site_data(_read_site(name, raw["nodes"]))
        else:
            report = (ReportEntry(name, None, "site_unreadable", reason),) if status == "unreadable" else ()
            data = SiteData((), (), (), (), report)
        projects.append(ProjectInput(name, status, reason, data))
    shown = tuple(DevagentEntry(e["project"], e["source_line"], e["kind"], e["message"]) for e in doc["parse_report"])
    return PortfolioInput(tuple(projects), shown, generated, doc["schema_version"])
