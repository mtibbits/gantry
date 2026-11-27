"""Optional inputs joined onto a site: gantry/inputs.py (#10)."""

import shutil
from dataclasses import fields
from types import SimpleNamespace

import pytest

import helpers_portfolio as hp
from gantry.config import SiteOverride
from gantry.inputs import MESSAGES, NO_TIERS, NO_TIERS_SCHEMA, apply_inputs, has_tiers, scheduled, site_totals
from gantry.model import REPORT_KINDS, Estimate, Models, Totals
from gantry.roots import directory_roots
from gantry.sites import SiteEntry
from sample_roles import BUSY, OWN_UNIT, UNESTIMATED
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
    assert [f.name for f in fields(Models)] == ["thinking", "checking", "source", "classes"]
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
    # est.md's totals, from devagent's reading of it (written.json; #12 restated it off the stopgap).
    got = run(tmp_path, est_data())
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
    # harbor's WBS.md keys (#14): Issue-6 and Issue-14 both classes, Issue-7 thinking only.
    assert leaf(harbor, "Issue-6").models == Models("opus", "fable", "wbs", ("wbs", "wbs"))
    assert leaf(harbor, "Issue-14").models == Models("sonnet", "sonnet", "wbs", ("wbs", "wbs"))
    assert leaf(harbor, "Issue-7").models == Models("opus", None, "wbs", ("wbs", None))
    rest = {x.key: x.models.source for x in harbor.data.leaves
            if x.open and x.models is not None and x.key not in ("Issue-6", "Issue-14", "Issue-7")}
    assert rest and set(rest.values()) == {"estimates_file"}
    # 8 open leaves with a row, less the 3 with tiers in the JSON.
    assert [e.message for e in entries(harbor, "models_from_estimates_file")] == [
        "model tiers from the estimates file: 5 leaves"]
    orchard = sample(tmp_path, OWN_UNIT)
    assert [e.message for e in entries(orchard, "no_model_tiers")] == [NO_TIERS]


def test_sample_project_source_shows_nothing():
    # orchard's step_models table (samples/devagent/config.toml) gives every class a project source,
    # and orchard has no wbs class: a project class alone is not a tier (#14), so no leaf has one.
    doc = hp.sample_doc()
    (orchard,) = [p for p in doc["projects"] if p["project"] == OWN_UNIT]
    classes = [n["models"][cls] for n in orchard["nodes"] if n["leaf"] for cls in ("thinking", "checking")]
    assert classes and all(c is not None and c["source"] == "project" for c in classes)
    assert all(x.models is None for x in hp.sample_site(OWN_UNIT).leaves)


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


def test_est_unparseable_text_for_list_value(tmp_path):
    """A list est: devagent parsed is unparseable; the message prints the stored value (#12, item 2)."""
    got = run(tmp_path, hp.site(hp.node("C", issue="Issue-3", est=["2h", "3h"])), estimates=None)
    assert entries(got, "est_unparseable")[0].message == (
        "WBS.md line 1: est ['2h', '3h'] on 'Issue-3' is not a time, a placeholder or a quantity with a unit; "
        "it is not used")


# --- model tiers from the portfolio JSON, the estimates file a per-leaf fallback (#14) ------------

MODELS_ESTIMATES = INPUTS / "models-estimates.md"
# tests/fixtures/portfolio/models.md's leaves by title, and the rows models-estimates.md gives them.
KEYS = {"Both": "Issue-2", "Think only": "Issue-3", "Bad alone": "Issue-4", "Bad beside legal": "Issue-5",
        "Inherit": "Issue-6", "Done keyed": "Issue-7", "No keys": "Issue-8"}
ROWS = {"Issue-2": ("XS", "haiku", "haiku", "f2"), "Issue-3": ("S", "opus", "haiku", "f3"),
        "Issue-4": ("M", "haiku", "sonnet", "f4"), "Issue-5": ("L", "sonnet", "sonnet", "f5"),
        "Issue-6": ("XL", "opus", "opus", "f6"), "Issue-8": ("S", None, None, "f8")}


