"""The five time thieves (#29), derived as values from constructed inputs.
The page's row is in tests/test_strategic_page.py; the build's wiring in
tests/test_build.py."""

import ast
import dataclasses
import os
from datetime import date
from pathlib import Path, PurePath
from types import SimpleNamespace

import pytest

import helpers_portfolio as hp
from gantry import thieves, wbs_history
from gantry.devagent_records import (ABSENT, NOT_READ, PRESENT, READ, UNREADABLE, IssueRecords, Record, SiteRecords,
                                     read_checklist)
from gantry.fires import Fire, FireReport, SourceStatus
from gantry.roots import directory_roots
from gantry.strategic import Panel, StrategicModel
from gantry.wbs_history import GIT_FAILED, HistoryResult, NoData
from helpers_history import commit, make_repo

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 1, 19)
SITE = "alpha"
PROGRESS = "- [x] 1. pull\n- [ ] 2. draft\n"
UNSTARTED = "- [ ] 1. pull\n"
PARKED = "- [x] 1. pull\n- [P] 2. draft\n"
ABSENT_RECORD = Record(ABSENT, None, None)
MACHINE = "as seen by this build machine: "


# --- constructed inputs ----------------------------------------------------------------------


def panel(name=SITE, gap=None, gap_reason=None, built=True):
    return Panel(site=name, built=built, href=None, reason=None, open_leaves=0, held_out=0, backlog=None,
                 estimates=None, intended=None, comparable=None, gap=gap, serves=(), gap_reason=gap_reason)


def model_of(*panels, headline=None):
    return StrategicModel(as_of=AS_OF, configured=False, allocation=None, panels={p.site: p for p in panels},
                          groups=(), total_hours=0, total_unestimated=0, off_not_built=0, off_not_in_config=0,
                          headline=headline, report=())


def result_of(sites, foreign=None):
    """A stand-in BuildResult: `sites` maps a name to its SiteData."""
    return SimpleNamespace(inputs={n: SimpleNamespace(data=d) for n, d in sites.items()},
                           foreign=foreign if foreign is not None else {n: () for n in sites})


def leaves(*specs):
    """SiteData of one leaf per (glyph, issue) in order."""
    return hp.site(*(hp.node(f"Leaf {i}", glyph=g, issue=issue) for i, (g, issue) in enumerate(specs, 1)))


def local(texts, unreadable=()):
    """SiteRecords holding a local checklist per issue (a text), and unreadable ones."""
    issues = {issue: IssueRecords(issue, ABSENT_RECORD, Record(PRESENT, read_checklist(text), None), ABSENT_RECORD)
              for issue, text in texts.items()}
    for issue in unreadable:
        issues[issue] = IssueRecords(issue, ABSENT_RECORD, Record(UNREADABLE, None, "checklist.md cannot be read"),
                                     ABSENT_RECORD)
    return SiteRecords(READ, None, dict(sorted(issues.items())), {})


def all_read(*sites):
    statuses = [SourceStatus("fires_list", None, "read", None)]
    for name in sites:
        statuses += [SourceStatus("p0_leaf", name, "read", None), SourceStatus("stuck", name, "read", None)]
    return FireReport((), tuple(statuses))


def history(repo=NoData("x"), *, day=date(2026, 1, 8), start=False, uncommitted=False, reason=None, no_data=None):
    if no_data is not None:
        return HistoryResult(None, False, 0, None, no_data.text, repo, no_data)
    return HistoryResult(day, start, 1, uncommitted, reason, repo, None)


def planning_repo(tmp_path, committed):
    """A fixture planning repository with `committed` ({issue: checklist text}) at HEAD: its Repo."""
    top = make_repo(tmp_path / "planning")
    commit(top, "WBS.md", "# S\n", "2026-01-05")
    for issue, text in committed.items():
        commit(top, f"{issue}/checklist.md", text, "2026-01-05")
    gantry = PurePath(os.path.realpath(make_repo(tmp_path / "gantry-checkout")))
    repo = wbs_history.history_repository(directory_roots([tmp_path]), top, gantry, key="devdoc_dir", site=SITE)
    assert isinstance(repo, wbs_history.Repo), repo
    return repo


