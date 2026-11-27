"""devagent's portfolio JSON, read into Gantry's site model (#83).

devagent writes one JSON document over every configured project's WBS.md
(its `scripts/portfolio.sh`, schema 1.x). This module reads that document:

    read_portfolio(roots, path) -> PortfolioInput
    map_document(doc) -> PortfolioInput

read_portfolio loads the file under the run's roots and checks it;
map_document does the same checks and mapping on a document that is already
parsed. `gantry build` reads every site through read_portfolio, once per run
(gantry/build.py, #12), and the test builder (tests/helpers_portfolio.py, #84)
through map_document: it is the one WBS.md reader Gantry has. `tag(value)`
gives one meta value the parsed shape the reader stores, for a test that
builds such a leaf by hand.

The result's `actuals` part (#213) holds what the reader keeps of schema 1.3's
actuals, and devagent's actuals_unavailable entries, which it consumes: they
reach no page. No page uses the part yet; #25 is its first caller.

Neither entry point raises anything but PortfolioLoadError, whatever the
file or the document holds: every walk here is a loop, never a recursion, and
no parent link is followed before it has been checked.
docs/worksite-page.md states the rules.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

from gantry.config import error_reason
from gantry.deps import MESSAGES as DEPS_MESSAGES
from gantry.deps import classify, find_cycles, refs, resolve
from gantry.est_rules import classify_est
from gantry.model import (
    FINISHED_GLYPHS,
    STATE_GLYPHS,
    Leaf,
    Models,
    ParsedValue,
    ReportEntry,
    Row,
    SiteData,
    meta_values,
    report_order,
)
from gantry.priority import is_valid
from gantry.roots import OutsideRoots, read_bytes_confined

SCHEMA_ID = "devagent-portfolio"
# The schema the pinned devagent release writes (tests/devagent.pin). Another
# major is refused; any minor of this major is read, and a field the reader
# does not use is ignored. A leaf's `models` is checked and read only from the
# minor that added it: at a lower one it is unknown. The actuals (#213) are
# read by their own key, a top-level `actuals`, whatever the minor.
MAJOR, MINOR = 1, 2
# The minor that added each leaf's `models` (#14).
MODELS_MINOR = 2
# The classes of a leaf's `models`, in the document's order (not render's tier names).
MODEL_KEYS = ("thinking", "checking")
# The JSON sources a leaf's tiers are read from: a leaf with no `wbs` class has none (#14).
TIER_SOURCES = ("wbs", "project")
MAX_DEPTH = 64  # the deepest a node may nest
# The deepest the JSON text may nest, counted before decoding: since Python 3.14
# the decoder's own limit is the C stack, so its verdict differs by host and
# version. A portfolio document nests about 7 deep; every decoder handles 100.
JSON_DEPTH = 100
MAX_LINE = 999_999_999
STATUSES = ("read", "no_wbs", "no_devdoc_dir", "unreadable")
# Bounded digit groups: int() never sees a long run.
VERSION_RE = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)")
# The actuals file's own time, as devagent writes it: YYYY-MM-DDTHH:MM:SSZ (used with fullmatch).
ACTUALS_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z")
ACTUALS_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

ROOTS_KEY = "portfolio_json"
DEFAULT_SOURCE = "the portfolio document"
LOAD_MESSAGES = {
    "missing": "{source}: devagent's portfolio JSON does not exist. Run devagent's aggregator first "
               "(/devagent:portfolio).",
    "unreadable": "{source}: cannot read devagent's portfolio JSON: {reason}",
    # The config key portfolio_json (#12) names the document, so it is a root of every build.
    "outside": "{source}: devagent's portfolio JSON is outside the configured roots",
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
# Gantry's own texts for the kinds it reports itself (first written by the stopgap parser #12 deleted).
MESSAGES = {
    "key_collision": "WBS.md line {line}: outline position {outline!r} is the issue: id on "
                     "line {other}; this node is named {key!r}",
    "bad_priority": "WBS.md line {line}: priority {raw!r} is not P0, P1, P2, P3 or P4; it counts as P3",
    "no_nodes": "WBS.md has no node lines",
    "no_issue": "WBS.md line {line}: leaf has no issue: id; it is named {key!r}",
    "duplicate_issue": "WBS.md line {line}: issue {issue!r} is also on line {first}; "
                       "references resolve to line {first}, and this node is named {key!r}",
}

# devagent's report entries (its `parse_report`). Each gets one disposition:
# mapped onto a Gantry kind, suppressed because a Gantry pass reports the same
# thing, shown as devagent wrote it, or consumed: kept in the result's actuals
# part and shown nowhere. MAPPED is devagent's kind -> Gantry's.
MAPPED = {"leaf_no_issue": "no_issue", "duplicate_issue": "duplicate_issue", "near_miss_line": "skipped_line",
          "dep_unresolved": "unresolved_ref"}
# Suppressed on every node that holds the key: gantry.attention.check_h reports each.
SUPPRESSED_ALWAYS = ("h_malformed",)
# Suppressed only where the rule in _dispose finds Gantry's own entry or check.
SUPPRESSED_BY_RULE = ("dep_unresolved", "est_unparseable", "dep_cycle", "parser_warning")
SUPPRESSED, SHOWN = "suppressed", "shown"
# Consumed in every document, whatever the entry's project, line or status (#213): the message
# starts with the absolute path of devagent's actuals.json, which is no configured root.
CONSUMED = ("actuals_unavailable",)
# A mapped near miss: Gantry's own reason needs the line's text, which the document does not carry.
SKIPPED_LINE = "WBS.md line {line}: {message}"
# One member of devagent's dep_cycle message: `<issue> (line N)`, with `<project>:` before another project's.
CYCLE_MEMBER_RE = re.compile(r"(?:^|, |: )(?:(?P<project>[A-Za-z0-9][A-Za-z0-9_.-]*):)?(?P<key>[^ ,()]+) "
                             r"\(line (?P<line>[0-9]{1,9})\)")
# The key a parser warning is about, in the forms devagent's parser words them (used with match).
WARNED_KEY_RE = re.compile(r"(?:line [0-9]{1,9}: )?(priority|depends_on|satisfies|objective|h|thinking|checking) ")


class PortfolioLoadError(Exception):
    """Why the document cannot be read. `kind` is "unreadable" (missing,
    outside the roots, or any other failed read), "not_json", "version" or
    "schema"; `source` is the path as given, or map_document's `source`."""

    def __init__(self, kind, source, message):
        super().__init__(message)
        self.kind, self.source = kind, source


