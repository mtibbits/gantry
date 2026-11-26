"""The portfolio-document builder the unit tests read site data through (#84).

tests/helpers_portfolio.py builds devagent's portfolio JSON for a test (node,
project, document, site) and narrows the committed written.json to one
project (fixture_doc, fixture_site). Every migrated test module reaches its
site data through hp.site_of, which maps the document with #83's
map_document. These tests pin the builder to what devagent writes.
"""

import hashlib
import json
import os
import re

import pytest

import commit_check
import helpers_devagent as hd
import helpers_portfolio as hp
import sample_portfolio as sp
import update_expected_leaves as ue
from gantry import deps
from gantry.model import ParsedValue
from gantry.portfolio_json import map_document
from helpers_config import append_bytes, copy_samples, install_sample_document, sample_projects, write_site_tree
from helpers_links import link_dir, symlink
from sample_roles import BUSY, NO_WBS, READABLE, SMALL, UNREADABLE
from test_samples import ROOT

NODE_LINE_RE = re.compile(r"^\s*- \[.\] ", re.M)


def kinds(data, kind):
    return [entry for entry in data.report if entry.kind == kind]


# --- the builder (Task 2) ---------------------------------------------------------------------


def test_auto_report_matches_devagent_on_the_written_fixture():
    doc = hp.written_doc()
    read = [p for p in doc["projects"] if p["status"] == "read"]
    assert len(read) == 14
    for project in read:
        name = project["project"]
        made = sorted((e["source_line"], e["kind"], e["message"]) for e in hp.auto_report(project))
        written = sorted((e["source_line"], e["kind"], e["message"]) for e in doc["parse_report"]
                         if hp.auto_entry(e, name))
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
    # site_document (#195): ids unique across the document, bytes only, a Source's report under the site name.
    doc = hp.site_document({"a": hp.source(b"- [ ] A {issue: Issue-1}\n", hp.node("A", issue="Issue-1")),
                            "b": hp.source(b"- [ ] E\n  - [ ] F\n", hp.node("E", hp.node("F")))})
    ids = [n["id"] for p in doc["projects"] for n in p["nodes"]]
    assert ids == ["n1", "n2", "n3"]
    assert [n["parent"] for n in doc["projects"][1]["nodes"]] == [None, "n2"]
    with pytest.raises(TypeError):
        hp.source("x")
    near = hp.site_document({"a": hp.source(b"", report=((3, "near_miss_line", "m"),))})
    assert [(e["project"], e["source_line"], e["kind"]) for e in near["parse_report"]] == [("a", 3, "near_miss_line")]


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
    # site_document (#195) resolves foreign entries against the whole document, as devagent does
    # (scripts/lib/portfolio.py at the pin, the dep_unresolved branches); a Source never carries them.
    wait = b"- [ ] A {issue: Issue-1, depends_on: [b:Issue-1, b:Issue-9, c:Issue-1, n:Issue-1]}\n"
    doc = hp.site_document({
        "a": hp.source(wait, hp.node("A", issue="Issue-1", depends_on=["b:Issue-1", "b:Issue-9", "c:Issue-1", "n:Issue-1"])),
        "b": hp.source(b"- [ ] B {issue: Issue-1}\n", hp.node("B", issue="Issue-1")), "n": None})
    assert [(e["project"], e["source_line"], e["message"]) for e in doc["parse_report"]] == [
        ("a", 1, "depends_on 'b:Issue-9' matches no issue in project 'b'"),
        ("a", 1, "depends_on 'c:Issue-1' names project 'c', which is not configured"),
        ("a", 1, "depends_on 'n:Issue-1' names project 'n', whose WBS was not read (no_wbs)")]
    foreign = (1, "dep_unresolved", "depends_on 'c:Issue-1' names project 'c', which is not configured")
    with pytest.raises(ValueError, match="site_document"):
        hp.source(wait, report=(foreign,))
    assert not [e for e in hp.fixture_source("page").report if "names project" in e[2]]


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
    # fixture_source (#195): written.json's nodes and its entries of the kinds auto_report does not write.
    refs = hp.fixture_source("refs")
    assert refs.text == (ROOT / sp.FIXTURE_SOURCES["refs"]).read_bytes()
    from_source = hp.site_of(hp.site_document({"alpha": refs}))
    assert [n.key for n in (*from_source.leaves, *from_source.rows)] == [
        n.key for n in (*hp.fixture_site("refs").leaves, *hp.fixture_site("refs").rows)]
    assert refs.report and not {kind for _, kind, _ in refs.report} & set(hp.AUTO_KINDS) - {"dep_unresolved"}
    assert all(not hp.auto_entry({"project": "refs", "kind": k, "message": m}, "refs") for _, k, m in refs.report)


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