def derive(data, *, hist, records=None, fires=None, portfolio=None, foreign=None, gap=None, gap_reason=None):
    """The one site's Thieves."""
    got = thieves.derive_thieves(result_of({SITE: data}, foreign), model_of(panel(gap=gap, gap_reason=gap_reason)),
                                 portfolio, fires if fires is not None else all_read(SITE),
                                 {SITE: records if records is not None else local({})}, {SITE: hist}, AS_OF)
    return got[SITE]


def texts(thief):
    return [p.text for p in thief.parts]


def machine_parts(thief):
    return [p.text for p in thief.parts if p.machine]


# --- WIP (AC1) ---------------------------------------------------------------------------------


def test_thieves_imports_no_stopgap():
    forbidden = "gantry." + "wbs_" + "stopgap"
    tree = ast.parse((ROOT / "gantry" / "thieves.py").read_bytes().decode("utf-8"))
    seen = 0
    for item in ast.walk(tree):
        if isinstance(item, ast.ImportFrom):
            seen += 1
            names = [item.module or ""] + [f"{item.module}.{a.name}" for a in item.names]
            assert not any(n == forbidden or n.startswith(forbidden + ".") for n in names), names
            assert not (item.module == "gantry" and any(a.name == "wbs_" + "stopgap" for a in item.names))
        elif isinstance(item, ast.Import):
            assert not any(a.name == forbidden or a.name.startswith(forbidden + ".") for a in item.names)
    assert seen >= 1


def test_thieves_imports_no_view_and_reads_no_clock():
    source = (ROOT / "gantry" / "thieves.py").read_bytes().decode("utf-8")
    tree = ast.parse(source)
    modules = {i.module for i in ast.walk(tree) if isinstance(i, ast.ImportFrom)}
    modules |= {a.name for i in ast.walk(tree) if isinstance(i, ast.Import) for a in i.names}
    assert not any(m and m.startswith("gantry.views") for m in modules), modules
    assert "today(" not in source and "now(" not in source


def test_wip_counts_started_glyphs():
    specs = [("~", "Issue-1"), ("!", "Issue-2"), ("?", "Issue-3"), ("P", "Issue-4"), ("x", "Issue-5"),
             ("-", "Issue-6"), (" ", "Issue-7")]
    wip = derive(leaves(*specs), hist=history()).wip
    assert (wip.value, wip.text, wip.threshold, wip.flagged) == (3, "3", 3, False)
    wip = derive(leaves(*specs, ("~", "Issue-8")), hist=history()).wip
    assert (wip.value, wip.flagged) == (4, True)
    assert wip.source == thieves.SOURCE_WIP


def test_wip_pending_with_committed_progress_counts(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {"Issue-1": PROGRESS})
    wip = derive(leaves((" ", "Issue-1")), hist=history(repo)).wip
    assert wip.value == 1 and "1 started, glyph pending" in texts(wip)
    assert dict(wip.counts) == {"parked": 0, "started": 1}
    repo = planning_repo(tmp_path / "u", {"Issue-1": UNSTARTED})
    wip = derive(leaves((" ", "Issue-1")), hist=history(repo)).wip
    assert wip.value == 0 and "0 started, glyph pending" in texts(wip)


def test_wip_committed_parked_is_subtracted(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {"Issue-1": PARKED})
    wip = derive(leaves(("~", "Issue-1")), hist=history(repo)).wip
    assert wip.value == 0 and "1 parked, not counted" in texts(wip)
    assert dict(wip.counts) == {"parked": 1, "started": 0}


def test_wip_parked_here_only_is_labelled(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {"Issue-1": PROGRESS})
    wip = derive(leaves(("~", "Issue-1"), ("~", "Issue-2")), hist=history(repo),
                 records=local({"Issue-2": PARKED})).wip
    assert wip.value == 2
    assert machine_parts(wip) == [MACHINE + "1 parked here only, still counted: Issue-2"]


def test_wip_started_here_only_is_labelled(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {"Issue-9": UNSTARTED})
    wip = derive(leaves((" ", "Issue-1"), ("x", "Issue-2")), hist=history(repo),
                 records=local({"Issue-1": PROGRESS, "Issue-2": PROGRESS})).wip
    assert wip.value == 0
    assert machine_parts(wip) == [MACHINE + "1 started here only, glyph pending, not counted: Issue-1"]
    assert not any("Issue-2" in t for t in texts(wip))