@dataclass(frozen=True)
class DevagentEntry:
    """One entry of devagent's parse report, as devagent wrote it: one Gantry
    shows (PortfolioInput.devagent_report) or one it consumes (ActualsInput.report)."""
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
class ActualsInput:
    """The actuals part of a document (schema 1.3, #213). No page uses it yet:
    #25 is its first caller."""
    wired: bool  # the document has a top-level `actuals` key: devagent built it with an actuals file
    generated_at: datetime | None  # the actuals file's time, aware, UTC; None when that object is null
    # {(project, issue): IssueActuals}, read-only: the slots that are not null, in document order.
    issues: MappingProxyType
    report: tuple[DevagentEntry, ...]  # the consumed entries (CONSUMED), in document order


NO_ACTUALS = ActualsInput(False, None, MappingProxyType({}), ())


@dataclass(frozen=True)
class PortfolioInput:
    projects: tuple[ProjectInput, ...]  # in document order
    devagent_report: tuple[DevagentEntry, ...]  # the shown entries, in document order
    generated_at: datetime  # aware, UTC
    schema_version: str  # as written
    actuals: ActualsInput = NO_ACTUALS  # empty and not wired unless the reader fills it


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


# A string (to its closing quote, or to the end when unterminated) or a bracket.
_JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"?|[\[\]{}]')


def _too_deep(text):
    """Whether the text nests deeper than JSON_DEPTH; brackets inside strings do not count."""
    depth = 0
    for token in _JSON_TOKEN.finditer(text):
        bracket = token.group()
        if bracket in ("[", "{"):
            depth += 1
            if depth > JSON_DEPTH:
                return True
        elif bracket in ("]", "}"):
            depth -= 1
    return False