def test_write_site_tree_refuses_bytes(tmp_path):
    # #195: every site carries its portfolio document, so bare WBS.md bytes are refused before anything is written.
    with pytest.raises(TypeError, match=r"hp\.source"):
        write_site_tree(tmp_path, {"alpha": b"- [ ] A\n"})
    assert list(tmp_path.iterdir()) == []


def test_copy_samples_installs_the_committed_document(tmp_path):
    copy = copy_samples(tmp_path / "a")
    assert copy.joinpath(sp.OUT, *sp.DOCUMENT).read_bytes() == sp.SAMPLES_WRITTEN.read_bytes()
    bare = copy_samples(tmp_path / "b", document=False)
    assert not (bare / "devagent" / "out").exists()
    # The writer refuses a link at each folder it makes or writes into, and writes nothing through it.
    for linked in (("out",), ("out", "portfolio")):
        elsewhere = tmp_path / ("elsewhere-" + "-".join(linked))
        elsewhere.mkdir()
        bare = copy_samples(tmp_path / ("linked-" + "-".join(linked)), document=False)
        bare.joinpath("devagent", *linked[:-1]).mkdir(parents=True, exist_ok=True)
        link_dir(elsewhere, bare.joinpath("devagent", *linked))
        with pytest.raises(sp.Refused, match="link"):
            install_sample_document(bare)
        assert list(elsewhere.iterdir()) == []
    bare = copy_samples(tmp_path / "linked-devagent", document=False)
    elsewhere = tmp_path / "elsewhere-devagent"
    bare.joinpath("devagent").rename(elsewhere)
    link_dir(elsewhere, bare / "devagent")
    with pytest.raises(sp.Refused, match="link"):
        install_sample_document(bare)
    assert not (elsewhere / "out").exists()
    if os.name != "nt":  # a file symlink needs a privilege on Windows; the folder legs above use junctions there
        bare = copy_samples(tmp_path / "linked-file", document=False)
        target = bare.joinpath(sp.OUT, *sp.DOCUMENT)
        target.parent.mkdir(parents=True)
        outside = tmp_path / "outside.json"
        outside.write_bytes(b"")
        symlink(outside, target)
        with pytest.raises(sp.Refused, match="link"):
            install_sample_document(bare)
        assert outside.read_bytes() == b""


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
    # sample_source (#195): the sample's bytes, or a copy of them, carrying the sample's own reading.
    whole = {name: hp.sample_source(name) for name in sample_projects() if name != NO_WBS}
    assert whole[SMALL].text == (ROOT / "samples" / SMALL / "WBS.md").read_bytes()
    doc = hp.site_document({**whole, NO_WBS: None})
    assert ue.snapshots(map_document(doc), "x") == ue.snapshots(map_document(hp.sample_doc()), "x")
    assert hp.sample_source(SMALL, b"x").text == b"x"


# --- the stored readings of the installable trees (#197) ---------------------------------------