def models_doc(name="models", minor=None):
    """written.json's project `name` (#14) alone, as site alpha; `minor` sets schema_version."""
    doc = hp.fixture_doc(name)
    if minor is not None:
        doc["schema_version"] = minor
    return doc


def models_run(tmp_path, doc=None, estimates=MODELS_ESTIMATES):
    return run(tmp_path, hp.site_of(models_doc() if doc is None else doc), estimates=estimates)


def row_models(key):
    _, thinking, checking, _ = ROWS[key]
    return Models(thinking, checking, "estimates_file")


def test_json_tiers_win_per_leaf(tmp_path):
    got = models_run(tmp_path)
    # Neither takes the row's tiers, nor the class the row has and the leaf lacks.
    assert leaf(got, "Issue-2").models == Models("fable", "sonnet", "wbs", ("wbs", "wbs"))
    assert leaf(got, "Issue-3").models == Models("sonnet", None, "wbs", ("wbs", None))
    assert leaf(got, "Issue-5").models == Models(None, "fable", "wbs", (None, "wbs"))


def test_project_class_beside_wbs_is_kept(tmp_path):
    got = models_run(tmp_path, models_doc("models_project"))
    assert leaf(got, "Issue-3").models == Models("sonnet", "opus", "wbs", ("wbs", "project"))


def test_inherit_in_json_blocks_the_row(tmp_path):
    assert ROWS["Issue-6"][1:3] == ("opus", "opus")
    assert leaf(models_run(tmp_path), "Issue-6").models == Models("inherit", None, "wbs", ("wbs", None))


def test_leaves_without_json_tiers_take_the_row(tmp_path):
    variants = {
        "project-only": hp.set_models(models_doc(), "alpha", 2, thinking=("opus", "project"),
                                      checking=("sonnet", "project")),
        "config-only": hp.set_models(models_doc(), "alpha", 2, thinking=("opus", "config"), checking=None),
        "no models field": hp.set_models(models_doc(), "alpha", 2, thinking=hp.ABSENT, checking=hp.ABSENT),
    }
    for what, doc in variants.items():
        if what == "no models field":
            del next(n for n in doc["projects"][0]["nodes"] if n["source_line"] == 2)["models"]
        got = leaf(models_run(tmp_path / what.replace(" ", "-"), doc), "Issue-2").models
        assert got == row_models("Issue-2") and got.classes is None, what
    # Bad alone: its thinking key is malformed; checking is null, or from the project.
    assert leaf(models_run(tmp_path / "m"), "Issue-4").models == row_models("Issue-4")
    assert leaf(models_run(tmp_path / "p", models_doc("models_project")), "Issue-4").models == row_models("Issue-4")


def test_project_only_without_row_has_no_tiers(tmp_path):
    project = models_doc("models_project")
    assert leaf(models_run(tmp_path, project), "Issue-8").models is None  # its row's tier cells are blank
    assert leaf(models_run(tmp_path / "b", project, estimates=None), "Issue-4").models is None  # no row at all


def test_effort_and_flags_from_every_row(tmp_path):
    for name in ("models", "models_project"):
        got = models_run(tmp_path / name, models_doc(name))
        for key, (effort, _, _, flags) in ROWS.items():
            assert (leaf(got, key).estimate.effort, leaf(got, key).flags) == (effort, flags), (name, key)


def fallback_count(got):
    return [e.message for e in entries(got, "models_from_estimates_file")]


