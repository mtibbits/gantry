"""The portfolio-document builder the unit tests read site data through (#84).

tests/helpers_portfolio.py builds devagent's portfolio JSON for a test (node,
project, document, site) and narrows the committed written.json to one
project (fixture_doc, fixture_site). Every migrated test module reaches its
site data through hp.site_of, which maps the document with #83's
map_document. These tests pin the builder to what devagent writes.
"""

import json
import re

import pytest

import commit_check
import helpers_devagent as hd
import helpers_portfolio as hp
import sample_portfolio as sp
import update_expected_leaves as ue
from gantry import deps
from gantry.model import ParsedValue
from gantry.portfolio_json import ProjectInput, map_document
from helpers_config import append_bytes, copy_samples, sample_projects
from sample_roles import BUSY, NO_WBS, READABLE, SMALL, UNREADABLE
from test_samples import ROOT

# devagent's report kinds that the builder writes, as auto_report does.
AUTO_KINDS = ("leaf_no_issue", "duplicate_issue", "dep_unresolved")
NODE_LINE_RE = re.compile(r"^\s*- \[.\] ", re.M)


def local_dep(entry, project):
    """True for a dep_unresolved entry whose depends_on entry devagent's project calls local."""
    if entry["kind"] != "dep_unresolved":
        return True
    named = entry["message"].split("'")[1]
    return deps.classify(named, project).kind == "local"


def kinds(data, kind):
    return [entry for entry in data.report if entry.kind == kind]


# --- the builder (Task 2) ---------------------------------------------------------------------


def test_auto_report_matches_devagent_on_the_written_fixture():
    doc = hp.written_doc()
    read = [p for p in doc["projects"] if p["status"] == "read"]
    assert len(read) == 13
    for project in read:
        name = project["project"]
        made = sorted((e["source_line"], e["kind"], e["message"]) for e in hp.auto_report(project))
        written = sorted((e["source_line"], e["kind"], e["message"]) for e in doc["parse_report"]
                         if e["project"] == name and e["kind"] in AUTO_KINDS and local_dep(e, name))
        assert made == written, name


def test_site_numbers_nests_and_names():
    data = hp.site(hp.node("Epic", hp.node("A", issue="Issue-1"), hp.node("B")),
                   hp.node("Top", glyph="x", issue="Issue-3"))
    assert [(row.key, row.title, row.parent) for row in data.rows] == [("1", "Epic", None)]
    assert [leaf.key for leaf in data.leaves] == ["Issue-1", "1.2", "Issue-3"]
    assert [leaf.line for leaf in data.leaves] == [2, 3, 4]
    assert [leaf.epic for leaf in data.leaves] == ["1", "1", None]
    assert not data.leaves[2].open
    assert [entry.line for entry in kinds(data, "no_issue")] == [3]
    assert [entry.kind for entry in data.report] == ["no_issue"]


def test_site_meta_is_parsed_and_ordered():
    (leaf,) = hp.site(hp.node("L", satisfies="a:FR-1", issue="I-1", depends_on=["I-2"])).leaves
    assert list(leaf.meta) == ["satisfies", "issue", "depends_on"]
    assert isinstance(leaf.meta["satisfies"], ParsedValue) and leaf.meta["satisfies"] == "a:FR-1"
    assert leaf.meta["depends_on"] == ["I-2"] and all(isinstance(v, ParsedValue) for v in leaf.meta["depends_on"])
    assert leaf.issue == "I-1"
    data = hp.site(hp.node("L", issue=["I-1"]))
    assert data.leaves[0].issue is None
    assert kinds(data, "no_issue") == []


def test_site_reports_local_unresolved_and_duplicates():
    data = hp.site(hp.node("A", issue="Issue-1", depends_on=["Issue-9"]))
    assert [entry.message for entry in kinds(data, "unresolved_ref")] == [
        deps.MESSAGES["unresolved_ref"].format(line=1, entry="Issue-9")]
    data = hp.site(hp.node("A", issue="Issue-1"), hp.node("B", issue="Issue-1"))
    assert [(entry.line, entry.kind) for entry in data.report] == [(2, "duplicate_issue")]
    doc = hp.document(hp.project("alpha", hp.node("A", issue="Issue-2", depends_on=["beta:Issue-1"])))
    assert doc["parse_report"] == []
    mapped = map_document(doc)
    assert mapped.devagent_report == ()
    assert kinds(mapped.projects[0].data, "unresolved_ref") == []
    assert kinds(mapped.projects[0].data, "duplicate_issue") == []


