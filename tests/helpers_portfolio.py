"""devagent's portfolio JSON as test data (#83, #84): the committed fixtures, the
gated sample document, and the builder the unit tests read site data through.

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). tools/sample_portfolio.py makes the
committed documents with the pinned devagent release's own aggregator.

The builder (#84) writes a document in devagent's form: node() describes one
node, project() numbers a tree of them, document() wraps projects with the
report entries devagent always writes (auto_report), and site() maps one
project to SiteData. fixture_doc() narrows written.json to one project. A
test reaches site data only through site_of(), the one caller of
map_document here. Callers import this module as `hp` and never take names
from it: conftest's `node` fixture would clash.
"""

import copy
import json
from pathlib import Path

import helpers_devagent as hd
import sample_portfolio as sp
from gantry import deps
from gantry.model import STATE_GLYPHS
from gantry.portfolio_json import map_document
from helpers_config import copy_samples

DOC_NAME = "portfolio.json"


written_doc = sp.written_doc


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


def _entries(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) and value else list(value) if isinstance(value, list) else []


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
        for entry in _entries(n["meta"].get("depends_on")):
            ref = deps.classify(entry, name)
            if ref.kind == "local" and ref.issue not in held:
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


def site(*nodes, name="alpha", start=1, report=()):
    """SiteData of one project of `nodes`; `report` holds extra (line, kind, message) entries on it."""
    return site_of(document(project(name, *nodes, start=start), report=[(name, *entry) for entry in report]))


def narrow(doc, name, site=None) -> dict:
    """A fresh copy of `doc` holding only the project `name` and its report entries, renamed `site`."""
    out = copy.deepcopy(doc)
    out["projects"] = [p for p in out["projects"] if p["project"] == name]
    if not out["projects"]:
        raise ValueError(f"the document holds no project {name!r}")
    out["parse_report"] = [e for e in out["parse_report"] if e["project"] == name]
    if site is not None:
        for item in (*out["projects"], *out["parse_report"]):
            item["project"] = site
    return out


def fixture_doc(name, site="alpha") -> dict:
    """written.json's project `name` alone, renamed `site`."""
    return narrow(written_doc(), name, site)


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
        for entry in _entries(n["meta"].get("depends_on")):
            ref = deps.classify(entry, proj["project"])
            if ref.kind == "local" and ref.issue in dropped:
                raise ValueError(f"line {n['source_line']} depends on {ref.issue}, which is dropped")
    out["parse_report"] = [e for e in out["parse_report"]
                           if e["project"] != proj["project"] or e["source_line"] not in lines]
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


def described_site(name, site="alpha"):
    _, start, nodes = DESCRIBED[name]
    return site_of(document(project(site, *nodes, start=start)))


def sample_doc() -> dict:
    """tests/fixtures/portfolio/samples.json, parsed: a fresh copy on every call."""
    return json.loads(sp.SAMPLES_WRITTEN.read_bytes().decode("utf-8"))


def sample_site(site):
    """SiteData of one sample site, as the committed sample document holds it."""
    return site_of(sample_doc(), site)
