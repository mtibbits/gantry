"""devagent's portfolio JSON as test data (#83, #84): the committed fixtures, the
gated sample document, and the builder the unit tests read site data through.

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). tools/sample_portfolio.py makes the
committed documents with the pinned devagent release's own aggregator.

The builder (#84) writes the fields Gantry's reader (map_document) checks, in
devagent's shapes; it is not a complete devagent document (devagent's own
checker wants wbs_title, issues, rollup, depth, leaf, ready and more). node()
describes one node, project() numbers a tree of them, document() wraps
projects with the report entries devagent always writes (auto_report), and
site() maps one project to SiteData. Fixture-backed sites are pinned to
devagent by test_site_builder; an inline node() is not, so it must state a
reading devagent gives (a str, or a list for bracket text). fixture_doc() narrows written.json to one project. A
test reaches site data only through site_of(), the one caller of
map_document here. Callers import this module as `hp` and never take names
from it: conftest's `node` fixture would clash.
"""

import copy
import functools
from dataclasses import dataclass
from pathlib import Path

import helpers_devagent as hd
import sample_portfolio as sp
from gantry import deps
from gantry.model import STATE_GLYPHS
from gantry.portfolio_json import map_document
from helpers_config import copy_samples

DOC_NAME = "portfolio.json"


written_doc = sp.written_doc
sample_doc = sp.sample_doc


def build_sample_document(tmp, env=None, os_name=None):
    """(the document's path, the samples copy): devagent's portfolio JSON over a
    copy of samples/ under `tmp`, written by the checkout the gate admits.

    The gate comes first (tests/helpers_devagent.py: skip, warn or fail), so
    nothing is copied without a checkout.
    """
    checkout = hd.require_devagent(env, os_name)
    copy = copy_samples(Path(tmp))
    return sp.make_sample(checkout, copy), copy


def read_doc(tmp_path, doc):
    """read_portfolio over `doc` written as <tmp_path>/portfolio.json, with
    tmp_path the only root. `doc` is a parsed document, written in devagent's
    own form, or bytes, written as they are."""
    from gantry.portfolio_json import read_portfolio
    from gantry.roots import directory_roots

    path = Path(tmp_path) / DOC_NAME
    path.write_bytes(doc if isinstance(doc, bytes) else sp.dumps(doc))
    return read_portfolio(directory_roots([tmp_path]), path)


# --- the builder (#84) ------------------------------------------------------------------------


def node(text, *children, glyph=" ", line=None, **meta) -> dict:
    """One node: `meta` in written order, each value a str or a list of str as
    devagent parses them (quotes stripped, a bracket list a list). A node with
    children is a row. `line` pins its source line; else it is the previous
    node's plus one."""
    for key, value in meta.items():
        ok = isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value))
        if not ok:
            raise TypeError(f"node {text!r}: meta {key!r} is not a str or a list of str")
    return {"text": text, "glyph": glyph, "line": line, "meta": dict(meta), "children": list(children)}


def flatten(nodes) -> list[tuple[dict, dict | None]]:
    """(node, parent node or None) for `nodes` and their children, in pre-order."""
    out, stack = [], [(n, None) for n in reversed(nodes)]
    while stack:
        item, parent = stack.pop()
        out.append((item, parent))
        stack.extend((child, item) for child in reversed(item["children"]))
    return out


def project(name, *nodes, start=1) -> dict:
    """A read project of `nodes`, numbered in pre-order from `start`; ids are `<name>-<line>`."""
    made, ids, previous = [], {}, None
    for item, parent in flatten(nodes):
        line = item["line"] if item["line"] is not None else (start if previous is None else previous + 1)
        if previous is not None and line <= previous:
            raise ValueError(f"node {item['text']!r} is on line {line}, not after line {previous}")
        previous = line
        ids[id(item)] = f"{name}-{line}"
        issue = item["meta"].get("issue")
        made.append({"id": ids[id(item)], "parent": None if parent is None else ids[id(parent)],
                     "text": item["text"], "state": STATE_GLYPHS[item["glyph"]], "glyph": item["glyph"],
                     "source_line": line, "issue": issue if isinstance(issue, str) else None,
                     "meta": copy.deepcopy(item["meta"])})
    return {"project": name, "status": "read", "status_reason": None, "nodes": made}