def test_explicit_lines_and_bad_order():
    data = hp.site(hp.node("A", issue="I-1", line=15), hp.node("B", issue="I-2"))
    assert [leaf.line for leaf in data.leaves] == [15, 16]
    with pytest.raises(ValueError, match="'B'"):
        hp.site(hp.node("A", issue="I-1", line=15), hp.node("B", issue="I-2", line=15))
    with pytest.raises(ValueError, match="'B'"):
        hp.project("alpha", hp.node("A", line=4), hp.node("B", line=2))
    data = hp.site(hp.node("A", issue="I-1"), hp.node("B", issue="I-2"), start=3)
    assert [leaf.line for leaf in data.leaves] == [3, 4]


def test_fixture_doc_narrows_and_renames():
    doc = hp.written_doc()
    before = sp.dumps(doc)
    narrowed = hp.narrow(doc, "refs", "alpha")
    assert sp.dumps(doc) == before
    assert hp.fixture_doc("refs") == narrowed
    assert [p["project"] for p in narrowed["projects"]] == ["alpha"]
    written = [e for e in doc["parse_report"] if e["project"] == "refs"]
    assert len(narrowed["parse_report"]) == len(written) > 0
    assert {e["project"] for e in narrowed["parse_report"]} == {"alpha"}
    assert [leaf.key for leaf in hp.fixture_site("refs").leaves] == ["Issue-1", "Issue-2", "Issue-3", "Issue-4"]
    assert hp.fixture_site("refs", "beta").leaves[0].deps == ()
    with pytest.raises(ValueError, match="not_utf8"):
        hp.site_of(hp.written_doc(), "not_utf8")
    with pytest.raises(ValueError):
        hp.site_of(hp.written_doc())


def test_drop_lines():
    doc = hp.fixture_doc("cycle")
    cut = hp.drop_lines(doc, 2, 3, 4)
    assert len(doc["projects"][0]["nodes"]) == 8
    assert len(cut["projects"][0]["nodes"]) == 5
    assert all(e["source_line"] not in (2, 3, 4) for e in cut["parse_report"])
    assert [leaf.line for leaf in hp.site_of(cut).leaves] == [5, 6, 7, 8, 9]
    rows = hp.fixture_doc("rows")
    row_line = next(n["source_line"] for n in rows["projects"][0]["nodes"]
                    if any(m["parent"] == n["id"] for m in rows["projects"][0]["nodes"]))
    with pytest.raises(ValueError):
        hp.drop_lines(rows, row_line)
    with pytest.raises(ValueError):
        hp.drop_lines(doc, 1)
    with pytest.raises(ValueError, match="Issue-2"):
        hp.drop_lines(doc, 3)


def test_described_fixtures_are_in_line():
    assert list(hp.DESCRIBED) == ["heuristic", "all-estimated"]
    for name, (path, start, nodes) in hp.DESCRIBED.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        assert len(NODE_LINE_RE.findall(text)) == len(hp.flatten(nodes)), name
        data = hp.described_site(name)
        lines = [n.line for n in (*data.leaves, *data.rows)]
        assert min(lines) == start, name


# --- the committed sample document (Task 3) ---------------------------------------------------


def test_samples_fixture_inputs_are_current():
    tag, _ = hd.read_pin()
    made = json.loads(sp.SAMPLES_PROVENANCE.read_bytes().decode("utf-8"))
    assert made["pin"] == tag
    assert list(made["inputs"]) == list(sp.sample_inputs())
    assert sp.stale_inputs() == [], (
        "samples/ changed since tests/fixtures/portfolio/samples.json was written; regenerate it: "
        f"python3 tools/sample_portfolio.py --fixtures --devagent <a devagent checkout at {tag}>")


def test_samples_fixture_staleness_is_seen(tmp_path):
    copy = copy_samples(tmp_path)
    assert sp.stale_inputs(copy) == []
    append_bytes(copy / BUSY / "WBS.md", "- [ ] Late addition {issue: Issue-99}\n")
    assert sp.stale_inputs(copy) == [f"samples/{BUSY}/WBS.md"]


