"""The strategic page's model (#23): backlog shares, intended shares, gaps,
groups and the headline, as values. The page's texts are in
tests/test_strategic_page.py."""

import math
from datetime import date

import helpers_portfolio as hp
from gantry.build import build_all
from gantry.config import load_config
from gantry.strategic import backlog_shares, strategic_model
from helpers_config import add_top_level, copy_samples, replace_bytes, write_site_tree
from sample_roles import BUSY, NO_WBS, OWN_UNIT, SMALL, UNESTIMATED, UNREADABLE

# The samples at their as-of date (U1): hours per built site, at the default unit of 6.
HOURS = {BUSY: 130, OWN_UNIT: 90, UNESTIMATED: 30, SMALL: 174}
TOTAL = 424
SAMPLE_SHARES = "shares = { harbor = 50, orchard = 30, quarry = 20 }"  # the 2026-01-05 allocation


def build(copy):
    return build_all(load_config(copy / "gantry.sample.toml"), samples=copy)


def samples(tmp_path, shares=None):
    """A samples copy, its 2026-01-05 shares replaced by `shares` (TOML inline-table body)."""
    copy = copy_samples(tmp_path)
    if shares is not None:
        replace_bytes(copy / "portfolio.md", SAMPLE_SHARES, f"shares = {{ {shares} }}")
    return copy


def groups_of(model):
    return [(g.kind, g.br_id, g.sites) for g in model.groups]


def gaps(model):
    return {name: p.gap for name, p in model.panels.items() if p.gap is not None}


# --- the backlog share (AC3) -----------------------------------------------------------


def test_backlog_share_is_hours_over_built_sum(tmp_path):
    shares = backlog_shares(build(samples(tmp_path)))
    assert list(shares) == [BUSY, OWN_UNIT, UNESTIMATED, SMALL]
    for name, hours in HOURS.items():
        assert math.isclose(shares[name].hours, hours)
        assert math.isclose(shares[name].share, hours / TOTAL * 100)
    assert math.isclose(sum(s.share for s in shares.values()), 100)


def test_site_counted_once(tmp_path):
    result = build(samples(tmp_path))
    model = strategic_model(result)
    assert sum(BUSY in sites for _, _, sites in groups_of(model)) == 2
    assert math.isclose(model.total_hours, TOTAL)
    assert math.isclose(model.total_hours, sum(s.hours for s in result.summaries if s.status == "built"))


def test_unestimated_site_at_default_unit(tmp_path):
    quarry = backlog_shares(build(samples(tmp_path)))[UNESTIMATED]
    assert math.isclose(quarry.hours, 5 * 6)
    assert quarry.unestimated_count == 5


def test_own_unit_at_default_unit(tmp_path):
    orchard = backlog_shares(build(samples(tmp_path)))[OWN_UNIT]
    assert orchard.non_time == {"pt": {"quantity": 13.0, "leaves": 2}}
    assert orchard.non_time_split == {"pt": {"default": 2, "estimates_file": 0}}


ESTIMATES_ROW = "| # | title | effort | hours |\n|---|---|---|---|\n| #6 | Lay the drip lines | M | 8-16 |\n"


def with_estimates_row(tmp_path):
    """U3: a samples copy whose orchard has an estimates file giving Issue-6 (8pt) hours."""
    copy = samples(tmp_path)
    (copy / OWN_UNIT / "estimates-2026-01-15-x.md").write_bytes(ESTIMATES_ROW.encode("utf-8"))
    return copy


def test_non_time_leaf_with_estimates_row(tmp_path):
    orchard = backlog_shares(build(with_estimates_row(tmp_path)))[OWN_UNIT]
    assert orchard.non_time == {"pt": {"quantity": 13.0, "leaves": 2}}
    assert orchard.non_time_split == {"pt": {"default": 1, "estimates_file": 1}}
    assert orchard.unestimated_count == 4


def test_default_unit_hours_moves_both_shares(tmp_path):
    base = backlog_shares(build(samples(tmp_path / "base")))
    copy = samples(tmp_path / "twelve")
    add_top_level(copy / "gantry.sample.toml", "default_unit_hours = 12\n")
    moved = backlog_shares(build(copy))
    assert math.isclose(moved[UNESTIMATED].hours, 60) and math.isclose(moved[OWN_UNIT].hours, 120)
    for name in (UNESTIMATED, OWN_UNIT):
        assert not math.isclose(moved[name].share, base[name].share)


# --- intended shares and the gap (AC4) ---------------------------------------------------