def _local_deps(node, project_name):
    """(entry, ref) for each depends_on entry of `node` that `project_name` calls local."""
    value = node["meta"].get("depends_on")
    entries = [value] if isinstance(value, str) and value else value if isinstance(value, list) else []
    for entry in entries:
        ref = deps.classify(entry, project_name)
        if ref.kind == "local":
            yield entry, ref


def auto_report(project) -> list[dict]:
    """The report entries devagent writes of every project and Gantry maps, in line order:
    a leaf with no issue key, an issue repeated from an earlier line, and a local
    depends_on entry that names no issue of the project."""
    name = project["project"]
    nodes = sorted(project["nodes"], key=lambda n: n["source_line"])
    parents = {n["parent"] for n in nodes}
    held = {n["issue"] for n in nodes if isinstance(n["issue"], str)}
    first, report = {}, []

    def add(line, kind, message):
        report.append({"project": name, "source_line": line, "kind": kind, "message": message})

    for n in nodes:
        line, issue = n["source_line"], n["issue"]
        if n["id"] not in parents and "issue" not in n["meta"]:
            add(line, "leaf_no_issue", f"leaf '{n['text']}' has no issue")
        if isinstance(issue, str):
            if issue in first:
                add(line, "duplicate_issue", f"issue '{issue}' also appears at line {first[issue]}")
            else:
                first[issue] = line
        for entry, ref in _local_deps(n, name):
            if ref.issue not in held:
                add(line, "dep_unresolved", f"depends_on '{entry}' matches no issue in this project")
    return report


def document(*projects, report=()) -> dict:
    """A portfolio document over `projects`: auto_report of each in order, then
    `report`'s (project, line, kind, message) entries."""
    entries = [entry for p in projects for entry in auto_report(p)]
    entries += [{"project": p, "source_line": line, "kind": kind, "message": message}
                for p, line, kind, message in report]
    return {"schema": "devagent-portfolio", "schema_version": "1.1", "generated_at": sp.FIXED_TIME,
            "projects": list(projects), "parse_report": entries}


def site_of(doc, name=None):
    """SiteData of the project `name` in `doc`, or of its only project; ValueError when it is not read."""
    projects = map_document(doc).projects
    if name is None:
        if len(projects) != 1:
            raise ValueError(f"the document holds {len(projects)} projects; name one")
        (found,) = projects
    else:
        found = next((p for p in projects if p.name == name), None)
        if found is None:
            raise ValueError(f"the document holds no project {name!r}")
    if found.status != "read":
        raise ValueError(f"project {found.name!r} is {found.status}, not read")
    return found.data


def site(*nodes, name="alpha", start=1):
    """SiteData of one project of `nodes`."""
    return site_of(document(project(name, *nodes, start=start)))


def narrow(doc, name, site=None) -> dict:
    """A fresh copy of `doc` holding only the project `name` and its report entries, renamed `site`."""
    out = {key: value for key, value in doc.items() if key not in ("projects", "parse_report")}
    out["projects"] = copy.deepcopy([p for p in doc["projects"] if p["project"] == name])
    if not out["projects"]:
        raise ValueError(f"the document holds no project {name!r}")
    out["parse_report"] = copy.deepcopy([e for e in doc["parse_report"] if e["project"] == name])
    if site is not None:
        for item in (*out["projects"], *out["parse_report"]):
            item["project"] = site
    return out


@functools.cache
def _parsed(path) -> dict:
    """`path` parsed once per session, keyed by path (a test may point sp.WRITTEN elsewhere); read-only."""
    return sp._load(path)


def fixture_doc(name, site="alpha") -> dict:
    """written.json's project `name` alone, renamed `site`."""
    return narrow(_parsed(sp.WRITTEN), name, site)


def fixture_site(name, site="alpha"):
    return site_of(fixture_doc(name, site))