def test_stored_trees_match_their_inputs(tmp_path):
    """Every tree install() copies, the pin its reading was made at, and each input as committed: an
    edit to a fixture WBS.md or devagent.toml reds here, since the build reads only the stored
    reading (#12). Each tree then installs, its document written from the stored readings."""
    fixtures = ROOT / "tests" / "fixtures"
    stored = hp._parsed(hp.TREES)
    trees = sorted(p.parent for p in (fixtures / "week_plan").glob("*/gantry.toml")) + [
        fixtures / "capacity" / "install", fixtures / "capacity" / "install_dep"]
    assert list(stored["trees"]) == [tree.relative_to(fixtures).as_posix() for tree in trees]
    regenerate = ("tests/fixtures/week_plan/trees.json is stale: regenerate it with gen_trees197.py "
                  "(#197's pull request) against the pinned devagent")
    assert stored["pin"] == hd.read_pin()[0], regenerate
    inputs = [path for tree in trees for path in (tree / "devagent.toml", *sorted(tree.rglob("WBS.md")))]
    assert stored["inputs"] == {path.relative_to(fixtures).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in inputs}, regenerate
    from test_planner import install

    for tree in stored["trees"]:
        install(tmp_path, fixtures / tree)


def test_document_report_entries_follow_the_auto_entries():
    entry = ("alpha", 9, "skipped_line", "line 9 looks like a node but is not one")
    doc = hp.document(hp.project("alpha", hp.node("A", issue="Issue-1"), hp.node("B")), report=[entry])
    assert [(e["source_line"], e["kind"]) for e in doc["parse_report"]] == [(2, "leaf_no_issue"), (9, "skipped_line")]
    assert [(e.line, e.kind, e.message) for e in map_document(doc).devagent_report] == [entry[1:]]


def test_append_nodes_keeps_foreign_and_malformed_unresolved():
    # refs.md's nine dep_unresolved entries: only 'Issue-99' is one auto_report writes again. The
    # malformed ones share its wording, and 'alpha:Issue-1' is foreign though the rename makes it read local.
    doc = hp.fixture_doc("refs")
    before = sorted(e["message"] for e in doc["parse_report"] if e["kind"] == "dep_unresolved")
    assert len(before) == 9 and "depends_on 'alpha:Issue-1' names project 'alpha', which is not configured" in before
    out = hp.append_nodes(doc, "alpha", hp.node("Late", issue="Issue-98"))
    assert sorted(e["message"] for e in out["parse_report"] if e["kind"] == "dep_unresolved") == before
    with pytest.raises(ValueError, match="no project 'beta'"):
        hp.append_nodes(doc, "beta", hp.node("Late"))