def test_models_from_estimates_file_count(tmp_path):
    got = models_run(tmp_path)
    # Bad alone only: No keys falls back too, but its row's tier cells are blank.
    assert fallback_count(got) == [MESSAGES["models_from_estimates_file_one"]]
    assert MESSAGES["models_from_estimates_file_one"] == "model tiers from the estimates file: 1 leaf"
    assert leaf(got, "Issue-8").models is None
    assert [e.line for e in entries(got, "models_from_estimates_file")] == [None]
    three = models_doc()
    for line in (3, 6):  # Think only and Inherit lose their wbs classes
        hp.set_models(three, "alpha", line, thinking=None, checking=None)
    assert fallback_count(models_run(tmp_path / "three", three)) == [
        MESSAGES["models_from_estimates_file"].format(n=3)] == ["model tiers from the estimates file: 3 leaves"]
    every = hp.set_models(models_doc(), "alpha", 4, thinking=("opus", "wbs"), checking=None)
    assert fallback_count(models_run(tmp_path / "every", every)) == []
    # Held-out leaves count too: the count is over open leaves, not the scheduled ones.
    cycle = models_doc()
    at = {n["source_line"]: n for p in cycle["projects"] if p["project"] == "alpha" for n in p["nodes"]}
    for line, other in ((4, 8), (8, 4)):  # Bad alone and No keys wait on each other
        at[line]["meta"]["depends_on"] = [at[other]["issue"]]
    held = models_run(tmp_path / "held", cycle)
    assert "Issue-4" in held.data.held_out and "Issue-4" not in [x.key for x in scheduled(held.data)]
    assert fallback_count(held) == [MESSAGES["models_from_estimates_file_one"]]


def test_no_model_tiers_message_by_schema(tmp_path):
    def tiers_entries(doc, estimates=None, where="x"):
        return [e.message for e in entries(models_run(tmp_path / where, doc, estimates), "no_model_tiers")]

    assert tiers_entries(hp.fixture_doc("est"), where="est") == [NO_TIERS]
    assert NO_TIERS_SCHEMA == NO_TIERS + ("; the portfolio document's schema version carries no model tiers "
                                          "(they arrive in 1.2)")
    assert tiers_entries(hp.fixture_doc("est") | {"schema_version": "1.1"}, where="est11") == [NO_TIERS_SCHEMA]
    assert tiers_entries(models_doc(), where="m12") == []
    assert tiers_entries(models_doc(minor="1.1"), where="m11") == [NO_TIERS_SCHEMA]
    got = models_run(tmp_path / "m11e", models_doc(minor="1.1"))
    assert entries(got, "no_model_tiers") == []
    assert {x.models.source for x in got.data.leaves if x.models is not None} == {"estimates_file"}
    assert sum(x.models is not None for x in got.data.leaves) == 5


def test_hours_still_read_for_json_tier_leaves(tmp_path):
    think = leaf(models_run(tmp_path), "Issue-3")
    assert think.models.source == "wbs"
    assert think.estimate == Estimate(4, 8, "estimates_file", "absent", "S")


def test_row_reports_kept_for_json_tier_leaf(tmp_path):
    def counts(got):
        return {kind: len(entries(got, kind)) for kind in (
            "estimates_cell_bad", "estimates_row_duplicate", "estimates_column_missing")}

    tiers = models_run(tmp_path / "json")
    assert leaf(tiers, "Issue-2").models.source == "wbs"
    assert counts(tiers) == {"estimates_cell_bad": 1, "estimates_row_duplicate": 1, "estimates_column_missing": 0}
    assert "#2" in entries(tiers, "estimates_cell_bad")[0].message
    assert "#2" in entries(tiers, "estimates_row_duplicate")[0].message
    fallback = hp.set_models(models_doc(), "alpha", 2, thinking=None, checking=None)
    rowed = models_run(tmp_path / "row", fallback)
    assert leaf(rowed, "Issue-2").models.source == "estimates_file"
    assert counts(rowed) == counts(tiers)
    no_thinking = INPUTS / "models-estimates-no-thinking.md"
    for where, doc in (("json-nt", models_doc()), ("row-nt", fallback)):
        got = models_run(tmp_path / where, doc, no_thinking)
        missing = entries(got, "estimates_column_missing")
        assert len(missing) == 1 and "'thinking'" in missing[0].message, where