def test_samples_fixture_shape():
    tag, version = hd.read_pin()
    raw = sp.SAMPLES_WRITTEN.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw
    doc = hp.sample_doc()
    assert hp.sample_doc() is not doc
    assert [p["project"] for p in doc["projects"]] == sample_projects()
    status = {p["project"]: p["status"] for p in doc["projects"]}
    assert (status[NO_WBS], status[UNREADABLE]) == ("no_wbs", "unreadable")
    assert {status[name] for name in READABLE} == {"read"}
    assert doc["generated_at"] == sp.FIXED_TIME
    assert len(doc["parse_report"]) == 12
    mapped = map_document(doc)
    assert sum(len(p.data.leaves) + len(p.data.rows) for p in mapped.projects) == 52
    made = json.loads(sp.SAMPLES_PROVENANCE.read_bytes().decode("utf-8"))
    assert list(made) == ["pin", "devagent_version", "schema_version", "generated_at", "inputs"]
    assert (made["pin"], made["devagent_version"]) == (tag, version)
    assert (made["schema_version"], made["generated_at"]) == (doc["schema_version"], sp.FIXED_TIME)
    assert made["inputs"][f"samples/{NO_WBS}/WBS.md"] is None
    for path in (sp.SAMPLES_WRITTEN, sp.SAMPLES_PROVENANCE):
        assert commit_check.path_form_hits(path.read_bytes().decode("utf-8")) == [], path.name


def test_samples_fixture_matches_the_snapshots():
    made = ue.snapshots(map_document(hp.sample_doc()), hd.read_pin()[0])
    assert list(made) == sample_projects()
    for name, data in made.items():
        assert data == (ue.OUT / f"{name}.json").read_bytes(), name


def test_sample_site_reads_each_readable_sample():
    for name in sorted(READABLE):
        assert hp.sample_site(name).leaves, name
    with pytest.raises(ValueError, match=NO_WBS):
        hp.sample_site(NO_WBS)


# --- agreement with the stopgap (Task 5; #12 deletes it) ---------------------------------------

AGREEMENT = [p["project"] for p in hp.written_doc()["projects"] if p["status"] == "read"] + list(hp.DESCRIBED)


def known_rows(name):
    entries = json.loads(ue.KNOWN.read_bytes().decode("utf-8"))
    return [(e["where"], e["stopgap"], e["snapshot"]) for e in entries if e["set"] == "fixtures" and e["site"] == name]


def alpha_snapshot(data):
    return ue.snapshot_of(ProjectInput("alpha", "read", None, data), (), pin=hd.read_pin()[0], schema_version="1.1",
                          namespaces=frozenset({"alpha"}))


def test_agreement_covers_every_read_fixture_and_description():
    assert len(AGREEMENT) == len(set(AGREEMENT)) == 15
    assert set(AGREEMENT) == {*sp.FIXTURE_SOURCES} - {"not_utf8"} | set(hp.DESCRIBED)
    assert [name for name in AGREEMENT if known_rows(name)] == ["ids", "edges"]


@pytest.mark.parametrize("name", AGREEMENT)
def test_fixture_sites_agree_with_the_stopgap(name):
    # The builder's sites read like the stopgap's reading of the same text, renamed alpha,
    # but for the listed devagent-reading differences (known_differences.json).
    from gantry.wbs_stopgap import parse_wbs

    if name in hp.DESCRIBED:
        source, data = hp.DESCRIBED[name][0], hp.described_site(name)
    else:
        source, data = sp.FIXTURE_SOURCES[name], hp.fixture_site(name)
    stopgap = parse_wbs((ROOT / source).read_bytes().decode("utf-8"), "alpha")
    found = ue.diff_snapshots(alpha_snapshot(stopgap), alpha_snapshot(data))
    assert [tuple(row) for row in found] == known_rows(name)


def test_append_nodes():
    doc = hp.narrow(hp.sample_doc(), SMALL)
    before = sp.dumps(doc)
    nodes = doc["projects"][0]["nodes"]
    last = max(n["source_line"] for n in nodes)
    out = hp.append_nodes(doc, SMALL, hp.node("Late", glyph="x", issue="Issue-99"), hp.node("Loose"))
    assert sp.dumps(doc) == before
    added = out["projects"][0]["nodes"][len(nodes):]
    assert [n["source_line"] for n in added] == [last + 1, last + 2]
    ids = [n["id"] for n in out["projects"][0]["nodes"]]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"n[0-9]+", i) for i in ids)
    data = hp.site_of(out, SMALL)
    late = next(leaf for leaf in data.leaves if leaf.key == "Issue-99")
    assert (late.line, late.open) == (last + 1, False)
    assert [entry.line for entry in kinds(data, "no_issue")] == [
        entry.line for entry in kinds(hp.sample_site(SMALL), "no_issue")] + [last + 2]
