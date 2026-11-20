"""The five time thieves (#29), derived as values from constructed inputs.
The page's row is in tests/test_strategic_page.py; the build's wiring in
tests/test_build.py."""

import ast
import dataclasses
import os
from datetime import date, timedelta
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
                                 thieves.thresholds(portfolio), fires if fires is not None else all_read(SITE),
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
    failed = NoData(GIT_FAILED.format(message="boom"), git=True)
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

    def paths(repo, name):
        assert name == "checklist.md"
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
    failed = NoData(GIT_FAILED.format(message="timed out"), git=True)
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
    got = thieves.derive_thieves(result_of({SITE: leaves(("~", "Issue-1"))}), model_of(panel()),
                                 thieves.thresholds(None), all_read(SITE), {}, {}, AS_OF)[SITE]
    assert got.neglect.text == "no data: history not read for this site" and got.neglect.flagged is False
    assert got.wip.label == thieves.NOT_CHECKED.format(reason=thieves.HISTORY_NOT_READ)
    assert machine_parts(got.wip)[0].endswith(": checklists here not read: records not read")
    not_read = SiteRecords(NOT_READ, "planning directory does not exist", {}, {})
    spy_reads(monkeypatch, frozenset(), {})
    got = derive(leaves(("~", "Issue-1")), hist=history(FAKE_REPO), records=not_read)
    assert machine_parts(got.wip)[0] == MACHINE + "checklists here not read: planning directory does not exist"


# --- unknown dependencies (AC3) ----------------------------------------------------------------


def report_entry(line, kind):
    from gantry.model import ReportEntry

    return ReportEntry(SITE, line, kind, f"line {line}: {kind}")


def deps_site():
    """Line 1 an open row holding an unresolved entry; line 2 an open leaf under it with an unresolved and a
    malformed entry; line 3 a done leaf with an unresolved entry; line 4 an open leaf with two identical entries."""
    return hp.site(
        hp.node("Epic", hp.node("A", glyph="~", issue="Issue-1", depends_on=["nope", "bad:x:y"]), depends_on="gone"),
        hp.node("Done", glyph="x", issue="Issue-3", depends_on="nope2"),
        hp.node("B", issue="Issue-4", depends_on=["twice", "twice"]))


def test_unknown_deps_open_leaves_only():
    data = deps_site()
    lines = sorted(e.line for e in data.report if e.kind == "unresolved_ref")
    assert lines == [1, 2, 3, 4, 4], lines
    deps = derive(data, hist=history()).unknown_deps
    assert (deps.value, deps.flagged, deps.threshold, deps.no_data) == (5, True, 0, None)
    assert dict(deps.by_kind) == {"unresolved_ref": 4, "malformed_ref": 1, "foreign_site_absent": 0,
                                  "foreign_unresolved": 0}
    assert deps.text == "5" and deps.source == thieves.SOURCE_DEPS
    assert "4 unresolved_ref, 1 malformed_ref" in texts(deps)
    assert thieves.CROSS_SITE_NONE in texts(deps)
    calm = derive(hp.site(hp.node("A", issue="Issue-1")), hist=history()).unknown_deps
    assert (calm.value, calm.flagged) == (0, False) and texts(calm) == [thieves.CROSS_SITE_NONE]


def test_unknown_deps_cross_site():
    from gantry.model import ForeignOutcome

    data = deps_site()
    data = dataclasses.replace(data, report=data.report + (report_entry(2, "foreign_site_absent"),
                                                           report_entry(4, "foreign_unresolved"),
                                                           report_entry(3, "foreign_unresolved")))

    def outcome(line, status):
        return ForeignOutcome(SITE, f"k{line}", line, False, "beta:Issue-9", "beta", "Issue-9", status)

    resolved = (outcome(2, "open"), outcome(4, "satisfied"), outcome(3, "open"), outcome(2, "absent"))
    base = derive(data, hist=history())
    deps = derive(data, hist=history(), foreign={SITE: resolved}).unknown_deps
    assert dict(deps.by_kind)["foreign_site_absent"] == 1 and dict(deps.by_kind)["foreign_unresolved"] == 1
    assert "1 foreign_site_absent, 1 foreign_unresolved" in texts(deps)[0]
    assert "resolved cross-site links: 2, not flagged" in texts(deps)
    assert (deps.value, deps.flagged) == (base.unknown_deps.value, base.unknown_deps.flagged) == (7, True)
    assert thieves.CROSS_SITE_NONE in texts(base.unknown_deps)