def test_append_nodes(tmp_path):
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
    # append_project (#195): one read project more, its ids from the document's highest + 1.
    one = hp.site_document({"a": hp.source(b"- [ ] A {issue: Issue-1}\n", hp.node("A", issue="Issue-1"))})
    two = hp.append_project(one, "b", hp.source(b"- [ ] B\n", hp.node("B")))
    assert [p["project"] for p in two["projects"]] == ["a", "b"]
    assert [n["id"] for n in two["projects"][1]["nodes"]] == ["n2"]
    assert [(e["project"], e["kind"]) for e in two["parse_report"]] == [("b", "leaf_no_issue")]
    # rewrite_document (#195): a samples copy's document, changed where it lies; a change that changes nothing fails.
    copy = copy_samples(tmp_path)
    append_bytes(copy / SMALL / "WBS.md", "- [ ] Later {issue: Issue-98}\n")
    assert hp.document_path(copy) == copy.joinpath(sp.OUT, *sp.DOCUMENT)
    hp.rewrite_document(copy, lambda d: hp.append_nodes(d, SMALL, hp.node("Later", issue="Issue-98")))
    written = json.loads(copy.joinpath(sp.OUT, *sp.DOCUMENT).read_bytes().decode("utf-8"))
    assert "Issue-98" in [leaf.key for leaf in hp.site_of(written, SMALL).leaves]
    with pytest.raises(AssertionError):
        hp.rewrite_document(copy, lambda d: d)
    # No check against the copy's WBS.md remains (#12): the document is the build's only source.
    # The install() layout (#197), as write_site_tree lays a tree out: the document beside devagent.toml,
    # each devdoc_dir relative to the folder.
    tree = tmp_path / "tree"
    write_site_tree(tree, {"a": hp.source(b"- [ ] A {issue: Issue-1}\n", hp.node("A", issue="Issue-1"))})
    assert hp.document_path(tree) == tree / "portfolio.json"
    append_bytes(tree / "0" / "WBS.md", "- [ ] B {issue: Issue-2}\n")
    hp.rewrite_document(tree, lambda d: hp.append_nodes(d, "a", hp.node("B", issue="Issue-2")))
    written = json.loads((tree / "portfolio.json").read_bytes().decode("utf-8"))
    assert [leaf.key for leaf in hp.site_of(written, "a").leaves] == ["Issue-1", "Issue-2"]
    with pytest.raises(AssertionError):
        hp.rewrite_document(tree, lambda d: d)
    # An install() copy (#197) holds its document there too, and rewrite_document round-trips on it.
    from test_planner import install

    installed = install(tmp_path, "two_sites")
    assert hp.document_path(installed) == installed / "portfolio.json"
    append_bytes(installed / "a" / "WBS.md", "- [ ] Later {issue: Issue-98}\n")
    hp.rewrite_document(installed, lambda d: hp.append_nodes(d, "a", hp.node("Later", issue="Issue-98")))
    written = json.loads((installed / "portfolio.json").read_bytes().decode("utf-8"))
    assert "Issue-98" in [leaf.key for leaf in hp.site_of(written, "a").leaves]
    with pytest.raises(AssertionError):
        hp.rewrite_document(installed, lambda d: d)
    # A project appended to the document answers the foreign entries that named it.
    waits = hp.site_document({"a": hp.source(b"- [ ] A {issue: Issue-1, depends_on: [c:Issue-1]}\n",
                                             hp.node("A", issue="Issue-1", depends_on=["c:Issue-1"]))})
    assert [e["kind"] for e in waits["parse_report"]] == ["dep_unresolved"]
    answered = hp.append_project(waits, "c", hp.source(b"- [ ] C {issue: Issue-1}\n", hp.node("C", issue="Issue-1")))
    assert answered["parse_report"] == []
    # A project devagent lists but does not read, appended; it answers no foreign entry.
    unread = hp.append_not_read(waits, "c", "no_devdoc_dir", hp.ABSENT_REASON)
    assert [(p.name, p.status, p.reason) for p in map_document(unread).projects][1:] == [
        ("c", "no_devdoc_dir", "the devdoc dir does not exist")]
    assert [e["message"] for e in unread["parse_report"]] == [
        "depends_on 'c:Issue-1' names project 'c', whose WBS was not read (no_devdoc_dir)"]


def test_site_document_not_read_reason_is_devagents():
    # The not-read projects site_document writes are devagent's own, as the aggregator wrote them in samples.json.
    doc = hp.site_document({"x": None, "m": hp.source((ROOT / "samples" / UNREADABLE / "WBS.md").read_bytes())})
    written = {p["project"]: p for p in hp.sample_doc()["projects"]}
    made = {p["project"]: p for p in doc["projects"]}
    for mine, theirs in (("x", NO_WBS), ("m", UNREADABLE)):
        assert (made[mine]["status"], made[mine]["status_reason"]) == (
            written[theirs]["status"], written[theirs]["status_reason"]), mine
        assert made[mine]["nodes"] == written[theirs]["nodes"] == [], mine
    # A project devagent lists but does not read (#197): hp.NotRead, in config order with the others.
    doc = hp.site_document({"d": hp.NotRead("no_devdoc_dir", hp.UNSET_REASON), "x": None})
    assert [(p["project"], p["status"], p["status_reason"], p["nodes"]) for p in doc["projects"]] == [
        ("d", "no_devdoc_dir", "no devdoc dir is configured", []), ("x", "no_wbs", hp.NO_WBS_REASON, [])]
    with pytest.raises(TypeError, match=r"hp\.source"):  # a bare (status, reason) is not a not-read project
        hp.site_document({"d": ("no_devdoc_dir", hp.UNSET_REASON)})