def _loads(data, source):
    """The file's bytes as a parsed document: strict UTF-8, strict JSON, no key used twice."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise _error("not_json", source, reason=NOT_JSON["utf8"].format(byte=e.start)) from None
    repeated = []

    def pairs(items):
        out = _pairs(items)
        if isinstance(out, _Repeated):
            repeated.append(out)
        return out

    if _too_deep(text):
        raise _error("not_json", source, reason=NOT_JSON["depth"])
    try:
        doc = json.loads(text, parse_constant=_refuse_constant, object_pairs_hook=pairs)
    except json.JSONDecodeError as e:
        reason = NOT_JSON["syntax"].format(line=e.lineno, column=e.colno)
    except _Constant:
        reason = NOT_JSON["constant"]
    except RecursionError:  # not reached past _too_deep; kept so a stack overflow is never a crash
        reason = NOT_JSON["depth"]
    except ValueError:
        # The integer-digit limit: the decoder raises a plain ValueError for it.
        reason = NOT_JSON["number"]
    else:
        if repeated:  # the walk finds the first in document order, which the parse order is not
            where, key = _first_repeat(doc)
            raise _error("schema", source, where=where, problem=f"key {key!r} used twice")
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


# A nullable key's default when it is absent: it fails every type check, so a
# missing key is refused as a wrong type, never read as null.
_ABSENT = object()


class _Problem(Exception):
    def __init__(self, where, problem):
        super().__init__(where, problem)
        self.where, self.problem = where, problem


def _is_line(value):
    """An integer from 1 to 999,999,999, as many digits as the message patterns
    read; a bool is not an integer. A larger one is too long to format."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_LINE


def _is_filled(value):
    return isinstance(value, str) and value != ""


def _need(ok, where, problem):
    if not ok:
        raise _Problem(where, problem)


def _check_models(models, where):
    """A node's `models`: an object whose classes are each null or {value, source}.

    Extra keys are ignored; a value is never checked against devagent's model
    tokens, nor a source against a list (#14)."""
    _need(isinstance(models, dict), where, "not an object")
    for cls in MODEL_KEYS:
        choice = models.get(cls, _ABSENT)
        _need(choice is None or isinstance(choice, dict), f"{where}.{cls}", "not an object or null")
        if choice is not None:
            _need(_is_filled(choice.get("value")), f"{where}.{cls}.value", "not a non-empty string")
            _need(isinstance(choice.get("source"), str), f"{where}.{cls}.source", "not a string")


def _check_node(node, where, ids, lines, models_ok):
    _need(isinstance(node, dict), where, "not an object")
    node_id = node.get("id")
    _need(_is_filled(node_id), f"{where}.id", "not a non-empty string")
    _need(node_id not in ids, f"{where}.id", "used twice")
    ids.add(node_id)
    parent = node.get("parent", _ABSENT)
    _need(parent is None or isinstance(parent, str), f"{where}.parent", "not a string or null")
    _need(isinstance(node.get("text"), str), f"{where}.text", "not a string")
    glyph = node.get("glyph")
    _need(isinstance(glyph, str) and glyph in STATE_GLYPHS, f"{where}.glyph", f"not one of {GLYPH_LIST}")
    _need(node.get("state") == STATE_GLYPHS[glyph], f"{where}.state", "does not match the glyph")
    line = node.get("source_line")
    _need(_is_line(line), f"{where}.source_line", "not an integer from 1 to 999999999")
    _need(line not in lines, f"{where}.source_line", "used twice in this project")
    lines.add(line)
    issue = node.get("issue", _ABSENT)
    _need(issue is None or _is_filled(issue), f"{where}.issue", "not a non-empty string or null")
    meta = node.get("meta")
    _need(isinstance(meta, dict), f"{where}.meta", "not an object")
    for key, value in meta.items():
        ok = isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value))
        _need(ok, f"{where}.meta.{key}", "not a string or a list of strings")
    if models_ok and "models" in node:
        _check_models(node["models"], f"{where}.models")


