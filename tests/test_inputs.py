"""Optional inputs joined onto a site: gantry/inputs.py (#10)."""

import shutil
from dataclasses import fields
from types import SimpleNamespace

from gantry.config import SiteOverride
from gantry.inputs import NO_TIERS, apply_inputs, has_tiers, scheduled, site_totals
from gantry.model import REPORT_KINDS, Estimate, Models, Totals
from gantry.sites import SiteEntry
from gantry.wbs_stopgap import parse_wbs
from sample_roles import BUSY, UNESTIMATED
from test_samples import ROOT, SAMPLES

INPUTS = ROOT / "tests" / "fixtures" / "inputs"


def run(tmp_path, wbs, estimates=INPUTS / "estimates.md", unit=6, configured=None):
    """apply_inputs over WBS.md text, with `estimates` copied in as a dated file."""
    folder = tmp_path / "plan"
    folder.mkdir(parents=True, exist_ok=True)
    if estimates is not None:
        shutil.copyfile(estimates, folder / "estimates-2026-01-01.md")
    sites = {} if configured is None else {"alpha": SiteOverride(None, configured)}
    config = SimpleNamespace(sites=sites, default_unit_hours=unit)
    return apply_inputs(SiteEntry("alpha", folder, None), parse_wbs(wbs, "alpha"), config)


def est_text():
    return (INPUTS / "est.md").read_bytes().decode("utf-8")


def leaf(got, key):
    return next(x for x in got.data.leaves if x.key == key)


def entries(got, kind):
    return [e for e in got.data.report if e.kind == kind]


def test_value_type_fields():
    assert [f.name for f in fields(Estimate)] == ["lo_h", "hi_h", "source", "est_kind", "effort"]
    assert [f.name for f in fields(Models)] == ["thinking", "checking", "source"]
    assert [f.name for f in fields(Totals)] == ["hours", "hours_lo", "hours_hi", "unestimated_count", "non_time"]


def test_time_est_beats_row_beats_default(tmp_path):
    got = run(tmp_path, est_text())
    a, d, j = leaf(got, "Issue-2").estimate, leaf(got, "Issue-5").estimate, leaf(got, "Issue-12").estimate
    assert (a.source, a.lo_h, a.hi_h, a.effort) == ("est", 3, 3, "M")
    assert (d.source, d.lo_h, d.hi_h) == ("estimates_file", 2, 4)
    assert (j.source, j.lo_h, j.hi_h, j.est_kind) == ("default", 6, 6, "absent")
    assert leaf(got, "Issue-3").estimate.lo_h == 9


def test_non_time_with_row_takes_row_and_stays_in_non_time(tmp_path):
    got = run(tmp_path, est_text())
    c = leaf(got, "Issue-4").estimate
    assert (c.source, c.lo_h, c.hi_h, c.est_kind) == ("estimates_file", 4, 8, "non_time")
    assert "Issue-4" not in {e.message.split("'")[1] for e in entries(got, "unestimated")}
    assert got.totals.non_time["pr"] == {"quantity": 50.5, "leaves": 2}


def test_unparseable_with_row_takes_row_and_is_listed(tmp_path):
    got = run(tmp_path, est_text())
    line = leaf(got, "Issue-5").line
    assert [e.kind for e in got.data.report if e.line == line] == ["est_unparseable"]
    assert entries(got, "est_unparseable")[0].message == (
        f"WBS.md line {line}: est '2-3d' on 'Issue-5' is not a time, a placeholder or a quantity "
        "with a unit; it is not used"
    )


def test_ranged_row_adds_midpoint_and_ends(tmp_path):
    got = run(tmp_path, "- [ ] D {issue: Issue-5, est: 2-3d}\n")
    assert got.totals == Totals(3, 2, 4, 0, {})


def test_est_fixture_totals(tmp_path):
    got = run(tmp_path, est_text())
    assert got.totals == Totals(80, 74, 86, 6, {"pr": {"quantity": 50.5, "leaves": 2}})


def test_cycle_leaves_in_no_total(tmp_path):
    text = est_text()
    cut = "\n".join(line for line in text.split("\n") if "Loop" not in line and "Waiter" not in line)
    assert run(tmp_path, text).totals == run(tmp_path / "b", cut).totals


def test_parent_row_est_in_no_total(tmp_path):
    text = est_text()
    assert run(tmp_path, text).totals == run(tmp_path / "b", text.replace(", est: 100h", "")).totals


def test_non_time_without_row_counts_default(tmp_path):
    got = run(tmp_path, "- [ ] H {issue: Issue-10, est: 48pr}\n")
    assert (got.totals.hours, got.totals.unestimated_count) == (6, 1)
    assert got.totals.non_time == {"pr": {"quantity": 48, "leaves": 1}}


