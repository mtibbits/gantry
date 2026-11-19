"""Optional inputs joined onto a site: gantry/inputs.py (#10)."""

import shutil
from dataclasses import fields
from types import SimpleNamespace

import pytest

import helpers_portfolio as hp
from gantry.config import SiteOverride
from gantry.inputs import NO_TIERS, apply_inputs, has_tiers, scheduled, site_totals
from gantry.model import REPORT_KINDS, Estimate, Models, Totals
from gantry.roots import directory_roots
from gantry.sites import SiteEntry
from sample_roles import BUSY, UNESTIMATED
from test_samples import ROOT, SAMPLES

INPUTS = ROOT / "tests" / "fixtures" / "inputs"


def run(tmp_path, data, estimates=INPUTS / "estimates.md", unit=6, configured=None):
    """apply_inputs over site `alpha`'s SiteData, with `estimates` copied in as a dated file."""
    folder = tmp_path / "plan"
    folder.mkdir(parents=True, exist_ok=True)
    if estimates is not None:
        shutil.copyfile(estimates, folder / "estimates-2026-01-01.md")
    sites = {} if configured is None else {"alpha": SiteOverride(None, configured)}
    config = SimpleNamespace(sites=sites, default_unit_hours=unit)
    return apply_inputs(directory_roots([folder]), SiteEntry("alpha", folder, None), data, config)


def est_text():
    return (INPUTS / "est.md").read_bytes().decode("utf-8")


def est_data():
    return hp.fixture_site("est")


def leaf(got, key):
    return next(x for x in got.data.leaves if x.key == key)


def entries(got, kind):
    return [e for e in got.data.report if e.kind == kind]


def test_value_type_fields():
    assert [f.name for f in fields(Estimate)] == ["lo_h", "hi_h", "source", "est_kind", "effort"]
    assert [f.name for f in fields(Models)] == ["thinking", "checking", "source"]
    assert [f.name for f in fields(Totals)] == ["hours", "hours_lo", "hours_hi", "unestimated_count", "non_time"]


def test_time_est_beats_row_beats_default(tmp_path):
    got = run(tmp_path, est_data())
    a, d, j = leaf(got, "Issue-2").estimate, leaf(got, "Issue-5").estimate, leaf(got, "Issue-12").estimate
    assert (a.source, a.lo_h, a.hi_h, a.effort) == ("est", 3, 3, "M")
    assert (d.source, d.lo_h, d.hi_h) == ("estimates_file", 2, 4)
    assert (j.source, j.lo_h, j.hi_h, j.est_kind) == ("default", 6, 6, "absent")
    assert leaf(got, "Issue-3").estimate.lo_h == 9


def test_non_time_with_row_takes_row_and_stays_in_non_time(tmp_path):
    got = run(tmp_path, est_data())
    c = leaf(got, "Issue-4").estimate
    assert (c.source, c.lo_h, c.hi_h, c.est_kind) == ("estimates_file", 4, 8, "non_time")
    assert "Issue-4" not in {e.message.split("'")[1] for e in entries(got, "unestimated")}
    assert got.totals.non_time["pr"] == {"quantity": 50.5, "leaves": 2}


def test_unparseable_with_row_takes_row_and_is_listed(tmp_path):
    got = run(tmp_path, est_data())
    line = leaf(got, "Issue-5").line
    assert [e.kind for e in got.data.report if e.line == line] == ["est_unparseable"]
    assert entries(got, "est_unparseable")[0].message == (
        f"WBS.md line {line}: est '2-3d' on 'Issue-5' is not a time, a placeholder or a quantity "
        "with a unit; it is not used"
    )


def test_ranged_row_adds_midpoint_and_ends(tmp_path):
    got = run(tmp_path, hp.site(hp.node("D", issue="Issue-5", est="2-3d")))
    assert got.totals == Totals(3, 2, 4, 0, {})