def _check_shapes(doc, source):
    """Pass 1: every field the reader uses has its shape, in document order.
    (generated_at, whether the minor carries leaf `models`, whether the
    document is wired, the actuals file's time or None)."""
    _need(isinstance(doc, dict), "$", "not an object")
    _need(doc.get("schema") == SCHEMA_ID and isinstance(doc.get("schema"), str), "$.schema", f'not "{SCHEMA_ID}"')
    version = doc.get("schema_version")
    match = VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    _need(match is not None, "$.schema_version", "not MAJOR.MINOR")
    if int(match.group(1)) != MAJOR:
        raise _error("version", source, found=version)
    models_ok = int(match.group(2)) >= MODELS_MINOR
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
        reason = project.get("status_reason", _ABSENT)
        if read:
            _need(reason is None, f"{where}.status_reason", "not null on a read project")
        else:
            _need(_is_filled(reason), f"{where}.status_reason", "not a non-empty string")
        nodes = project.get("nodes")
        _need(isinstance(nodes, list), f"{where}.nodes", "not a list")
        _need(read or not nodes, f"{where}.nodes", "not empty on a project that is not read")
        lines = set()
        for j, node in enumerate(nodes):
            _check_node(node, f"{where}.nodes[{j}]", ids, lines, models_ok)
    report = doc.get("parse_report")
    _need(isinstance(report, list), "$.parse_report", "not a list")
    for k, entry in enumerate(report):
        where = f"$.parse_report[{k}]"
        _need(isinstance(entry, dict), where, "not an object")
        _need(isinstance(entry.get("project"), str), f"{where}.project", "not a string")
        line = entry.get("source_line", _ABSENT)
        _need(line is None or _is_line(line), f"{where}.source_line", "not an integer from 1 to 999999999 or null")
        _need(_is_filled(entry.get("kind")), f"{where}.kind", "not a non-empty string")
        _need(isinstance(entry.get("message"), str), f"{where}.message", "not a string")
    wired = "actuals" in doc
    return generated, models_ok, wired, _check_actuals_time(doc["actuals"]) if wired else None


def _check_actuals_time(top):
    """A wired document's top-level `actuals`: null (devagent found no usable
    actuals file), or an object whose `generated_at` is a real
    YYYY-MM-DDTHH:MM:SSZ. The time, aware and UTC, or None for null."""
    _need(top is None or isinstance(top, dict), "$.actuals", "not an object or null")
    if top is None:
        return None
    when, problem = top.get("generated_at"), "not YYYY-MM-DDTHH:MM:SSZ"
    _need(isinstance(when, str) and ACTUALS_TIME_RE.fullmatch(when) is not None, "$.actuals.generated_at", problem)
    try:
        return datetime.strptime(when, ACTUALS_TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise _Problem("$.actuals.generated_at", problem) from None


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


def tag(value):
    """A node's meta value in its parsed shape: a ParsedValue, or a new list of them."""
    return [ParsedValue(v) for v in value] if isinstance(value, list) else ParsedValue(value)


class _Node:
    """One node of a read project while it is named and built."""

    __slots__ = ("line", "glyph", "title", "meta", "issue", "parent", "outline", "children", "key", "dep_index",
                 "models")

    def __init__(self, raw, parent, outline):
        self.line, self.glyph, self.title = raw["source_line"], raw["glyph"], raw["text"]
        self.models = raw.get("models")
        self.issue, self.parent, self.outline = raw["issue"], parent, outline
        # New containers: the document is never changed. Key order is the document's.
        self.meta = {key: tag(value) for key, value in raw["meta"].items()}
        self.children = 0
        self.key = None
        self.dep_index = None


class _Site:
    """A read project's nodes and Gantry's own findings about them."""

    __slots__ = ("name", "nodes", "by_line", "first", "leaves", "rows", "links", "held_out", "report", "cycles",
                 "dep_rank", "models_ok", "bad_models")


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
    """Key every node by the identity rule (D2): the first holder of each issue
    by the issue, every other node by its outline position."""
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


def _entry(site, line, kind, **fields):
    return ReportEntry(site.name, line, kind, MESSAGES[kind].format(line=line, **fields))


def _report(site, line, kind, **fields):
    site.report.append(_entry(site, line, kind, **fields))


def _top(node):
    while node.parent is not None:  # at most MAX_DEPTH steps: the links are checked
        node = node.parent
    return node


def _leaf_models(raw):
    """A leaf's tiers from its `models` (#14): None unless a class's source is
    "wbs". Then a "wbs" or "project" class gives its value and source, and any
    other source, or null, gives None for both. Values are kept as written."""
    if raw is None:
        return None
    choices = [raw[cls] for cls in MODEL_KEYS]
    if not any(c is not None and c["source"] == "wbs" for c in choices):
        return None
    kept = [c if c is not None and c["source"] in TIER_SOURCES else None for c in choices]
    values = [None if c is None else c["value"] for c in kept]
    return Models(values[0], values[1], "wbs", tuple(None if c is None else c["source"] for c in kept))


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
                           node.line, models=_leaf_models(node.models) if site.models_ok else None))
    site.leaves, site.rows = tuple(leaves), tuple(rows)