# --- unplanned work (AC3) ----------------------------------------------------------------------


def fire(*kinds, site=SITE):
    return Fire(kinds=kinds, why=tuple("why" for _ in kinds), site=site)


def report(fires=(), **statuses):
    """A FireReport: every source read for SITE, except `statuses` ({source: status}; fires_list is site-less)."""
    out = []
    for source in ("fires_list", "p0_leaf", "stuck"):
        status = statuses.get(source, "read")
        out.append(SourceStatus(source, None if source == "fires_list" else SITE, status,
                                None if status in ("read", "absent") else "r"))
    return FireReport(tuple(fires), tuple(out))


def test_unplanned_counts_every_fire():
    fires = report([fire("p0_leaf"), fire("p0_leaf", "stuck"), fire("fires_list", site="beta"),
                    fire("fires_list", site=None)])
    unplanned = derive(leaves(), hist=history(), fires=fires).unplanned
    assert (unplanned.value, unplanned.text, unplanned.flagged, unplanned.threshold) == (2, "2", True, 0)
    assert machine_parts(unplanned) == [MACHINE + "1 from STUCK markers"]
    assert unplanned.source == thieves.SOURCE_UNPLANNED


def test_unplanned_incomplete():
    got = derive(leaves(), hist=history(), fires=report([fire("p0_leaf"), fire("p0_leaf")], fires_list="not_read"))
    assert (got.unplanned.text, got.unplanned.value, got.unplanned.flagged) == (
        "2, incomplete: fires list not read", 2, True)
    for status in ("not_read", "read_with_problems"):
        got = derive(leaves(), hist=history(), fires=report(p0_leaf=status)).unplanned
        assert (got.text, got.value, got.flagged) == ("incomplete: P0 leaves not read", None, False), status
    got = derive(leaves(), hist=history(), fires=report(p0_leaf="absent")).unplanned
    assert (got.text, got.value) == ("0", 0)
    got = derive(leaves(), hist=history(), fires=report(fires_list="not_read", stuck="not_read")).unplanned
    assert got.text == "incomplete: fires list, STUCK markers not read"
    other = FireReport((), (SourceStatus("p0_leaf", "beta", "not_read", "r"),) + report().sources)
    assert derive(leaves(), hist=history(), fires=other).unplanned.text == "0"
    got = thieves.derive_thieves(result_of({SITE: leaves()}), model_of(panel()), thieves.thresholds(None), None,
                                 {SITE: local({})},
                                 {SITE: history()}, AS_OF)[SITE].unplanned
    assert (got.text, got.value) == ("incomplete: fires step not read", None)


RULED = {"read": ("0", False), "read_with_problems": ("incomplete: fires list not read", False),
         "absent": ("0", False), "not_read": ("incomplete: fires list not read", False),
         "not_configured": ("0", True)}


def test_unplanned_rules_every_status():
    from gantry import fires as fires_module

    assert tuple(RULED) == fires_module.STATUSES
    for status, (text, unconfigured) in RULED.items():
        got = derive(leaves(), hist=history(), fires=report(fires_list=status)).unplanned
        assert got.text == text, status
        assert got.source.endswith("; fires list not configured") is unconfigured, status


def test_unplanned_stuck_source_not_read():
    got = derive(leaves(), hist=history(), fires=report([fire("p0_leaf")], stuck="not_read")).unplanned
    assert machine_parts(got) == [MACHINE + "STUCK markers not read"]
    assert not any("from STUCK markers" in t for t in texts(got))
    got = derive(leaves(), hist=history(), fires=report()).unplanned
    assert machine_parts(got) == [MACHINE + "0 from STUCK markers"]


def test_fires_without_site():
    fires = report([fire("fires_list", site=None), fire("fires_list", site=None), fire("p0_leaf")])
    assert thieves.fires_without_site(fires) == thieves.NoSiteFires(2, "2", thieves.SOURCE_FIRES_NO_SITE)
    assert derive(leaves(), hist=history(), fires=fires).unplanned.value == 1
    got = thieves.fires_without_site(report(fires_list="not_configured"))
    assert (got.count, got.text) == (None, "no data: no fires list configured")
    got = thieves.fires_without_site(report([fire("fires_list", site=None)], fires_list="read_with_problems"))
    assert (got.count, got.text) == (1, "1, incomplete: fires list not read")
    got = thieves.fires_without_site(report(fires_list="not_read"))
    assert (got.count, got.text) == (None, "incomplete: fires list not read")
    got = thieves.fires_without_site(None)
    assert (got.count, got.text) == (None, "incomplete: fires step not read")