def test_default_unit_follows_config(tmp_path):
    got = run(tmp_path, "- [ ] J {issue: Issue-12}\n", unit=2.5)
    assert (leaf(got, "Issue-12").estimate.lo_h, got.totals.hours) == (2.5, 2.5)


def test_unestimated_entries_one_per_leaf_with_class(tmp_path):
    got = run(tmp_path, est_text())
    found = entries(got, "unestimated")
    keys = [e.message.split("'")[1] for e in found]
    assert keys == [f"Issue-{n}" for n in range(7, 13)]
    whats = ["an unparseable est:"] * 3 + ["a non-time est:", "a placeholder est:", "no est:"]
    for e, key, what in zip(found, keys, whats):
        assert e.message == (f"WBS.md line {e.line}: {key!r} has {what} and no estimates-file hours; "
                             "it is scheduled at the default unit")


def test_models_flags_and_inherit(tmp_path):
    got = run(tmp_path, est_text())
    assert leaf(got, "Issue-21").models == Models("inherit", "sonnet", "estimates_file")
    assert leaf(got, "Issue-20").models == Models("opus", "opus", "estimates_file")
    assert leaf(got, "Issue-22").flags == "spike"
    assert leaf(got, "Issue-12").models is None


def test_non_leaf_and_finished_rows_ignored(tmp_path):
    got = run(tmp_path, est_text())
    assert [r.key for r in got.data.rows if r.key == "Issue-1"] == ["Issue-1"]
    assert leaf(got, "Issue-13").estimate is None
    assert not [e for e in got.data.report if "#1 " in e.message or "#13 " in e.message]


def test_unmatched_row_reported(tmp_path):
    got = run(tmp_path, est_text())
    lines = (INPUTS / "estimates.md").read_text(encoding="utf-8").split("\n")
    at = next(i for i, line in enumerate(lines, 1) if line.startswith("| #99 |"))
    assert [e.message for e in entries(got, "estimates_row_unmatched")] == [
        f"estimates file 'estimates-2026-01-01.md' line {at}: #99 names no WBS.md line",
    ]


def test_configured_missing_every_leaf_default(tmp_path):
    got = run(tmp_path, est_text(), configured=tmp_path / "plan" / "gone.md")
    assert [e.kind for e in got.data.report if e.line is None][:1] == ["estimates_unreadable"]
    assert got.estimates is None
    timed = {"Issue-2", "Issue-3"}
    assert {x.estimate.source for x in scheduled(got.data) if x.key not in timed} == {"default"}


def test_no_priority_counts_open_leaves(tmp_path):
    got = run(tmp_path, est_text())
    assert [(e.line, e.message) for e in entries(got, "no_priority")] == [
        (None, "14 open leaves have no priority and count as P3"),
    ]
    one = run(tmp_path / "b", "- [ ] J {issue: Issue-12}\n- [x] K {issue: Issue-13}\n")
    assert [e.message for e in entries(one, "no_priority")] == ["1 open leaf has no priority and counts as P3"]
    assert entries(run(tmp_path / "c", "- [ ] J {issue: Issue-12, priority: P2}\n"), "no_priority") == []


def wbs(site):
    return (SAMPLES / site / "WBS.md").read_bytes().decode("utf-8")


def sample(tmp_path, site):
    folder = tmp_path / site
    shutil.copytree(SAMPLES / site, folder)
    config = SimpleNamespace(sites={}, default_unit_hours=6)
    return apply_inputs(SiteEntry(site, folder, None), parse_wbs(wbs(site), site), config)


def test_no_model_tiers_entry_iff_no_tiers(tmp_path):
    quarry, harbor = sample(tmp_path, UNESTIMATED), sample(tmp_path, BUSY)
    assert [e.message for e in entries(quarry, "no_model_tiers")] == [NO_TIERS]
    assert entries(harbor, "no_model_tiers") == []
    assert has_tiers(scheduled(harbor.data)) and not has_tiers(scheduled(quarry.data))


def test_estimates_source_named(tmp_path):
    harbor = sample(tmp_path, BUSY)
    assert harbor.estimates.name.startswith("estimates-2026-01-15")
    assert str(harbor.estimates.date) == "2026-01-15"
    assert sample(tmp_path, UNESTIMATED).estimates is None


def test_report_stays_sorted(tmp_path):
    report = run(tmp_path, est_text()).data.report
    key = [(e.line is not None, e.line or 0, REPORT_KINDS.index(e.kind)) for e in report]
    assert key == sorted(key)


def test_every_entry_kind_is_known(tmp_path):
    for got in (run(tmp_path, est_text()), sample(tmp_path, UNESTIMATED), sample(tmp_path, BUSY)):
        assert {e.kind for e in got.data.report} <= set(REPORT_KINDS)


def test_site_totals_without_inputs_counts_every_leaf_default():
    data = parse_wbs(est_text(), "alpha")
    totals = site_totals(scheduled(data), 6)
    assert (totals.hours, totals.unestimated_count) == (6 * 16, 16)