def _read_site(name, raw_nodes, models_ok):
    """A read project's nodes, named and built, with Gantry's own report entries.
    `models_ok`: whether the document's minor carries leaf `models`."""
    site = _Site()
    site.name, site.report, site.cycles, site.dep_rank, site.models_ok = name, [], None, {}, models_ok
    site.bad_models = set()  # (line, key) of each leaf bad_model entry, filled by map_document
    site.nodes = _scan(raw_nodes)
    site.by_line = {node.line: node for node in site.nodes}
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
    # A mapped unresolved_ref keeps its place in the node's depends_on, as Gantry's
    # own reader orders them, not the order devagent's report gives them.
    report = sorted(site.report, key=lambda entry: (report_order(entry), site.dep_rank.get(entry, 0)))
    return SiteData(site.leaves, site.rows, site.links, site.held_out, tuple(report), site.models_ok)


# --- devagent's report entries ----------------------------------------------------------------


def _cycle_lines(site):
    """The member lines, sorted, of each cycle Gantry finds among the site's open leaves."""
    if site.cycles is None:
        line_of = {leaf.key: leaf.line for leaf in site.leaves}
        keys = [leaf.key for leaf in site.leaves if leaf.open]
        site.cycles = [sorted(line_of[key] for key in cycle) for cycle in find_cycles(keys, site.links)]
    return site.cycles


def _dep_index(node):
    """{depends_on entry: its first place}, built once per node."""
    if node.dep_index is None:
        node.dep_index = {}
        for i, entry in enumerate(meta_values(node.meta.get("depends_on"))):
            node.dep_index.setdefault(entry, i)
    return node.dep_index


DEP_PREFIX = "depends_on '"


def _named_entry(node, message):
    """The depends_on entry a dep_unresolved message is about: the longest one
    it opens with (`depends_on '<entry>' ...`). Each `' ` in the message, from
    the right, ends one candidate, so the cost follows the message, not the
    node's entries."""
    if not message.startswith(DEP_PREFIX):
        return None
    start, index, end = len(DEP_PREFIX), _dep_index(node), len(message)
    while (end := message.rfind("' ", start, end + 1)) != -1:
        if message[start:end] in index:
            return message[start:end]
    return None