# --- conflicting priorities (AC3) --------------------------------------------------------------


def test_priorities_take_the_panel_gap():
    from gantry.strategic import NO_GAP_NO_OPEN_WORK, NO_GAP_NO_SHARE, NO_GAP_SHARES_ZERO

    data = leaves()
    got = derive(data, hist=history(), gap=33.0).priorities
    assert (got.value, got.text, got.flagged, got.threshold) == (33.0, "33 points, backlog basis", True, 10)
    assert got.source == thieves.SOURCE_PRIORITIES
    assert derive(data, hist=history(), gap=12.4).priorities.text == "12 points, backlog basis"
    assert derive(data, hist=history(), gap=12.4).priorities.flagged
    for gap in (10.0, 10.0 + 1e-12, 9.6):
        assert derive(data, hist=history(), gap=gap).priorities.flagged is False, gap
    assert derive(data, hist=history(), gap=10.01).priorities.flagged is True
    got = derive(data, hist=history(), gap=10.4).priorities
    assert (got.text, got.flagged) == ("10 points, backlog basis", True)
    assert derive(data, hist=history(), gap=12.5).priorities.text == "13 points, backlog basis"
    for reason in (NO_GAP_NO_SHARE, NO_GAP_NO_OPEN_WORK, NO_GAP_SHARES_ZERO):
        got = derive(data, hist=history(), gap_reason=reason).priorities
        assert (got.text, got.flagged, got.no_data, got.value) == (f"no data: {reason}", False, reason, None)


# --- neglected work (AC4) ----------------------------------------------------------------------


def neglect(day, *, start=False, weeks=None):
    portfolio = None
    if weeks is not None:
        portfolio = SimpleNamespace(thresholds=SimpleNamespace(neglect_weeks=weeks))
    return derive(leaves(), hist=history(day=day, start=start), portfolio=portfolio).neglect


def test_neglect_window_boundary():
    day = AS_OF - timedelta(days=27)
    got = neglect(day)
    assert (got.text, got.flagged, got.value, got.threshold) == (f"last state change {day.isoformat()}", False, day, 4)
    assert neglect(AS_OF - timedelta(days=28)).flagged is True
    assert neglect(AS_OF - timedelta(days=13), weeks=2).flagged is False
    assert neglect(AS_OF - timedelta(days=14), weeks=2).flagged is True
    assert neglect(AS_OF).flagged is False


def test_neglect_no_change():
    got = neglect(None)
    assert (got.text, got.flagged, got.no_data) == ("no state change in 4 weeks", True, None)


def test_neglect_history_start():
    day = AS_OF - timedelta(days=10)
    got = neglect(day, start=True)
    assert (got.text, got.flagged) == (f"no state change since history began {day.isoformat()}", False)


def test_neglect_no_data_each_reason():
    for template in wbs_history.REASONS:
        reason = template.format(as_of="2026-01-19", message="m", reason="r")
        got = derive(leaves(), hist=history(no_data=NoData(reason))).neglect
        assert (got.text, got.flagged, got.no_data, got.value) == (f"no data: {reason}", False, reason, None)
        assert got.source == thieves.SOURCE_NEGLECT


# --- thresholds, an empty site, isolation, attach ----------------------------------------------


def test_thresholds_from_portfolio_file():
    from gantry.portfolio_file import DEFAULTS, read_portfolio_file

    fixtures = ROOT / "tests" / "fixtures" / "thieves"
    portfolio = read_portfolio_file(directory_roots([fixtures]), fixtures / "thresholds.md", br_namespace="life")
    assert thieves.thresholds(portfolio) == {"wip": 0, "unknown_deps": 5, "unplanned": 3, "priorities": 50,
                                             "neglect": 1}
    assert thieves.thresholds(None) == {kind: DEFAULTS[f"thresholds.{key}"]
                                        for kind, key in thieves.THRESHOLD_KEYS.items()}
    data = hp.site(hp.node("A", glyph="~", issue="Issue-1", depends_on="nope"))
    hist = history(day=AS_OF - timedelta(days=10))
    fires = report([fire("p0_leaf")])
    default = derive(data, hist=hist, fires=fires, gap=20.0)
    custom = derive(data, hist=hist, fires=fires, gap=20.0, portfolio=portfolio)
    for kind in thieves.KINDS:
        assert getattr(default, kind).flagged is not getattr(custom, kind).flagged, kind