def drop_lines(doc, *lines) -> dict:
    """A copy of a one-project `doc` without the leaves on `lines` and their report
    entries. A line holding a row or no node raises ValueError, and so does a
    remaining depends_on entry that names a dropped leaf's issue."""
    out = copy.deepcopy(doc)
    (proj,) = out["projects"]
    by_line = {n["source_line"]: n for n in proj["nodes"]}
    parents = {n["parent"] for n in proj["nodes"]}
    for line in lines:
        if line not in by_line:
            raise ValueError(f"no node on line {line}")
        if by_line[line]["id"] in parents:
            raise ValueError(f"line {line} holds a row")
    proj["nodes"] = [n for n in proj["nodes"] if n["source_line"] not in lines]
    dropped = {by_line[line]["issue"] for line in lines} - {n["issue"] for n in proj["nodes"]} - {None}
    for n in proj["nodes"]:
        for _, ref in _local_deps(n, proj["project"]):
            if ref.issue in dropped:
                raise ValueError(f"line {n['source_line']} depends on {ref.issue}, which is dropped")
    out["parse_report"] = [e for e in out["parse_report"]
                           if e["project"] != proj["project"] or e["source_line"] not in lines]
    return out


AUTO_KINDS = ("leaf_no_issue", "duplicate_issue", "dep_unresolved")
LOCAL_UNRESOLVED = ("depends_on '", "' matches no issue in this project")


def auto_entry(entry, project_name):
    """True for an entry of `project_name` of a kind auto_report writes.

    A local dep_unresolved has devagent's local wording and names an entry the
    project calls local. A malformed entry shares that wording, and a foreign one
    is worded apart but can read as local once fixture_doc renames its project."""
    if entry["project"] != project_name or entry["kind"] not in AUTO_KINDS:
        return False
    if entry["kind"] != "dep_unresolved":
        return True
    head, tail = LOCAL_UNRESOLVED
    message = entry["message"]
    if not (message.startswith(head) and message.endswith(tail)):
        return False
    return deps.classify(message[len(head):-len(tail)], project_name).kind == "local"


def append_nodes(doc, project_name, *nodes) -> dict:
    """A copy of `doc` with `nodes` added after the last line of `project_name`.

    The new nodes get the document's next free `n<N>` ids; the project's
    entries of the kinds auto_report writes are written again, and every other
    entry (a foreign or malformed dep_unresolved among them) is kept."""
    out = copy.deepcopy(doc)
    proj = next((p for p in out["projects"] if p["project"] == project_name), None)
    if proj is None:
        raise ValueError(f"the document holds no project {project_name!r}")
    last = max((n["source_line"] for n in proj["nodes"]), default=0)
    used = [int(n["id"][1:]) for p in out["projects"] for n in p["nodes"] if n["id"][1:].isdigit()]
    added = project(project_name, *nodes, start=last + 1)["nodes"]
    ids = {n["id"]: f"n{max(used, default=0) + i}" for i, n in enumerate(added, start=1)}
    for n in added:
        n["id"], n["parent"] = ids[n["id"]], ids.get(n["parent"])
    proj["nodes"] += added

    out["parse_report"] = [e for e in out["parse_report"] if not auto_entry(e, project_name)] + auto_report(proj)
    return out


# --- document-carrying sources (#195) ---------------------------------------------------------
# A build-side test writes a WBS.md and, beside it, the portfolio document #12's build
# will read. A Source carries both: the bytes, and the nodes devagent reads from them.

# devagent v3.0.0 scripts/lib/portfolio.py: REASON_NO_WBS, and REASON_UNREADABLE with _err_detail's
# UnicodeDecodeError form (over the whole file).
NO_WBS_REASON = "the devdoc dir has no WBS.md"
UNREADABLE_REASON = "WBS.md could not be read: UnicodeDecodeError: {reason} at byte {start}"


@dataclass(frozen=True)
class Source:
    text: bytes  # the WBS.md bytes write_site_tree writes
    nodes: tuple  # numbered node dicts, project()'s shape; ids are made again per document
    report: tuple  # (line, kind, message): entries of kinds auto_report does not write


def _decode_error(text):
    try:
        text.decode("utf-8")
    except UnicodeDecodeError as e:
        return e
    return None