def test_off_page_share_and_comparable_gap(tmp_path):
    result = build(samples(tmp_path, "harbor = 35, orchard = 30, quarry = 20, bakery = 10, ferry = 5"))
    model = strategic_model(result)
    assert math.isclose(model.off_not_built, 10) and math.isclose(model.off_not_in_config, 5)
    given = {BUSY: 35, OWN_UNIT: 30, UNESTIMATED: 20}
    assert set(gaps(model)) == set(given)
    for name, share in given.items():
        backlog = HOURS[name] / TOTAL * 100
        assert math.isclose(model.panels[name].comparable, share / 85 * 100)
        assert math.isclose(model.panels[name].gap, abs(backlog - share / 85 * 100))
    assert not math.isclose(model.panels[BUSY].gap, abs(HOURS[BUSY] / TOTAL * 100 - 35))
    assert model.panels[NO_WBS].intended == 10 and model.panels[NO_WBS].gap is None


def test_zero_share_places_and_competes(tmp_path):
    model = strategic_model(build(samples(tmp_path, "harbor = 31, orchard = 21, quarry = 0, lighthouse = 48")))
    (placed,) = [sites for kind, _, sites in groups_of(model) if kind == "no_requirement"]
    assert placed == (UNESTIMATED, SMALL)
    assert model.headline.site == UNESTIMATED and model.headline.intended == 0
    assert math.isclose(model.panels[UNESTIMATED].gap, 30 / TOTAL * 100)  # ≈7.08
    assert math.isclose(model.panels[SMALL].gap, abs(174 / TOTAL * 100 - 48))  # ≈6.96


def test_requirement_placed_site_without_share_is_in_no_other_group(tmp_path):
    """AC1's "never both" where it can break: harbor serves two requirements but the allocation gives
    it no share, so it is only in those two groups; "no intended share set" holds only unplaced sites."""
    model = strategic_model(build(samples(tmp_path, "orchard = 60, quarry = 40")))
    assert groups_of(model) == [("br", "life:BR-1", (BUSY,)), ("br", "life:BR-2", (BUSY,)),
                                ("br", "life:BR-3", (OWN_UNIT,)), ("no_requirement", None, (UNESTIMATED,)),
                                ("no_share", None, (SMALL,)), ("not_built", None, (NO_WBS, UNREADABLE))]
    harbor = model.panels[BUSY]
    assert harbor.intended is None and harbor.comparable is None and harbor.gap is None


ALLOCATION = """# Portfolio

## Business requirements

- BR-1: Keep the fixture small.

## Settings

```toml
portfolio_version = 1

[[allocation]]
from = 2026-01-01
shares = {{ {shares} }}
```
"""


def planner_install(tmp_path, shares, wbs):
    from test_planner import install

    return build_all(load_config(install(tmp_path, "states", wbs=wbs, portfolio=ALLOCATION.format(shares=shares))
                                 / "gantry.toml"))


SAME = hp.source(b"# S\n\n- [ ] Only {issue: Issue-1, est: 6h, h: 1}\n",
                 hp.node("Only", issue="Issue-1", est="6h", h="1"), start=3)
FINISHED = hp.source(b"# S\n\n- [x] Done {issue: Issue-1}\n", hp.node("Done", glyph="x", issue="Issue-1"), start=3)


def test_headline_tie_by_config_order(tmp_path):
    model = strategic_model(planner_install(tmp_path, "a = 50, b = 50", {"a": SAME, "b": SAME}))
    assert gaps(model) == {"a": 0, "b": 0}
    assert model.headline.site == "a"


def test_headline_two_site_tie_is_not_float_noise(tmp_path):
    """Two built sites with shares always tie: 1 h and 2 h against 10 and 90
    give both a gap of 23 1/3, which floats compute a hair apart."""
    one = hp.source(b"# S\n\n- [ ] Only {issue: Issue-1, est: 1h, h: 1}\n",
                    hp.node("Only", issue="Issue-1", est="1h", h="1"), start=3)
    two = hp.source(b"# S\n\n- [ ] Only {issue: Issue-1, est: 2h, h: 1}\n",
                    hp.node("Only", issue="Issue-1", est="2h", h="1"), start=3)
    model = strategic_model(planner_install(tmp_path, "a = 10, b = 90", {"a": one, "b": two}))
    assert math.isclose(gaps(model)["a"], gaps(model)["b"])
    assert model.headline.site == "a"


def test_headline_gaps_a_hundredth_apart_do_not_tie(tmp_path):
    """The tie is for floating-point error only: harbor's gap (about 10.0004 points) and quarry's
    (about 10.0145) are not a tie, so quarry, later in config order, is the headline."""
    model = strategic_model(build(samples(tmp_path, "harbor = 20.66, orchard = 21.23, quarry = 17.09, "
                                                    "lighthouse = 41.02")))
    found = gaps(model)
    assert 0.01 < found[UNESTIMATED] - found[BUSY] < 0.02
    assert max(found.values()) == found[UNESTIMATED]
    assert model.headline.site == UNESTIMATED