def _dispose(site, entry):
    """One devagent report entry's disposition: Gantry's ReportEntry when
    mapped, else SUPPRESSED or SHOWN.

    `site` is the entry's project when the document holds it and it was read,
    else None. A CONSUMED kind never comes here: map_document keeps it in the
    actuals part. Every other entry leaves here as exactly one of the three, and
    nothing is suppressed unless a Gantry pass
    reports the same thing about the same node: gantry.deps (cycle,
    malformed_ref), gantry.crosssite (the foreign kinds), gantry.inputs
    (est_unparseable), gantry.attention (bad_h), gantry.trace (satisfies,
    objective) or this module (bad_priority). The one exception is a leaf's
    thinking or checking warning: devagent's own bad_model entry for that key
    on that line covers it, and that entry is shown (#14).
    """
    kind, line, message = entry["kind"], entry["source_line"], entry["message"]
    named = kind in MAPPED or kind in SUPPRESSED_ALWAYS or kind in SUPPRESSED_BY_RULE
    if site is None or line is None or not named:
        return SHOWN
    node = site.by_line.get(line)
    if kind == "near_miss_line":
        # A near miss is not a node: one on a node's line is not what Gantry would report.
        if node is None:
            return ReportEntry(site.name, line, MAPPED[kind], SKIPPED_LINE.format(line=line, message=message))
        return SHOWN
    if node is None:
        return SHOWN
    is_leaf = not node.children
    is_open = node.glyph not in FINISHED_GLYPHS
    if kind == "leaf_no_issue":
        if is_leaf and node.issue is None:
            return _entry(site, line, MAPPED[kind], key=node.key)
    elif kind == "duplicate_issue":
        if node.issue is not None and site.first[node.issue] is not node:
            return _entry(site, line, MAPPED[kind], issue=node.issue, first=site.first[node.issue].line,
                          key=node.key)
    elif kind == "dep_unresolved":
        about = _named_entry(node, message)
        if about is not None:
            ref = classify(about, site.name)
            if ref.kind != "local":
                return SUPPRESSED  # foreign or malformed: Gantry classifies and reports those itself
            if ref.issue not in site.first:
                mapped = ReportEntry(site.name, line, MAPPED[kind], DEPS_MESSAGES["unresolved_ref"].format(
                    line=line, entry=about))
                site.dep_rank[mapped] = _dep_index(node)[about]
                return mapped
    elif kind == "est_unparseable":
        if is_leaf and is_open and classify_est(node.meta.get("est")).kind == "unparseable":
            return SUPPRESSED
    elif kind in SUPPRESSED_ALWAYS:
        if "h" in node.meta:
            return SUPPRESSED
    elif kind == "dep_cycle":
        members = list(CYCLE_MEMBER_RE.finditer(message))
        # A member of another project makes it a cross-project cycle, which is no cycle of this file.
        if members and not any(m.group("project") for m in members):
            if sorted(int(m.group("line")) for m in members) in _cycle_lines(site):
                return SUPPRESSED
    elif kind == "parser_warning":
        # A defence: no devagent release words a warning about these keys today. A leaf's
        # thinking or checking warning goes only beside devagent's bad_model for that key on that
        # line (#14), which is shown; a row's model key is reported nowhere at the pin
        # (mtibbits/devagent#1236), so its warning is shown.
        match = WARNED_KEY_RE.match(message)
        key = match.group(1) if match else None
        if key in node.meta and (key in ("priority", "h") or (key == "depends_on" and is_leaf and is_open)
                                 or (key in ("satisfies", "objective") and is_leaf)
                                 or (key in MODEL_KEYS and is_leaf and (line, key) in site.bad_models)):
            return SUPPRESSED
    return SHOWN


def map_document(doc, *, source=DEFAULT_SOURCE) -> PortfolioInput:
    """A parsed portfolio document as a PortfolioInput. `doc` is never changed;
    `source` names it in an error."""
    try:
        generated, models_ok, wired, actuals_at = _check_shapes(doc, source)
        _check_parents(doc)
    except _Problem as e:
        raise _error("schema", source, where=e.where, problem=e.problem) from None
    sites = {raw["project"]: _read_site(raw["project"], raw["nodes"], models_ok)
             for raw in doc["projects"] if raw["status"] == "read"}
    for entry in doc["parse_report"]:
        site = sites.get(entry["project"])
        if site is not None and entry["kind"] == "bad_model" and entry["source_line"] is not None:
            site.bad_models.add((entry["source_line"], entry["message"].split(" ", 1)[0]))
    shown, consumed = [], []
    for entry in doc["parse_report"]:
        if entry["kind"] in CONSUMED:
            consumed.append(DevagentEntry(entry["project"], entry["source_line"], entry["kind"], entry["message"]))
            continue
        site = sites.get(entry["project"])
        disposition = _dispose(site, entry)
        if isinstance(disposition, ReportEntry):
            site.report.append(disposition)
        elif disposition == SHOWN:
            shown.append(DevagentEntry(entry["project"], entry["source_line"], entry["kind"], entry["message"]))
    projects = []
    for raw in doc["projects"]:
        name, status, reason = raw["project"], raw["status"], raw["status_reason"]
        if status == "read":
            data = _site_data(sites[name])
        else:
            # devagent's reason, as written.
            report = (ReportEntry(name, None, "site_unreadable", reason),) if status == "unreadable" else ()
            data = SiteData((), (), (), (), report, models_ok)
        projects.append(ProjectInput(name, status, reason, data))
    actuals = ActualsInput(wired, actuals_at, MappingProxyType({}), tuple(consumed))
    return PortfolioInput(tuple(projects), tuple(shown), generated, doc["schema_version"], actuals)