def source(text, *nodes, start=1, report=()) -> Source:
    """A WBS.md text and devagent's reading of it: `nodes` as project() numbers
    them from `start`, and `report`'s (line, kind, message) entries of kinds
    auto_report does not write (near_miss_line, issue_not_id ...). A foreign
    depends_on entry's dep_unresolved is not one of them: site_document
    resolves it against the document the site is in."""
    if not isinstance(text, bytes):
        raise TypeError(f"source: the text is {type(text).__name__}, not bytes")
    made = tuple(project("_", *nodes, start=start)["nodes"])
    if made and _decode_error(text) is not None:
        raise ValueError("source: a WBS.md that is not UTF-8 is unreadable, so it has no nodes")
    report = tuple(tuple(entry) for entry in report)
    for _, kind, message in report:
        if _foreign_unresolved(kind, message):
            raise ValueError(f"source: site_document resolves foreign depends_on entries itself: {message!r}")
    return Source(text, made, report)


def fixture_source(name) -> Source:
    """A FIXTURE_SOURCES text: its bytes, written.json's nodes of project `name`,
    and that project's entries auto_report does not write (classified under `name`),
    less its foreign dep_unresolved entries (site_document resolves those)."""
    doc = _parsed(sp.WRITTEN)
    (proj,) = [p for p in doc["projects"] if p["project"] == name]
    report = tuple((e["source_line"], e["kind"], e["message"]) for e in doc["parse_report"]
                   if e["project"] == name and not auto_entry(e, name)
                   and not _foreign_unresolved(e["kind"], e["message"]))
    return Source((sp.ROOT / sp.FIXTURE_SOURCES[name]).read_bytes(), tuple(copy.deepcopy(proj["nodes"])), report)


# devagent's dep_unresolved texts for a foreign entry (scripts/lib/portfolio.py at the pin); they
# depend on the other projects of the document, not on the site's own text.
NOT_CONFIGURED = "depends_on '{raw}' names project '{target}', which is not configured"
NOT_READ = "depends_on '{raw}' names project '{target}', whose WBS was not read ({status})"
NO_SUCH_ISSUE = "depends_on '{raw}' matches no issue in project '{target}'"


def _foreign_unresolved(kind, message):
    return kind == "dep_unresolved" and ("' names project '" in message or "' matches no issue in project '" in message)


def foreign_report(projects) -> list[dict]:
    """devagent's dep_unresolved entries of the foreign depends_on entries of `projects`'
    read nodes, in document order: by the branches devagent takes (not configured, not
    read, no such issue)."""
    by_name, issues = {}, {}
    for p in projects:
        by_name.setdefault(p["project"], p)
        if p["status"] == "read":
            issues.setdefault(p["project"], set()).update(n["issue"] for n in p["nodes"] if n["issue"] is not None)
    out = []
    for p in projects:
        name = p["project"]
        for n in p["nodes"]:
            value = n["meta"].get("depends_on")
            for entry in [value] if isinstance(value, str) and value else value if isinstance(value, list) else []:
                ref = deps.classify(entry, name)
                if ref.kind != "foreign":
                    continue
                target = by_name.get(ref.site)
                if target is None:
                    message = NOT_CONFIGURED.format(raw=entry, target=ref.site)
                elif target["status"] != "read":
                    message = NOT_READ.format(raw=entry, target=ref.site, status=target["status"])
                elif ref.issue in issues.get(ref.site, ()):
                    continue
                else:
                    message = NO_SUCH_ISSUE.format(raw=entry, target=ref.site)
                out.append({"project": name, "source_line": n["source_line"], "kind": "dep_unresolved",
                            "message": message})
    return out


def sample_source(site, text=None) -> Source:
    """A read sample site as samples.json holds it: `text` (default: its WBS.md bytes,
    else a copy of them such as CRLF line ends) with the sample's nodes, and its
    entries that are neither auto_report's nor a foreign dep_unresolved."""
    doc = _parsed(sp.SAMPLES_WRITTEN)
    (proj,) = [p for p in doc["projects"] if p["project"] == site]
    report = tuple((e["source_line"], e["kind"], e["message"]) for e in doc["parse_report"]
                   if e["project"] == site and not auto_entry(e, site)
                   and not _foreign_unresolved(e["kind"], e["message"]))
    if text is None:
        text = (sp.SAMPLES / site / "WBS.md").read_bytes()
    return Source(text, tuple(copy.deepcopy(proj["nodes"])), report)