def test_empty_site():
    got = derive(hp.site(), hist=history())
    assert [(got.wip.value, got.wip.flagged), (got.unknown_deps.value, got.unknown_deps.flagged),
            (got.unplanned.value, got.unplanned.flagged)] == [(0, False), (0, False), (0, False)]


def test_failing_site_is_isolated(monkeypatch):
    real = thieves.wip_thief

    def wip(data, history, records, threshold):
        if not data.leaves:
            raise RuntimeError("boom")
        return real(data, history, records, threshold)

    monkeypatch.setattr(thieves, "wip_thief", wip)
    result = result_of({"alpha": leaves(("~", "Issue-1")), "beta": hp.site()})
    got = thieves.derive_thieves(result, model_of(panel("alpha"), panel("beta")), thieves.thresholds(None),
                                 all_read("alpha", "beta"), {}, {}, AS_OF)
    for kind in thieves.KINDS:
        thief = getattr(got["beta"], kind)
        assert (thief.text, thief.flagged) == ("no data: thieves not derived: RuntimeError", False), kind
        assert thief.source
    assert (got["beta"].failed, got["alpha"].failed) == ("RuntimeError", None)
    assert got["alpha"].wip.value == 1


def test_attach_repoints_headline():
    alpha, beta = panel("alpha", gap=20.0), panel("beta", gap=5.0)
    model = model_of(alpha, beta, headline=alpha)
    result = result_of({"alpha": leaves(), "beta": leaves()})
    by_site = thieves.derive_thieves(result, model, thieves.thresholds(None), all_read("alpha", "beta"), {}, {},
                                    AS_OF)
    no_site = thieves.fires_without_site(all_read())
    attached = thieves.attach(model, by_site, no_site)
    assert attached.headline is attached.panels["alpha"]
    assert attached.headline.thieves is by_site["alpha"]
    assert attached.panels["beta"].thieves is by_site["beta"]
    assert attached.fires_no_site is no_site
    unbuilt = model_of(panel("alpha", built=False))
    assert thieves.attach(unbuilt, {}, no_site).panels["alpha"].thieves is None


# --- final review fixes ----------------------------------------------------------------------------


def test_wip_shallow_or_no_commit_history_still_reads_head(tmp_path, git_ready):
    """The committed check reads HEAD only: shallow history (or no commit in the window) never turns it off."""
    repo = planning_repo(tmp_path, {"Issue-1": PARKED, "Issue-2": PROGRESS})
    for reason in (wbs_history.SHALLOW, wbs_history.NO_COMMITS.format(as_of="2026-01-19")):
        wip = derive(leaves(("~", "Issue-1"), (" ", "Issue-2")), hist=history(repo, no_data=NoData(reason))).wip
        assert wip.value == 1, reason
        assert dict(wip.counts) == {"parked": 1, "started": 1}, reason
        assert texts(wip)[:2] == ["1 parked, not counted", "1 started, glyph pending"], reason


def test_wip_unborn_head_reads_numbers(tmp_path, git_ready):
    """A planning repository with no commit yet has no committed checklist: counts, not no data."""
    top = make_repo(tmp_path / "planning")
    gantry = PurePath(os.path.realpath(make_repo(tmp_path / "gantry-checkout")))
    repo = wbs_history.history_repository(directory_roots([tmp_path]), top, gantry, key="devdoc_dir", site=SITE)
    no_commits = NoData(wbs_history.NO_COMMITS.format(as_of="2026-01-19"))
    wip = derive(leaves(("~", "Issue-1"), (" ", "Issue-2")), hist=history(repo, no_data=no_commits)).wip
    assert (wip.value, dict(wip.counts)) == (1, {"parked": 0, "started": 0})


def test_unplanned_stuck_part_per_status():
    from gantry import fires as fires_module

    shown = {"read": True, "read_with_problems": True, "absent": True, "not_read": False, "not_configured": True}
    assert tuple(shown) == fires_module.STATUSES
    for status, counted in shown.items():
        got = derive(leaves(), hist=history(), fires=report([fire("stuck")], stuck=status)).unplanned
        expected = MACHINE + ("1 from STUCK markers" if counted else "STUCK markers not read")
        assert machine_parts(got) == [expected], status
    no_status = FireReport((fire("stuck"),), tuple(s for s in report().sources if s.source != "stuck"))
    got = derive(leaves(), hist=history(), fires=no_status).unplanned
    assert machine_parts(got) == [MACHINE + "STUCK markers not read"]
