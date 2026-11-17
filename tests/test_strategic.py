"""The strategic page's model (#23): backlog shares, intended shares, gaps,
groups and the headline, as values. The page's texts are in
tests/test_strategic_page.py."""

import math
from datetime import date

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


def model_of(result, as_of=None):
    return strategic_model(result, result.portfolio, result.portfolio_entries,
                           as_of if as_of is not None else result.config.as_of)


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
    model = model_of(result)
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
    model = model_of(result)
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
    model = model_of(build(samples(tmp_path, "harbor = 31, orchard = 21, quarry = 0, lighthouse = 48")))
    (placed,) = [sites for kind, _, sites in groups_of(model) if kind == "no_requirement"]
    assert placed == (UNESTIMATED, SMALL)
    assert model.headline.site == UNESTIMATED and model.headline.intended == 0
    assert math.isclose(model.panels[UNESTIMATED].gap, 30 / TOTAL * 100)  # ≈7.08
    assert math.isclose(model.panels[SMALL].gap, abs(174 / TOTAL * 100 - 48))  # ≈6.96


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


SAME = "# S\n\n- [ ] Only {issue: Issue-1, est: 6h, h: 1}\n"
FINISHED = "# S\n\n- [x] Done {issue: Issue-1}\n"


def test_headline_tie_by_config_order(tmp_path):
    model = model_of(planner_install(tmp_path, "a = 50, b = 50", {"a": SAME, "b": SAME}))
    assert gaps(model) == {"a": 0, "b": 0}
    assert model.headline.site == "a"


def test_allocation_by_as_of(tmp_path):
    result = build(samples(tmp_path))
    assert model_of(result, date(2026, 1, 20)).allocation.from_date == date(2026, 1, 5)
    assert model_of(result, date(2026, 3, 2)).allocation.from_date == date(2026, 3, 2)
    early = model_of(result, date(2025, 11, 30))
    assert early.allocation is None and early.headline is None
    assert all(p.intended is None and p.comparable is None for p in early.panels.values())


def test_no_portfolio_file(tmp_path):
    copy = samples(tmp_path)
    replace_bytes(copy / "gantry.sample.toml", 'portfolio_file = "portfolio.md"\n', "")
    model = model_of(build(copy))
    assert model.configured is False and model.allocation is None and model.report == ()
    assert groups_of(model) == [("no_share", None, (BUSY, OWN_UNIT, UNESTIMATED, SMALL)),
                                ("not_built", None, (NO_WBS, UNREADABLE))]


def test_extremes(tmp_path):
    # Every leaf finished: no backlog share, so no gap and no headline.
    model = model_of(planner_install(tmp_path, "a = 50, b = 50", {"a": FINISHED, "b": FINISHED}))
    assert all(b.share is None for b in model.backlog.values()) and len(model.backlog) == 2
    assert gaps(model) == {} and model.headline is None
    # Shares only on names that are unknown or not built: nothing comparable.
    model = model_of(build(samples(tmp_path / "off", "bakery = 50, ferry = 50")))
    assert all(p.comparable is None for p in model.panels.values())
    assert model.headline is None
    # Explicit zero shares on every built site: the built sum is 0 (B5).
    model = model_of(planner_install(tmp_path, "a = 0, b = 0, ghost = 100", {"a": SAME, "b": SAME}))
    assert [p.comparable for p in model.panels.values()] == [None, None]
    assert [p.intended for p in model.panels.values()] == [0, 0]
    assert model.headline is None
    # Leaves held out by a cycle are open but not in hours (B5).
    done = b"- [x] Done {issue: Issue-1}\n"
    held = b"- [ ] A {issue: Issue-1, depends_on: [Issue-2]}\n- [ ] B {issue: Issue-2, depends_on: [Issue-1]}\n"
    model = model_of(build_all(load_config(write_site_tree(tmp_path / "held", {"done": done, "held": held}))))
    panel = model.panels["held"]
    assert (panel.open_leaves, panel.held_out, panel.backlog.hours) == (2, 2, 0)
    assert [b.share for b in model.backlog.values()] == [None, None]