def _not_read(name, status, reason):
    return {"project": name, "status": status, "status_reason": reason, "nodes": []}


def _highest_id(doc):
    return max((int(n["id"][1:]) for p in doc["projects"] for n in p["nodes"] if n["id"][1:].isdigit()), default=0)


def _project_of(name, source, first):
    """(project, report rows) for `source` as project `name`, node ids n<first>, n<first + 1> ..."""
    if not isinstance(source, Source):
        raise TypeError(f"site {name!r}: {type(source).__name__} is not a Source")
    error = _decode_error(source.text)
    if error is not None:
        return _not_read(name, "unreadable", UNREADABLE_REASON.format(reason=error.reason, start=error.start)), []
    ids = {n["id"]: f"n{i}" for i, n in enumerate(source.nodes, start=first)}
    nodes = [{**copy.deepcopy(n), "id": ids[n["id"]], "parent": ids.get(n["parent"])} for n in source.nodes]
    return {"project": name, "status": "read", "status_reason": None, "nodes": nodes}, \
        [(name, *entry) for entry in source.report]


def site_document(sites) -> dict:
    """The portfolio document over `sites`, {name: Source | None} in config order:
    None is devagent's no_wbs project, a text that is not UTF-8 its unreadable
    one, any other Source a read project; ids n<N> are unique across the document."""
    projects, report = [], []
    for name, value in sites.items():
        if value is None:
            projects.append(_not_read(name, "no_wbs", NO_WBS_REASON))
            continue
        made, rows = _project_of(name, value, 1 + sum(len(p["nodes"]) for p in projects))
        projects.append(made)
        report += rows
    doc = document(*projects, report=report)
    doc["parse_report"] += foreign_report(projects)
    return doc


def append_project(doc, name, source) -> dict:
    """A copy of `doc` with one more project `name` of `source`, after the others;
    its ids continue from the document's highest n<N>. Every project's foreign
    dep_unresolved entries are resolved again, since the new project can answer them."""
    out = copy.deepcopy(doc)
    made, rows = _project_of(name, source, _highest_id(out) + 1)
    out["projects"].append(made)
    kept = [e for e in out["parse_report"] if not _foreign_unresolved(e["kind"], e["message"])]
    out["parse_report"] = kept + auto_report(made) + [
        {"project": p, "source_line": line, "kind": kind, "message": message} for p, line, kind, message in rows
    ] + foreign_report(out["projects"])
    return out


# Two WBS texts written.json does not hold, described node by node: (source, first line, nodes).
DESCRIBED = {
    "heuristic": ("tests/fixtures/attention/heuristic.md", 2, (
        node("Epic one", node("First", issue="Issue-2"), node("Second", issue="Issue-3"), issue="Issue-1"),
        node("Epic four", node("Skipped", glyph="-", issue="Issue-5"), node("After a skip", issue="Issue-6"),
             issue="Issue-4"),
        node("Epic seven", node("Done", glyph="x", issue="Issue-8"), node("After a done", issue="Issue-9"),
             issue="Issue-7"),
        node("Top-level leaf", issue="Issue-10"),
        node("Epic eleven", node("A row", node("Under the row", issue="Issue-13"), issue="Issue-12"),
             issue="Issue-11"),
    )),
    "all-estimated": ("tests/fixtures/inputs/all-estimated.md", 3, (
        node("Frame", node("Posts", issue="Issue-2", est="6h"),
             node("Beams", issue="Issue-3", est="1d", depends_on=["Issue-2"]), issue="Issue-1"),
        node("Roof", node("Rafters", issue="Issue-5", est="4h", depends_on=["Issue-3"]),
             node("Tiles", issue="Issue-6", est="2h"), issue="Issue-4"),
    )),
}


def described_site(which, name="alpha"):
    _, start, nodes = DESCRIBED[which]
    return site(*nodes, name=name, start=start)


def sample_site(site):
    """SiteData of one sample site, as the committed sample document holds it."""
    return site_of(sample_doc(), site)
