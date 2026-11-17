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
from gantry.model import STATE_GLYPHS
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


class PortfolioLoadError(Exception):
    """Why the document cannot be read. `kind` is "unreadable" (missing,
    outside the roots, or any other failed read), "not_json", "version" or
    "schema"; `source` is the path as given, or map_document's `source`."""

    def __init__(self, kind, source, message):
        super().__init__(message)
        self.kind, self.source = kind, source


@dataclass(frozen=True)
class PortfolioInput:
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


def map_document(doc, *, source=DEFAULT_SOURCE) -> PortfolioInput:
    """A parsed portfolio document as a PortfolioInput. `doc` is never changed;
    `source` names it in an error."""
    try:
        generated = _check_shapes(doc, source)
        _check_parents(doc)
    except _Problem as e:
        raise _error("schema", source, where=e.where, problem=e.problem) from None
    return PortfolioInput(generated_at=generated, schema_version=doc["schema_version"])