def test_headline_ranks_unrounded_gaps(tmp_path):
    """Two gaps that both show as 7 points: the larger unrounded one wins, though it is later in config order."""
    model = strategic_model(build(samples(tmp_path, "harbor = 31, orchard = 21, quarry = 14, lighthouse = 34")))
    shown = {name: round(gap) for name, gap in gaps(model).items()}
    assert shown[SMALL] == max(shown.values()) == 7 and list(shown.values()).count(7) == 2
    assert model.headline.site == SMALL


def test_allocation_by_as_of(tmp_path):
    result = build(samples(tmp_path))
    assert strategic_model(result, date(2026, 1, 20)).allocation.from_date == date(2026, 1, 5)
    assert strategic_model(result, date(2026, 3, 2)).allocation.from_date == date(2026, 3, 2)
    early = strategic_model(result, date(2025, 11, 30))
    assert early.allocation is None and early.headline is None
    assert all(p.intended is None and p.comparable is None for p in early.panels.values())


def test_no_portfolio_file(tmp_path):
    copy = samples(tmp_path)
    replace_bytes(copy / "gantry.sample.toml", 'portfolio_file = "portfolio.md"\n', "")
    model = strategic_model(build(copy))
    assert model.configured is False and model.allocation is None and model.report == ()
    assert groups_of(model) == [("no_share", None, (BUSY, OWN_UNIT, UNESTIMATED, SMALL)),
                                ("not_built", None, (NO_WBS, UNREADABLE))]


def test_extremes(tmp_path):
    # Every leaf finished: no backlog share, so no gap and no headline.
    model = strategic_model(planner_install(tmp_path, "a = 50, b = 50", {"a": FINISHED, "b": FINISHED}))
    assert all(b.share is None for b in model.backlog.values()) and len(model.backlog) == 2
    assert gaps(model) == {} and model.headline is None
    # Shares only on names that are unknown or not built: nothing comparable.
    model = strategic_model(build(samples(tmp_path / "off", "bakery = 50, ferry = 50")))
    assert all(p.comparable is None for p in model.panels.values())
    assert model.headline is None
    # Explicit zero shares on every built site: the built sum is 0 (B5).
    model = strategic_model(planner_install(tmp_path, "a = 0, b = 0, ghost = 100", {"a": SAME, "b": SAME}))
    assert [p.comparable for p in model.panels.values()] == [None, None]
    assert [p.intended for p in model.panels.values()] == [0, 0]
    assert model.headline is None
    # Leaves held out by a cycle are open but not in hours (B5).
    sites = {"done": hp.DONE_SITE, "held": hp.HELD_SITE}
    model = strategic_model(build_all(load_config(write_site_tree(tmp_path / "held", sites))))
    panel = model.panels["held"]
    assert (panel.open_leaves, panel.held_out, panel.backlog.hours) == (2, 2, 0)
    assert [b.share for b in model.backlog.values()] == [None, None]


# --- why a panel has no gap (#29 D1) -----------------------------------------------------


def test_gap_reason_names_why_no_gap(tmp_path):
    from gantry.strategic import NO_GAP_NO_OPEN_WORK, NO_GAP_NO_SHARE, NO_GAP_SHARES_ZERO

    copy = samples(tmp_path / "none")
    replace_bytes(copy / "gantry.sample.toml", 'portfolio_file = "portfolio.md"\n', "")
    model = strategic_model(build(copy))
    built = [p for p in model.panels.values() if p.built]
    assert len(built) == 4
    assert all(p.gap is None and p.gap_reason == NO_GAP_NO_SHARE for p in built)
    assert all(p.gap_reason is None for p in model.panels.values() if not p.built)
    # Every site done, both with shares: no open work; a site with no share still names its share first.
    model = strategic_model(planner_install(tmp_path / "done", "a = 50, ghost = 50", {"a": FINISHED, "b": FINISHED}))
    assert [p.gap_reason for p in model.panels.values()] == [NO_GAP_NO_OPEN_WORK, NO_GAP_NO_SHARE]
    model = strategic_model(planner_install(tmp_path / "zero", "a = 0, b = 0, ghost = 100",
                                            {"a": SAME, "b": SAME}))
    assert [p.gap_reason for p in model.panels.values()] == [NO_GAP_SHARES_ZERO] * 2
    model = strategic_model(build(samples(tmp_path / "demo")))
    assert model.panels[BUSY].gap is not None and model.panels[BUSY].gap_reason is None
    assert model.panels[SMALL].gap is None and model.panels[SMALL].gap_reason == NO_GAP_NO_SHARE
    assert model.panels[NO_WBS].built is False and model.panels[NO_WBS].gap_reason is None