def test_est_fixture_totals(tmp_path):
    # A smoke test of the stopgap path (#84): est.md's totals from the raw text.
    from gantry.wbs_stopgap import parse_wbs

    got = run(tmp_path, parse_wbs(est_text(), "alpha"))
    assert got.totals == Totals(80, 74, 86, 6, {"pr": {"quantity": 50.5, "leaves": 2}})


def test_cycle_leaves_in_no_total(tmp_path):
    # est.md's lines 22-24: Loop one, Loop two and Waiter.
    cut = hp.site_of(hp.drop_lines(hp.fixture_doc("est"), 22, 23, 24))
    assert run(tmp_path, est_data()).totals == run(tmp_path / "b", cut).totals


def test_parent_row_est_in_no_total(tmp_path):
    doc = hp.fixture_doc("est")
    for node in doc["projects"][0]["nodes"]:
        if node["meta"].get("est") == "100h":
            del node["meta"]["est"]
    assert run(tmp_path, est_data()).totals == run(tmp_path / "b", hp.site_of(doc)).totals


def test_non_time_without_row_counts_default(tmp_path):
    got = run(tmp_path, hp.site(hp.node("H", issue="Issue-10", est="48pr")))
    assert (got.totals.hours, got.totals.unestimated_count) == (6, 1)
    assert got.totals.non_time == {"pr": {"quantity": 48, "leaves": 1}}


def test_default_unit_follows_config(tmp_path):
    got = run(tmp_path, hp.site(hp.node("J", issue="Issue-12")), unit=2.5)
    assert (leaf(got, "Issue-12").estimate.lo_h, got.totals.hours) == (2.5, 2.5)


def test_unestimated_entries_one_per_leaf_with_class(tmp_path):
    got = run(tmp_path, est_data())
    found = entries(got, "unestimated")
    keys = [e.message.split("'")[1] for e in found]
    assert keys == [f"Issue-{n}" for n in range(7, 13)]
    whats = ["an unparseable est:"] * 3 + ["a non-time est:", "a placeholder est:", "no est:"]
    for e, key, what in zip(found, keys, whats):
        assert e.message == (f"WBS.md line {e.line}: {key!r} has {what} and no estimates-file hours; "
                             "it is scheduled at the default unit")


def test_models_flags_and_inherit(tmp_path):
    got = run(tmp_path, est_data())
    assert leaf(got, "Issue-21").models == Models("inherit", "sonnet", "estimates_file")
    assert leaf(got, "Issue-20").models == Models("opus", "opus", "estimates_file")
    assert leaf(got, "Issue-22").flags == "spike"
    assert leaf(got, "Issue-12").models is None


def test_non_leaf_and_finished_rows_ignored(tmp_path):
    got = run(tmp_path, est_data())
    assert [r.key for r in got.data.rows if r.key == "Issue-1"] == ["Issue-1"]
    assert leaf(got, "Issue-13").estimate is None
    assert not [e for e in got.data.report if "#1 " in e.message or "#13 " in e.message]


def test_unmatched_row_reported(tmp_path):
    got = run(tmp_path, est_data())
    lines = (INPUTS / "estimates.md").read_text(encoding="utf-8").split("\n")
    at = next(i for i, line in enumerate(lines, 1) if line.startswith("| #99 |"))
    assert [e.message for e in entries(got, "estimates_row_unmatched")] == [
        f"estimates file 'estimates-2026-01-01.md' line {at}: #99 names no WBS.md line",
    ]


def zero_row(tmp_path, cell):
    """An estimates file whose one row, on line 3, has `cell` as its id."""
    path = tmp_path / "e.md"
    path.write_bytes(("| # | effort | hours | thinking | checking | flags |\n|---|---|---|---|---|---|\n"
                      f"| {cell} | S | 2 | | | |\n").encode("utf-8"))
    return path