def test_wip_nothing_here_has_no_machine_part(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {"Issue-1": PROGRESS})
    wip = derive(leaves(("~", "Issue-1")), hist=history(repo), records=local({"Issue-1": PROGRESS})).wip
    assert machine_parts(wip) == [] and texts(wip)[-1] == thieves.LANES


def test_wip_absent_checklist_is_per_leaf(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {"Issue-2": PARKED})
    wip = derive(leaves(("~", "Issue-1"), ("~", "Issue-2")), hist=history(repo)).wip
    assert wip.value == 1 and wip.no_data is None
    assert "1 parked, not counted" in texts(wip)


def test_wip_committed_no_data():
    failed = NoData(GIT_FAILED.format(message="boom"))
    wip = derive(leaves(("~", "Issue-1"), (" ", "Issue-2"), ("~", "Issue-3")), hist=history(failed),
                 records=local({"Issue-1": PARKED, "Issue-2": PROGRESS})).wip
    assert wip.value == 2  # Issue-1 and Issue-3 counted; nothing subtracted, the pending one not counted
    assert texts(wip)[:2] == ["parked, not counted: no data: git failed: boom",
                              "started, glyph pending: no data: git failed: boom"]
    assert dict(wip.counts) == {"parked": None, "started": None}
    assert machine_parts(wip)[0] == (
        "as seen by this build machine: not checked against a commit (git failed: boom): 1 parked here, still "
        "counted: Issue-1; 1 started here, glyph pending, not counted: Issue-2")


def spy_reads(monkeypatch, listed, answers):
    """Patch committed_paths to return `listed` and committed_text to answer from `answers`; the calls."""
    calls = {"paths": [], "text": []}

    def paths(repo):
        calls["paths"].append(repo)
        return listed

    def text(repo, rel):
        calls["text"].append(rel)
        return answers[rel]

    monkeypatch.setattr(wbs_history, "committed_paths", paths)
    monkeypatch.setattr(wbs_history, "committed_text", text)
    return calls


FAKE_REPO = wbs_history.Repo(PurePath("/nowhere"), "", SITE, PurePath("/gantry"))


def test_wip_bad_issue_ids_make_no_read(monkeypatch):
    calls = spy_reads(monkeypatch, frozenset({"Issue-1/checklist.md", "a/checklist.md"}),
                      {"Issue-1/checklist.md": PARKED})
    data = leaves(("~", "Issue-1"), ("~", "Issue-2"), ("~", "Issue-3"), ("~", "Issue-4"), ("~", "Issue-5"),
                  ("~", "Issue-6"))
    bad = {"Issue-3": "../x", "Issue-4": "a/b", "Issue-5": "a\\b", "Issue-6": None}
    data = dataclasses.replace(data, leaves=tuple(dataclasses.replace(leaf, issue=bad.get(leaf.issue, leaf.issue))
                                                  for leaf in data.leaves))
    wip = derive(data, hist=history(FAKE_REPO)).wip
    assert len(calls["paths"]) == 1 and calls["text"] == ["Issue-1/checklist.md"]
    assert wip.value == 5 and "1 parked, not counted" in texts(wip)


def test_wip_mid_read_no_data_discards(monkeypatch):
    calls = spy_reads(monkeypatch, frozenset({"Issue-1/checklist.md", "Issue-2/checklist.md"}),
                      {"Issue-1/checklist.md": PARKED, "Issue-2/checklist.md": NoData("boom")})
    wip = derive(leaves(("~", "Issue-1"), (" ", "Issue-2")), hist=history(FAKE_REPO)).wip
    assert len(calls["text"]) == 2
    assert wip.value == 1
    assert dict(wip.counts) == {"parked": None, "started": None}
    assert texts(wip)[:2] == ["parked, not counted: no data: boom", "started, glyph pending: no data: boom"]
    assert machine_parts(wip)[0].startswith("as seen by this build machine: not checked against a commit (boom)")


def test_wip_committed_paths_no_data(monkeypatch):
    calls = spy_reads(monkeypatch, NoData("x"), {})
    wip = derive(leaves(("~", "Issue-1")), hist=history(FAKE_REPO)).wip
    assert calls["text"] == []
    assert dict(wip.counts) == {"parked": None, "started": None}
    assert texts(wip)[0] == "parked, not counted: no data: x"


def test_wip_history_failure_skips_committed_reads(monkeypatch):
    calls = spy_reads(monkeypatch, frozenset(), {})
    failed = NoData(GIT_FAILED.format(message="timed out"))
    wip = derive(leaves(("~", "Issue-1")), hist=history(FAKE_REPO, no_data=failed)).wip
    assert calls == {"paths": [], "text": []}
    assert texts(wip)[0] == "parked, not counted: no data: git failed: timed out"
    assert machine_parts(wip)[0].startswith(MACHINE + "not checked against a commit (git failed: timed out)")


def test_wip_unreadable_local_checklist(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {})
    wip = derive(leaves(("~", "Issue-1"), ("~", "Issue-3")), hist=history(repo),
                 records=local({}, unreadable=("Issue-3",))).wip
    (part,) = machine_parts(wip)
    assert "checklists here not read: 1 (Issue-3)" in part and thieves.HERE_NONE not in part
    failed = NoData("x")
    wip = derive(leaves(("~", "Issue-1"), ("~", "Issue-3")), hist=history(failed),
                 records=local({}, unreadable=("Issue-3",))).wip
    assert "checklists here not read: 1 (Issue-3)" in machine_parts(wip)[0]
    assert thieves.HERE_NONE not in machine_parts(wip)[0]
    wip = derive(leaves(("~", "Issue-1")), hist=history(failed)).wip
    assert machine_parts(wip)[0].endswith(": " + thieves.HERE_NONE)


def test_wip_lists_in_wbs_order_once(tmp_path, git_ready):
    repo = planning_repo(tmp_path, {})
    wip = derive(leaves(("~", "Issue-9"), ("~", "Issue-2"), ("~", "Issue-9"), ("~", "Issue-5")), hist=history(repo),
                 records=local({"Issue-9": PARKED, "Issue-2": PARKED, "Issue-5": PARKED})).wip
    assert machine_parts(wip)[0] == MACHINE + "4 parked here only, still counted: Issue-9, Issue-2, Issue-5"


def test_wip_lanes_no_data():
    wip = derive(leaves(("~", "Issue-1")), hist=history()).wip
    assert texts(wip)[-1] == thieves.LANES
    assert "no data: lane records are per-machine (mtibbits/devagent#748)" in thieves.LANES


# --- the uncommitted labels (AC2) ------------------------------------------------------------


def test_uncommitted_labels():
    data = leaves(("~", "Issue-1"))
    dirty = derive(data, hist=history(uncommitted=True))
    clean = derive(data, hist=history(uncommitted=False))
    assert dirty.wip.label == dirty.unknown_deps.label == thieves.UNCOMMITTED
    assert thieves.NEGLECT_UNCOMMITTED in texts(dirty.neglect)
    assert dirty.neglect.flagged == clean.neglect.flagged
    assert clean.wip.label is None and clean.unknown_deps.label is None
    assert thieves.NEGLECT_UNCOMMITTED not in texts(clean.neglect)
    unchecked = derive(data, hist=history(uncommitted=None,
                                          reason="no data: cannot check the working-tree `WBS.md`: x"))
    expected = thieves.NOT_CHECKED.format(reason="cannot check the working-tree `WBS.md`: x")
    assert unchecked.wip.label == unchecked.unknown_deps.label == expected
    no_data = derive(data, hist=history(no_data=NoData("shallow history")))
    assert no_data.wip.label == thieves.NOT_CHECKED.format(reason="shallow history")


def test_missing_history_and_records_fail_closed(monkeypatch):
    got = thieves.derive_thieves(result_of({SITE: leaves(("~", "Issue-1"))}), model_of(panel()), None,
                                 all_read(SITE), {}, {}, AS_OF)[SITE]
    assert got.neglect.text == "no data: history not read for this site" and got.neglect.flagged is False
    assert got.wip.label == thieves.NOT_CHECKED.format(reason=thieves.HISTORY_NOT_READ)
    assert machine_parts(got.wip)[0].endswith(": checklists here not read: records not read")
    not_read = SiteRecords(NOT_READ, "planning directory does not exist", {}, {})
    spy_reads(monkeypatch, frozenset(), {})
    got = derive(leaves(("~", "Issue-1")), hist=history(FAKE_REPO), records=not_read)
    assert machine_parts(got.wip)[0] == MACHINE + "checklists here not read: planning directory does not exist"