@pytest.mark.parametrize("cell, expected", [
    ("#012", "estimates file 'estimates-2026-01-01.md' line 3: #012 names no WBS.md line; it has a leading zero, "
             "so it is read as Issue-012, not Issue-12"),
    ("#00", "estimates file 'estimates-2026-01-01.md' line 3: #00 names no WBS.md line; it has a leading zero, "
            "so it is read as Issue-00, not Issue-0"),
    ("#0", "estimates file 'estimates-2026-01-01.md' line 3: #0 names no WBS.md line"),
], ids=["#012", "#00", "#0"])
def test_leading_zero_unmatched_names_the_zero(tmp_path, cell, expected):
    # The zero is kept, never normalised away; one digit is not a leading zero (#108).
    got = run(tmp_path, hp.site(hp.node("A", issue="Issue-12")), estimates=zero_row(tmp_path, cell))
    assert [e.message for e in entries(got, "estimates_row_unmatched")] == [expected]
    assert leaf(got, "Issue-12").estimate.source == "default"


def test_leading_zero_row_joins_literal_issue(tmp_path):
    got = run(tmp_path, hp.site(hp.node("B", issue="Issue-012")), estimates=zero_row(tmp_path, "#012"))
    assert entries(got, "estimates_row_unmatched") == []
    est = leaf(got, "Issue-012").estimate
    assert (est.lo_h, est.hi_h, est.source) == (2, 2, "estimates_file")


def test_tableless_later_file_does_not_fall_back(tmp_path):
    # The later file with no `#` table wins discovery, so the earlier file's
    # rows are not used in its place (#108).
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "estimates-2026-03-01-notes.md").write_bytes(b"Notes only.\n")
    got = run(tmp_path, est_data())
    assert [e.kind for e in got.data.report if e.line is None][:2] == ["estimates_ignored", "estimates_no_table"]
    assert got.estimates is None
    timed = {"Issue-2", "Issue-3"}
    assert {x.estimate.source for x in scheduled(got.data) if x.key not in timed} == {"default"}


def test_configured_missing_every_leaf_default(tmp_path):
    got = run(tmp_path, est_data(), configured=tmp_path / "plan" / "gone.md")
    assert [e.kind for e in got.data.report if e.line is None][:1] == ["estimates_unreadable"]
    assert got.estimates is None
    timed = {"Issue-2", "Issue-3"}
    assert {x.estimate.source for x in scheduled(got.data) if x.key not in timed} == {"default"}


def test_no_priority_counts_open_leaves(tmp_path):
    got = run(tmp_path, est_data())
    assert [(e.line, e.message) for e in entries(got, "no_priority")] == [
        (None, "14 open leaves have no priority and count as P3"),
    ]
    one = run(tmp_path / "b", hp.site(hp.node("J", issue="Issue-12"), hp.node("K", glyph="x", issue="Issue-13")))
    assert [e.message for e in entries(one, "no_priority")] == ["1 open leaf has no priority and counts as P3"]
    assert entries(run(tmp_path / "c", hp.site(hp.node("J", issue="Issue-12", priority="P2"))), "no_priority") == []


def sample(tmp_path, site):
    folder = tmp_path / site
    shutil.copytree(SAMPLES / site, folder)
    config = SimpleNamespace(sites={}, default_unit_hours=6)
    return apply_inputs(directory_roots([folder]), SiteEntry(site, folder, None), hp.sample_site(site), config)


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
    report = run(tmp_path, est_data()).data.report
    key = [(e.line is not None, e.line or 0, REPORT_KINDS.index(e.kind)) for e in report]
    assert key == sorted(key)


def test_every_entry_kind_is_known(tmp_path):
    for got in (run(tmp_path, est_data()), sample(tmp_path, UNESTIMATED), sample(tmp_path, BUSY)):
        assert {e.kind for e in got.data.report} <= set(REPORT_KINDS)


def test_site_totals_without_inputs_counts_every_leaf_default():
    data = est_data()
    totals = site_totals(scheduled(data), 6)
    assert (totals.hours, totals.unestimated_count) == (6 * 16, 16)
