"""The week view page: gantry/views/week.py (#38).

Every expected text is an exact string, read off one element or one part of
a leaf line, so "continues after" never passes a "continues " check.
"""

import copy
import json
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from gantry import render
from gantry.build import build_all
from gantry.config import load_config
from gantry.model import Models
from gantry.planner import ALLOCATION_RULE, NO_PLAN, PLAN_VERSION, UNPLANNED_REASONS, WeekPlan
from gantry.links import leaf_href
from gantry.render import (MODEL_CLASSES, MODEL_FALLBACK, SLACK_CLASSES, STATE_CLASSES, STATE_FALLBACK,
                           WEEK_CLASSES)
from gantry.views import week
from gantry.views.week import render_week
from helpers_config import sample_build
from page_safety import HOSTILE, RULES, assert_no_external_resources, assert_page_safe
from page_script.harness import Planted, open_page
from test_plan_file import shape_errors
from test_planner import FIXTURE_NAMES, NO_PLAN_CASES, install, leaf, portfolio_md, wbs_md
from test_worksite_page import find, text_of, tree, visible_text

STAMP = "built 2026-01-19 09:00"
HORIZON = ["2026-01-19", "2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23",
           "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30"]
ATTENTION, LANES = "operator attention hours", "agent lanes"
NEW_ATTRS = ("data-beyond", "data-block", "data-day", "data-figure", "data-lanes", "data-leaf", "data-site",
             "data-source", "data-state", "data-tag", "data-tier", "data-today", "data-unplanned")
# Classes that name a part of the page, never a record value.
STRUCTURAL = frozenset({"wrap", "label", "stamp", "not-working", "rule", "mapping", "capacity", "shareless", "day", "later",
                        "today", "hours", "reserved", "none", "block", "lanes", "left", "leaf", "title", "unest",
                        "state", "tier", "need", "basis", "cont", "site-unplanned", "beyond-list", "reason", "held",
                        "p0-leaf", "gone", "site", "top", "key", "meta", "prio", "ttl", "tl", "facts", "bands",
                        "band", "lane-site", "sw", "xsite"})
ALLOWED_CLASSES = (WEEK_CLASSES | STRUCTURAL | set(STATE_CLASSES.values()) | {STATE_FALLBACK} | set(SLACK_CLASSES)
                   | set(MODEL_CLASSES.values()) | {MODEL_FALLBACK})


# --- constructed plans --------------------------------------------------------------------


def pools(attention_out=(), lanes_out=()):
    return [{"name": ATTENTION, "capacity_source": "portfolio file", "days_run_out": list(attention_out)},
            {"name": LANES, "capacity_source": "portfolio file", "days_run_out": list(lanes_out)}]


def make_plan(**fields) -> WeekPlan:
    """A planned WeekPlan with a 10-day horizon from Monday 2026-01-19; `days`
    holds only the days a test sets. The plan file's pinned shape holds."""
    base = {
        "plan_version": PLAN_VERSION, "status": "planned", "reason": None, "as_of": HORIZON[0],
        "allocation_rule": ALLOCATION_RULE, "unestimated_planned": 0, "horizon_days": list(HORIZON),
        "planned_week": HORIZON[:5],
        "mapping": {"values": [0.0, 0.1, 0.25, 0.5, 1.0], "source": "default", "rejected": None},
        "pools": pools(), "binding": {"pool": None, "first_day": None, "text": "no pool ran out"},
        "days": [], "leaves": [], "sites": [], "report": [], "notes": [],
    }
    base.update(fields)
    plan = WeekPlan(**base)
    assert shape_errors(json.loads(json.dumps(asdict(plan)))) == []
    return plan


def day(date, blocks=(), lanes_row=(), reserved=(), attention=8.0, left=(1.0, 2.0), weekday="mon"):
    return {"date": date, "weekday": weekday, "attention_hours_declared": attention, "attention_hours": attention,
            "reserved": list(reserved), "lane_hours": 6.0, "blocks": list(blocks), "lanes_row": list(lanes_row),
            "left": {"attention_hours": left[0], "lane_hours": left[1]}, "ran_out": [], "unestimated": 0}


def block(number, site, entries, length=1.0, start_cost=0.25):
    """`entries`: (key, agent_hours) pairs."""
    return {"number": number, "site": site, "length_hours": length, "start_cost_hours": start_cost,
            "leaves": [{"site": site, "key": key, "agent_hours": h, "operator_hours": 0.1} for key, h in entries]}


def lane(site, key, hours):
    return {"site": site, "key": key, "agent_hours": hours}


def leaf_record(site, key, **kw):
    record = {"site": site, "key": key, "rate": 0.1, "need": 1, "tag": "scored", "basis": "scored: h: 1",
              "agent_hours": 1.0, "unestimated": False, "state": "pending", "hours_after_horizon": 0.0,
              "days": [HORIZON[0]], "unresolved_local": 0, "tier": "fair_share"}
    record.update(kw)
    return record


def site_record(name, unplanned=(), p0=(), share=50.0, shareless=(), **kw):
    record = {"name": name, "ratio_source": "estimated", "attention_hours_charged": 1.0, "lane_hours_charged": 2.0,
              "attention_hours_target": 1.0, "lane_hours_target": 2.0, "unestimated_planned": 0,
              "unplanned": [{"key": key, "reason": reason, "state": "pending", "unresolved_local": 0}
                            for key, reason in unplanned],
              "shares": [{"date": d, "share": None if d in shareless else share} for d in HORIZON],
              "shareless_days": list(shareless), "share_note": None, "p0": list(p0)}
    record.update(kw)
    return record


def make_result(leaves=(), pages=("alpha",), not_built=(), page_models=None):
    """A BuildResult stand-in: `leaves` are (site, key, title, est) tuples, or
    (site, key, title, est, {field: value}) to set priority, models or
    waits_on_other_site."""
    inputs = {}
    for site, key, title, est, *more in leaves:
        meta = {} if est is None else {"est": est}
        fields = {"priority": None, "models": None, "waits_on_other_site": False, **(more[0] if more else {})}
        data = inputs.setdefault(site, SimpleNamespace(data=SimpleNamespace(leaves=[]))).data
        data.leaves.append(SimpleNamespace(key=key, title=title, meta=meta, **fields))
    summaries = [SimpleNamespace(name=name, status="built", reason=None) for name in pages]
    summaries += [SimpleNamespace(name=name, status="not_built", reason=reason) for name, reason in not_built]
    return SimpleNamespace(inputs=inputs, pages={name: "" for name in pages}, summaries=tuple(summaries),
                           stamp=STAMP, page_models={} if page_models is None else page_models)


def fixture_build(tmp_path, base, **changes):
    result = build_all(load_config(install(tmp_path, base, **changes) / "gantry.toml"))
    return result, render_week(result.plan, result, result.stamp)


def fixture_page(tmp_path, base, **changes):
    return fixture_build(tmp_path, base, **changes)[1]


def demo(tmp_path):
    result = sample_build(tmp_path)
    return result, render_week(result.plan, result, result.stamp)


# --- reading a page -----------------------------------------------------------------------


def attr(t, i, name):
    return t.elements[i]["attrs"].get(name)


def with_attr(t, name, value=None, within=None):
    return [i for i in find(t, within=within)
            if name in t.elements[i]["attrs"] and (value is None or attr(t, i, name) == value)]


def children(t, i):
    return [j for j, el in enumerate(t.elements) if el["parent"] == i]


def texts(t, tag=None, classes=(), within=None):
    return [text_of(t, i) for i in find(t, tag, classes, within)]


def all_texts(t):
    return [text_of(t, i) for i in range(len(t.elements)) if t.elements[i]["tag"] not in ("script", "style")]


def one(t, name, value=None, within=None):
    found = with_attr(t, name, value, within)
    assert len(found) == 1, (name, value, found)
    return found[0]


def site_box(t, name):
    """A site's box in the unplanned section."""
    (box,) = [i for i in find(t, "div", ["site-unplanned"]) if attr(t, i, "data-site") == name]
    return box


def leaf_lines(t, key=None, within=None):
    """A placement's leaf lines, under #days unless `within` is given."""
    within = one(t, "id", "days") if within is None else within
    return [i for i in with_attr(t, "data-leaf", key, within) if t.elements[i]["tag"] == "li"]


def placed(**leaf_kw):
    """One day, one block, one leaf: (plan fields, result) for a leaf line's parts."""
    record = leaf_record("alpha", "Issue-1", **leaf_kw)
    return {"days": [day(HORIZON[0], [block(1, "alpha", [("Issue-1", 2.0)])])], "leaves": [record],
            "sites": [site_record("alpha")]}


# --- AC 1: days, blocks and hours (constructed) --------------------------------------------


def test_constructed_days_blocks_and_hours():
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 2.0)], length=135 / 60),
                                            block(2, "beta", [("Issue-1", 1.0)])], left=(0.5, 7 / 60))],
                     leaves=[leaf_record("alpha", "Issue-1"), leaf_record("beta", "Issue-1")],
                     sites=[site_record("alpha"), site_record("beta")])
    t = tree(render_week(plan, make_result([("alpha", "Issue-1", "One", None)], pages=("alpha", "beta")), STAMP))
    (monday,) = with_attr(t, "data-day", HORIZON[0])
    assert texts(t, "p", ["hours"], monday) == ["8 h attention after reserved blocks"]
    blocks = with_attr(t, "data-block", within=monday)
    assert [(attr(t, i, "data-block"), attr(t, i, "data-site")) for i in blocks] == [("1", "alpha"), ("2", "beta")]
    assert texts(t, "h3", within=blocks[0]) == ["alpha · 2.25 h · start cost 0.25 h"]
    assert texts(t, "p", ["left"], monday) == ["left: operator attention hours 0.5 h; agent lanes 0.12 h"]
    first = leaf_lines(t, within=blocks[0])[0]
    assert (texts(t, "span", ["tl"], first), texts(t, "span", ["hours"], first)) == (["One"], ["2 h"])
    # A missing title shows the key.
    assert texts(t, "span", ["tl"], leaf_lines(t, within=blocks[1])[0]) == ["Issue-1"]


def test_constructed_merged_block_one_start_cost():
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 1.0), ("Issue-2", 1.0)])])],
                     leaves=[leaf_record("alpha", "Issue-1", tier="p0"), leaf_record("alpha", "Issue-2")],
                     sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result(), STAMP))
    (only,) = with_attr(t, "data-block")
    assert len(leaf_lines(t, within=only)) == 2
    assert sum(text.count("start cost") for text in texts(t, "h3")) == 1


def test_constructed_lanes_row_under_blocks():
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 1.0)])], [lane("alpha", "Issue-2", 4.0)])],
                     leaves=[leaf_record("alpha", "Issue-1"), leaf_record("alpha", "Issue-2", need=0, rate=0.0)],
                     sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result(), STAMP))
    row = one(t, "data-lanes", "1")
    assert row > with_attr(t, "data-block")[-1]
    assert texts(t, "h3", within=row) == ["runs in a lane; no block needed"]
    first = leaf_lines(t, within=row)[0]
    assert [texts(t, "span", [cls], first) for cls in ("key", "hours")] == [["Issue-2"], ["4 h"]]
    assert lane_groups(t, row) == [("alpha", ["Issue-2"])]
    # A block's leaves take their site from the block header.
    assert texts(t, "span", ["site"], with_attr(t, "data-block")[-1]) == []


def lane_groups(t, row):
    """[(site, [key, ...])] of a lanes row's site groups, in page order; each group's h4 names its site."""
    groups = []
    for g in find(t, "div", ["lane-site"], within=row):
        assert texts(t, "h4", within=g) == [attr(t, g, "data-site")]
        lines = leaf_lines(t, within=g)
        assert {attr(t, i, "data-site") for i in lines} == {attr(t, g, "data-site")}
        groups.append((attr(t, g, "data-site"), [attr(t, i, "data-leaf") for i in lines]))
    return groups


def test_mixed_site_lanes_row_names_each_site(tmp_path):
    t = tree(fixture_page(tmp_path, "unequal_rates"))
    rows = with_attr(t, "data-lanes")
    assert any([site for site, _ in lane_groups(t, row)] in (["a", "b"], ["b", "a"]) for row in rows)
    assert find(t, "span", ["site"]) == []


def test_lanes_row_groups_by_site_constructed():
    row = [lane("b", "1", 1.0), lane("a", "1", 1.0), lane("b", "2", 1.0)]
    plan = make_plan(days=[day(HORIZON[0], lanes_row=row)],
                     leaves=[leaf_record(e["site"], e["key"], need=0, rate=0.0) for e in row],
                     sites=[site_record("a"), site_record("b")])
    t = tree(render_week(plan, make_result(pages=("a", "b")), STAMP))
    (lanes,) = with_attr(t, "data-lanes")
    # First appearance, then plan order inside a site; sorted or config order would give a first.
    assert lane_groups(t, lanes) == [("b", ["1", "2"]), ("a", ["1"])]
    assert texts(t, "h4") == ["b", "a"]
    assert find(t, "span", ["site"]) == []


def test_lanes_row_groups_on_fixture(tmp_path):
    result, html = fixture_build(tmp_path, "leaf_boxes")
    t = tree(html)
    checked = 0
    for d in result.plan.days:
        row = d["lanes_row"]
        if not row:
            continue
        (day_el,) = with_attr(t, "data-day", d["date"])
        (lanes,) = with_attr(t, "data-lanes", within=day_el)
        order = list(dict.fromkeys(e["site"] for e in row))
        assert lane_groups(t, lanes) == [(site, [e["key"] for e in row if e["site"] == site]) for site in order]
        checked += order == ["b", "a"]
    assert checked >= 1


def test_constructed_split_leaf_continues():
    days = [day(HORIZON[0], [block(1, "alpha", [("Issue-1", 1.0)]), block(2, "alpha", [("Issue-1", 1.0)])]),
            day(HORIZON[2], [block(1, "alpha", [("Issue-1", 1.0)])], weekday="wed")]
    plan = make_plan(days=days, leaves=[leaf_record("alpha", "Issue-1", days=[HORIZON[0], HORIZON[2]])],
                     sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result(), STAMP))
    lines = leaf_lines(t, "Issue-1")
    assert len(lines) == 3
    conts = [texts(t, "span", ["cont"], i) for i in lines]
    assert conts == [["continues Monday 2026-01-19"], ["continues Wednesday 2026-01-21"], []]


def test_constructed_long_leaf_continues_after_horizon():
    def page(left):
        plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 1.0)])]),
                               day(HORIZON[9], [block(1, "alpha", [("Issue-1", 1.0)])], weekday="fri")],
                         leaves=[leaf_record("alpha", "Issue-1", hours_after_horizon=left)],
                         sites=[site_record("alpha")])
        return tree(render_week(plan, make_result(), STAMP))

    t = page(4.5)
    conts = [text for i in leaf_lines(t) for text in texts(t, "span", ["cont"], i)]
    assert conts == ["continues Friday 2026-01-30", "continues after Friday 2026-01-30 (4.5 h left)"]
    t = page(0.0)
    assert [text for i in leaf_lines(t) for text in texts(t, "span", ["cont"], i)] == ["continues Friday 2026-01-30"]


def test_continues_after_names_the_horizons_last_day():
    # horizon_days[-1] drives "continues after", not the leaf's last day.
    horizon = HORIZON[:9] + ["2026-02-02"]
    plan = make_plan(horizon_days=horizon, days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 1.0)])])],
                     leaves=[leaf_record("alpha", "Issue-1", hours_after_horizon=1.0)], sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result(), STAMP))
    assert texts(t, "span", ["cont"]) == ["continues after Monday 2026-02-02 (1 h left)"]


def test_constructed_reserved_block_shown():
    reserved = [{"start": "09:00", "hours": 1.0, "label": "standup"}]
    reserved += [{"start": "13:00", "hours": 0.5, "label": label} for label in (None, "", "   ")]
    plan = make_plan(days=[day(HORIZON[0], reserved=reserved)])
    t = tree(render_week(plan, make_result(), STAMP))
    assert texts(t, "p", ["reserved"]) == ["reserved 09:00, 1 h: standup"] + ["reserved 13:00, 0.5 h"] * 3
    assert not [text for text in all_texts(t) if text.rstrip().endswith(":")]


def test_constructed_unestimated_labels():
    keys = ("Issue-1", "Issue-2", "Issue-3", "Issue-4")
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [(key, 1.0) for key in keys])])],
                     leaves=[leaf_record("alpha", key, unestimated=key != "Issue-4") for key in keys],
                     sites=[site_record("alpha")], unestimated_planned=3)
    result = make_result([("alpha", "Issue-1", "One", "3pr"), ("alpha", "Issue-2", "Two", "?w"),
                          ("alpha", "Issue-3", "Three", None), ("alpha", "Issue-4", "Four", "2h")])
    t = tree(render_week(plan, result, STAMP))
    assert [texts(t, "span", ["unest"], leaf_lines(t, key)[0]) for key in keys] == [
        ["unestimated (3 pr)"], ["unestimated"], ["unestimated"], []]


def test_constructed_states_and_p0_marks():
    keys = {"Issue-1": ("in_progress", "started"), "Issue-2": ("stuck", "started"), "Issue-3": ("pending", "p0"),
            "Issue-4": ("pending", "fair_share")}
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [(key, 1.0) for key in keys])])],
                     leaves=[leaf_record("alpha", key, state=s, tier=tier) for key, (s, tier) in keys.items()],
                     sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result(), STAMP))
    got = {key: (texts(t, "span", ["state"], i), texts(t, "span", ["tier"], i), attr(t, i, "data-state"),
                 attr(t, i, "data-tier")) for key in keys for i in leaf_lines(t, key)}
    assert got == {"Issue-1": (["in progress"], [], "in_progress", "started"),
                   "Issue-2": (["stuck"], [], "stuck", "started"),
                   "Issue-3": ([], ["P0"], "pending", "p0"),
                   "Issue-4": ([], [], "pending", "fair_share")}
    assert "tier-p0" in t.elements[leaf_lines(t, "Issue-3")[0]]["classes"]


def test_constructed_today_column():
    plan = make_plan(days=[day(d) for d in HORIZON])
    t = tree(render_week(plan, make_result(), STAMP))
    (today,) = with_attr(t, "data-today")
    assert (attr(t, today, "data-today"), attr(t, today, "data-day")) == ("true", HORIZON[0])
    assert texts(t, "p", ["today"]) == ["today"]
    assert not [text for text in all_texts(t) if "is not a working day" in text]


def test_constructed_not_built_listed():
    plan = make_plan()
    t = tree(render_week(plan, make_result(not_built=[("bakery", "no WBS.md in the planning directory")]), STAMP))
    assert texts(t, "li", within=one(t, "id", "not-built")) == ["bakery: not built: no WBS.md in the planning directory"]


def test_constructed_failed_write_site_shows_unlinked_and_not_built():
    plan = make_plan(**placed())
    result = make_result([("alpha", "Issue-1", "One", None)], pages=(),
                         not_built=[("alpha", "cannot write sites/alpha.html: Permission denied")])
    t = tree(render_week(plan, result, STAMP))
    (line,) = leaf_lines(t, "Issue-1")
    assert find(t, "a", within=line) == [] and texts(t, "span", ["tl"], line) == ["One"]
    assert texts(t, "li", within=one(t, "id", "not-built")) == [
        "alpha: not built: cannot write sites/alpha.html: Permission denied"]


def test_leaf_href_uses_the_anchor_encoding():
    plan = make_plan(**placed())
    t = tree(render_week(plan, make_result([("alpha", "Issue-1", "One", None)]), STAMP))
    (a,) = find(t, "a", within=leaf_lines(t, "Issue-1")[0])
    assert attr(t, a, "href") == "sites/alpha.html#leaf-Issue-1"


# --- the leaf box (#170) ---------------------------------------------------------------------

# The cue classes both boxes carry: slack, state, unest and xsite.
CUES = frozenset({*SLACK_CLASSES, *STATE_CLASSES.values(), STATE_FALLBACK, "unest", "xsite"})


def placed_nodes(plan):
    """[(site, key)] of every placement, in page order, each once."""
    seen = {}
    for d in plan.days:
        for e in [*(x for b in d["blocks"] for x in b["leaves"]), *d["lanes_row"]]:
            seen.setdefault((e["site"], e["key"]))
    return list(seen)


def week_box(t, i):
    """A week box's texts and cue classes."""
    prio = texts(t, "span", ["prio"], i)
    return {"key": texts(t, "span", ["key"], i), "prio": prio[0] if prio else None,
            "title": texts(t, "span", ["tl"], i),
            "band": [(text_of(t, j), sorted(set(t.elements[j]["classes"]) - {"band"})) for j in find(t, "span", ["band"], i)],
            "cues": set(t.elements[i]["classes"]) & CUES}


def worksite_box(page, key):
    """The worksite network box of `key` on a site page, or None when it is not a node."""
    t = tree(page)
    nodes = [i for i in find(t, "g", ["node"]) if attr(t, i, "data-item") == key]
    if not nodes:
        return None
    (full,) = find(t, "g", ["full-form"], within=nodes[0])
    meta = texts(t, "text", ["nmeta"], full)[0].split(" · ")
    return {"key": texts(t, "text", ["nid"], full), "prio": meta[0] if len(meta) == 2 else None,
            "title": texts(t, "text", ["ntitle"], full),
            "band": [(text_of(t, j), sorted(set(t.elements[j]["classes"]) - {"band-t"}))
                     for j in find(t, "text", ["band-t"], full)],
            "cues": set(t.elements[nodes[0]]["classes"]) & CUES}


def leaf_boxes(tmp_path):
    """(result, tree of the week page, {(site, key): its first box}) for the leaf_boxes fixture."""
    result, html = fixture_build(tmp_path, "leaf_boxes")
    t = tree(html)
    return result, t, {node: leaf_lines(t, node[1], within=one(t, "id", "days"))
                       for node in placed_nodes(result.plan)}


def first_box(t, lines, site):
    (i,) = [i for i in lines if attr(t, i, "data-site") == site][:1]
    return i


def test_leaf_boxes_fixture_premises(tmp_path):
    """leaf_boxes plants one leaf of each box kind, all placed in the 10 days."""
    result, _ = fixture_build(tmp_path, "leaf_boxes")
    plan = result.plan
    assert plan.status == "planned"
    placed = placed_nodes(plan)
    records = {(e["site"], e["key"]): e for e in plan.leaves}
    leaves = {(site, leaf.key): leaf for site, i in result.inputs.items() for leaf in i.data.leaves}
    slack = {(site, leaf["key"]): leaf["slack"] for site, m in result.page_models.items() for leaf in m["leaves"]}
    kinds = {"Issue-1": 0, "Issue-7": 2, "Issue-8": 4, "Issue-12": 3, "Issue-14": 4, "Issue-10": None,
             "Issue-12345678901234": None, "Issue-11": None, "Issue-13": None}
    for key, want in kinds.items():
        assert ("a", key) in placed, key
        assert slack[("a", key)] == want, key
    assert leaves[("a", "Issue-10")].waits_on_other_site
    assert leaves[("a", "Issue-12")].models is None
    u1 = leaves[("a", "Issue-12345678901234")]
    assert u1.models is None and len(u1.key) > 16 and u1.priority == "urgent!" and len(u1.title) > 40
    u2 = leaves[("a", "Issue-11")].models
    # A thinking name over the 9-character cut, and a checking name no table knows.
    assert (u2.thinking, u2.checking) == ("sonnet-4-5", "inherit") and len(u2.thinking) > 9
    n2 = leaves[("a", "Issue-13")]
    assert records[("a", "Issue-13")]["unestimated"] and n2.models is not None and n2.models.checking is None
    assert leaves[("a", "Issue-14")].title == ""
    assert worksite_box(result.pages["a"], "Issue-14") is not None
    assert (records[("a", "Issue-2")]["state"], records[("a", "Issue-3")]["state"]) == ("in_progress", "stuck")
    rows = [[e["site"] for e in d["lanes_row"]] for d in plan.days if d["lanes_row"]]
    assert any(set(row) == {"a", "b"} and row[0] == "b" for row in rows)


def test_box_matches_worksite_for_boxed_leaves(tmp_path):
    result, t, boxes = leaf_boxes(tmp_path)
    compared = 0
    for (site, key), lines in boxes.items():
        node = worksite_box(result.pages[site], key) if site in result.pages else None
        if node is None or not node["title"]:  # an empty title shows the key on the week (OQ1 (a))
            continue
        for i in [i for i in lines if attr(t, i, "data-site") == site]:
            assert week_box(t, i) == node, (site, key)
        compared += 1
    assert compared >= 6


def test_box_literal_rules_for_unboxed_leaves(tmp_path):
    result, t, boxes = leaf_boxes(tmp_path)
    long_key = "Issue-12345678901234"
    got = week_box(t, first_box(t, boxes[("a", long_key)], "a"))
    assert got["key"] == ["Issue-123456789…"] and got["prio"] == "urgen…"
    assert got["title"] == ["The quick brown fox jumps", "over the lazy…"] and got["band"] == []
    got = week_box(t, first_box(t, boxes[("a", "Issue-11")], "a"))
    assert got["band"] == [("think sonnet-4…", ["m-other"]), ("check inherit", ["m-other"])]
    # Every other unboxed placement, against the rule as the docs state it: no
    # cut reaches these short values, so each text is the value itself.
    leaves = {(site, leaf.key): leaf for site, i in result.inputs.items() for leaf in i.data.leaves}
    subjects = 0
    for (site, key), lines in boxes.items():
        if key in (long_key, "Issue-11") or worksite_box(result.pages[site], key) is not None:
            continue
        leaf = leaves[(site, key)]
        assert len(key) <= 16 and len(leaf.title) <= 25
        tiers = [leaf.models.thinking or None, leaf.models.checking or None] if leaf.models else None
        band = [(f"{label} {tier or 'none'}", [MODEL_CLASSES.get(tier, MODEL_FALLBACK)])
                for label, tier in zip(("think", "check"), tiers)] if tiers and any(tiers) else []
        assert week_box(t, first_box(t, lines, site)) == {
            "key": [key], "prio": leaf.priority, "title": [leaf.title or key], "band": band,
            "cues": week_box(t, first_box(t, lines, site))["cues"]}, (site, key)
        subjects += 1
    assert subjects >= 1


def test_fixture_box_classes(tmp_path):
    _, t, boxes = leaf_boxes(tmp_path)
    classes = [set(t.elements[i]["classes"]) for lines in boxes.values() for i in lines]
    assert set().union(*classes) & set(SLACK_CLASSES) == {"crit", "near", "free"}
    assert any(not c & set(SLACK_CLASSES) for c in classes)
    for cls in ("st-wip", "st-stuck", "unest", "xsite"):
        assert any(cls in c for c in classes), cls


def test_box_keeps_facts_and_data_attrs():
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 1.0)])]),
                           day(HORIZON[1], [block(1, "alpha", [("Issue-1", 1.0)])], weekday="tue")],
                     leaves=[leaf_record("alpha", "Issue-1", unestimated=True, state="stuck", tier="p0",
                                         days=HORIZON[:2])],
                     sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result([("alpha", "Issue-1", "One", "3pr")]), STAMP))
    line = leaf_lines(t, "Issue-1")[0]
    (facts,) = find(t, "p", ["facts"], line)
    assert [(t.elements[j]["classes"], text_of(t, j)) for j in children(t, facts)] == [
        (["unest"], "unestimated (3 pr)"), (["state"], "stuck"), (["tier"], "P0"), (["need"], "need 1, scored"),
        (["basis"], "scored: h: 1"), (["cont"], "continues Tuesday 2026-01-20")]
    assert [attr(t, line, a) for a in ("data-leaf", "data-site", "data-tag", "data-state", "data-tier")] == [
        "Issue-1", "alpha", "scored", "stuck", "p0"]


def test_hand_built_result_draws_boxes_without_slack():
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 2.0), ("Issue-2", 1.0)])])],
                     leaves=[leaf_record("alpha", "Issue-1"), leaf_record("alpha", "Issue-2")],
                     sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result([("alpha", "Issue-1", "One", None)], page_models={}), STAMP))
    lines = leaf_lines(t)
    assert len(lines) == 2
    for i in lines:
        assert len(find(t, "span", ["key"], i)) == len(find(t, "p", ["ttl"], i)) == len(find(t, "span", ["hours"], i)) == 1
        assert not set(t.elements[i]["classes"]) & set(SLACK_CLASSES)


def test_empty_title_boxed_leaf_shows_key(tmp_path):
    result, t, boxes = leaf_boxes(tmp_path)
    (i,) = boxes[("a", "Issue-14")]
    (a,) = find(t, "a", within=i)
    assert attr(t, a, "href") == leaf_href("a", "Issue-14", from_top=True)
    assert texts(t, "span", ["tl"], a) == ["Issue-14"]
    node = worksite_box(result.pages["a"], "Issue-14")
    assert node["title"] == []
    assert {**week_box(t, i), "title": []} == node


def test_placed_leaves_have_page_model_records(tmp_path):
    """Every placed leaf on a site with a page model has a record in it.

    Placed items come from _site_work's open leaves (planner.py), the same
    inputs.scheduled the page model reads; a held-out leaf is never placed,
    since it gets an unplanned reason instead.
    """
    results = [sample_build(tmp_path / "demo")]
    results += [fixture_build(tmp_path / base, base)[0] for base in FIXTURE_NAMES]
    checked = 0
    for result in results:
        records = {(site, leaf["key"]) for site, m in result.page_models.items() for leaf in m["leaves"]}
        for site, key in placed_nodes(result.plan) if result.plan.status == "planned" else []:
            if site in result.page_models:
                assert (site, key) in records, (site, key)
                checked += 1
    assert checked > 0


def test_legend_texts(tmp_path):
    t = tree(demo(tmp_path)[1])
    (legend,) = with_attr(t, "id", "legend")
    assert texts(t, "li", within=legend) == [
        "critical: slack 0 on its own site's network", "near-critical: slack 1\u20133 on its own site's network",
        "P0: in the P0 tier, because its own priority or that of a leaf it blocks, directly or not, is P0"]
    assert [t.elements[i]["classes"] for i in find(t, "i", within=legend)] == [["sw", "crit"], ["sw", "near"]]
    # Between the lines and the day buttons.
    assert one(t, "id", "lines") < legend < one(t, "id", "days")


# --- the box CSS (#170) -----------------------------------------------------------------------

TEMPLATES = Path(__file__).resolve().parents[1] / "gantry" / "templates"
STATE_OF = {cls: state for state, cls in STATE_CLASSES.items()} | {STATE_FALLBACK: "unheard_of"}
# What a box's fill and outline must come to, by hand from the worksite's
# precedence: fill by slack, outline by state, then xsite's colour and width
# over the state's, keeping a held state's dash.
SLACK_LOOK = {"crit": ("var(--crit)", "var(--crit)"), "near": ("var(--near)", "var(--crit)"),
              "free": ("var(--sheet)", "var(--rule)"), None: ("var(--sheet)", "var(--rule)")}
STATE_LOOK = {"st-wip": ("2px", "solid", "var(--ink)"), "st-stuck": ("2.5px", "dashed", "var(--held)"),
              "st-blocked": ("2.5px", "dotted", "var(--held)"), "st-parked": ("2.5px", "dashed", "var(--muted)")}


def expected_look(slack, state, xsite):
    background, colour = SLACK_LOOK[slack]
    width, style = "1px", "solid"
    if state in STATE_LOOK:
        width, style, colour = STATE_LOOK[state]
    if xsite:
        colour, width = "var(--xsite)", "2.5px"
    return {"background": background, "border-color": colour, "border-style": style, "border-width": width}


def css_rules(css):
    """[(selector, [(property, value)])] of a sheet outside its @media blocks, in order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    kept, i = [], 0
    while (at := css.find("@media", i)) != -1:
        kept.append(css[i:at])
        j, depth = css.index("{", at) + 1, 1
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        i = j
    kept.append(css[i:])
    rules = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", "".join(kept)):
        decls = [(m[1], m[2].strip()) for m in re.finditer(r"([\w-]+)\s*:\s*([^;]+)", body)]
        rules += [(sel.strip(), decls) for sel in selectors.split(",")]
    return rules


def cascade(rules, pattern, classes, expand):
    """{property: value} for an element with `classes`, over the rules whose selector fully
    matches `pattern` (its `classes` group a run of .class): the most classes win, then the later."""
    won = {}
    for selector, decls in rules:
        m = re.fullmatch(pattern, selector)
        if m is None:
            continue
        need = set(m["classes"].split(".")[1:])
        if not need <= set(classes):
            continue
        for prop, value in decls:
            for p, v in expand(prop, value):
                if p not in won or len(need) >= won[p][0]:
                    won[p] = (len(need), v)
    return {p: v for p, (_, v) in won.items()}


def week_expand(prop, value):
    if prop == "border":
        width, style, colour = value.split()
        return [("border-width", width), ("border-style", style), ("border-color", colour)]
    return [(prop, value)] if prop in ("background", "border-color", "border-width", "border-style") else []


def template_css(name):
    return re.search(r"<style>(.*)</style>", (TEMPLATES / name).read_text(encoding="utf-8"), flags=re.S)[1]


def week_look(classes):
    return cascade(css_rules(template_css("week.html")), r"\.leaf(?P<classes>(?:\.[\w-]+)*)", classes, week_expand)


def look_key(classes):
    slack = next((c for c in classes if c in SLACK_CLASSES), None)
    state = next(c for c in classes if c.startswith("st-"))
    return slack, state, "xsite" in classes


def test_week_box_css_combinations(tmp_path):
    _, t, boxes = leaf_boxes(tmp_path)
    seen = set()
    for lines in boxes.values():
        for i in lines:
            classes = t.elements[i]["classes"]
            seen.add(look_key(classes))
            assert week_look(classes) == expected_look(*look_key(classes)), classes
    assert len(seen) >= 6
    assert {("crit", "st-wip", False), ("crit", "st-stuck", False), (None, "st-pending", True)} <= seen


def test_week_box_css_full_matrix():
    combos = [(slack, state, xsite) for slack in (*SLACK_CLASSES, None)
              for state in (*STATE_CLASSES.values(), STATE_FALLBACK) for xsite in (False, True)]
    keys = [f"Issue-{n}" for n in range(1, len(combos) + 1)]
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [(key, 1.0) for key in keys])])],
                     leaves=[leaf_record("alpha", key, state=STATE_OF[state]) for key, (_, state, _) in zip(keys, combos)],
                     sites=[site_record("alpha")])
    result = make_result([("alpha", key, "T", None, {"waits_on_other_site": xsite})
                          for key, (_, _, xsite) in zip(keys, combos)],
                         page_models={"alpha": {"leaves": [{"key": key, "sclass": slack}
                                                           for key, (slack, _, _) in zip(keys, combos)]}})
    t = tree(render_week(plan, result, STAMP))
    assert len(combos) == 48
    for key, combo in zip(keys, combos):
        (i,) = leaf_lines(t, key)
        classes = t.elements[i]["classes"]
        assert look_key(classes) == combo, key
        assert week_look(classes) == expected_look(*combo), combo


def test_worksite_css_agrees_with_week_table():
    dash = {None: "solid", "6 3": "dashed", "2 3": "dotted"}
    rules = css_rules(template_css("worksite.html"))

    def fill(prop, value):
        return [("background", value)] if prop == "fill" else []

    def outline(prop, value):
        if prop == "stroke-dasharray":
            return [("border-style", dash[value])]
        return {"stroke": [("border-color", value)], "stroke-width": [("border-width", f"{value}px")]}.get(prop, [])

    for slack in (*SLACK_CLASSES, None):
        for state in (*STATE_CLASSES.values(), STATE_FALLBACK):
            for xsite in (False, True):
                classes = [c for c in (slack, state, "xsite" if xsite else None) if c]
                got = cascade(rules, r"\.node(?P<classes>(?:\.[\w-]+)*) \.box", classes, fill)
                got |= {"border-style": "solid"} | cascade(rules, r"\.node(?P<classes>(?:\.[\w-]+)*) \.outline",
                                                            classes, outline)
                assert got == expected_look(slack, state, xsite), classes


# --- AC 2: the as-of date is not a working day ---------------------------------------------


def test_as_of_not_working_day(tmp_path):
    t = tree(fixture_page(tmp_path, "binds_lanes", config={"as_of": "2026-01-17"}))
    assert with_attr(t, "data-today") == []
    assert len(with_attr(t, "data-day")) == 10
    assert texts(t, "p", ["not-working"]) == [
        "the as-of date 2026-01-17 is not a working day; the plan starts Monday 2026-01-19"]


# --- AC 4: needs, rates, tags and bases ------------------------------------------------------


def test_tags_and_bases(tmp_path):
    alpha = wbs_md(leaf(1, est="2h", h=2), leaf(2, est="2h"))
    t = tree(fixture_page(tmp_path, "binds_lanes", wbs={"alpha": alpha}))
    assert texts(t, "span", ["need"], leaf_lines(t, "Issue-1")[0]) == ["need 2, scored"]
    (line,) = leaf_lines(t, "Issue-2")[:1]
    (need,) = texts(t, "span", ["need"], line)
    assert need.endswith(", estimated")
    (basis,) = texts(t, "span", ["basis"], line)
    assert "no model tiers or flags recorded" in basis
    plan = make_plan(days=[day(HORIZON[0], [block(1, "alpha", [("Issue-1", 1.0), ("Issue-2", 1.0)])])],
                     leaves=[leaf_record("alpha", "Issue-1", need=None, rate=0.3, tag="measured"),
                             leaf_record("alpha", "Issue-2", need=None, rate=0.2, tag="declared")],
                     sites=[site_record("alpha")])
    t = tree(render_week(plan, make_result(), STAMP))
    assert [texts(t, "span", ["need"], leaf_lines(t, key)[0]) for key in ("Issue-1", "Issue-2")] == [
        ["0.3 h per agent hour, measured"], ["0.2 h per agent hour, declared"]]
    assert [attr(t, leaf_lines(t, key)[0], "data-tag") for key in ("Issue-1", "Issue-2")] == ["measured", "declared"]


def test_mapping_line():
    t = tree(render_week(make_plan(), make_result(), STAMP))
    assert texts(t, "p", ["mapping"]) == [
        "attention mapping: 0, 0.1, 0.25, 0.5, 1 operator hours per agent hour for needs 0 to 4, default"]
    mapping = {"values": [0.0, 1e-06, 0.3, 0.6, 20.0], "source": "portfolio_file", "rejected": None}
    t = tree(render_week(make_plan(mapping=mapping), make_result(), STAMP))
    assert texts(t, "p", ["mapping"]) == [
        "attention mapping: 0, 0.000001, 0.3, 0.6, 20 operator hours per agent hour for needs 0 to 4, "
        "from the portfolio file"]
    mapping = {"values": [0.0, 0.1, 0.25, 0.5, 1.0], "source": "default", "rejected": "need 4 is above 1"}
    t = tree(render_week(make_plan(mapping=mapping), make_result(), STAMP))
    assert texts(t, "p", ["mapping"])[1:] == ["the portfolio file's mapping was not used: need 4 is above 1"]


def test_rate_text():
    assert [week.rate_text(r) for r in (1e-06, 20.0, 0.25, 0.0, 0.1)] == ["0.000001", "20", "0.25", "0", "0.1"]


# --- AC 5: the count line -------------------------------------------------------------------


def test_count_line():
    def page(n):
        days = [day(d, [block(1, "alpha", [("Issue-1", 1.0)])]) for d in HORIZON[:3]]
        plan = make_plan(days=days, unestimated_planned=n, sites=[site_record("alpha")],
                         leaves=[leaf_record("alpha", "Issue-1", unestimated=n > 0, days=HORIZON[:3])])
        return tree(render_week(plan, make_result(), STAMP))

    t = page(1)
    count = one(t, "data-unestimated")
    assert (attr(t, count, "data-unestimated"), text_of(t, count)) == ("1", "Includes 1 unestimated leaf")
    assert len(find(t, "span", ["unest"])) == 3
    t = page(2)
    assert text_of(t, one(t, "data-unestimated", "2")) == "Includes 2 unestimated leaves"
    t = page(0)
    assert text_of(t, one(t, "data-unestimated", "0")) == ""


# --- AC 7: binding, rule and capacity ----------------------------------------------------------


@pytest.mark.parametrize(("base", "pool"), [("binds_attention", ATTENTION), ("binds_lanes", LANES)])
def test_binding_on_fixtures(tmp_path, base, pool):
    t = tree(fixture_page(tmp_path, base))
    line = one(t, "data-figure", "binding")
    assert text_of(t, line) == f"binding: {pool} (ran out first on Monday 2026-01-19)"
    assert attr(t, line, "data-source") == "week plan as of 2026-01-19"


def test_no_pool_ran_out(tmp_path):
    t = tree(fixture_page(tmp_path, "continues"))
    assert text_of(t, one(t, "data-figure", "binding")) == "no pool ran out in the planned week"


def test_rule_and_capacity_on_every_fixture(tmp_path):
    ran = 0
    for base in FIXTURE_NAMES:
        t = tree(fixture_page(tmp_path, base))
        assert texts(t, "p", ["rule"]) == [
            "fair share by intended shares (Gantry v1); devagent's forecast (mtibbits/devagent#745) may allocate "
            "differently"], base
        assert texts(t, "p", ["capacity"]) == [
            "capacity from: operator attention hours: portfolio file; agent lanes: portfolio file"], base
        ran += 1
    assert ran == 14


# --- AC 8: share-less sites, notes, P0 and reasons ---------------------------------------------


def shareless_lines(t):
    return texts(t, "p", ["shareless"]) + texts(t, "li", within=one(t, "id", "shareless")) if find(
        t, "p", ["shareless"]) else []


def test_shareless_no_share(tmp_path):
    t = tree(fixture_page(tmp_path, "no_share"))
    assert shareless_lines(t) == [
        "2 sites have no intended share set; they are planned only into capacity left after the sites with shares",
        "c: on every planned day", "d: on every planned day"]


def test_shareless_partial_days():
    plan = make_plan(sites=[site_record("x", shareless=HORIZON[:2]), site_record("y")])
    t = tree(render_week(plan, make_result(pages=("x", "y")), STAMP))
    assert shareless_lines(t) == [
        "1 site has no intended share set; it is planned only into capacity left after the sites with shares",
        "x: on Monday 2026-01-19, Tuesday 2026-01-20"]


def test_no_allocation_note_only(tmp_path):
    result, page = fixture_build(tmp_path, "no_allocation")
    t = tree(page)
    assert shareless_lines(t) == []
    assert texts(t, "li", within=one(t, "id", "notes")) == [note["text"] for note in result.plan.notes]
    assert len(result.plan.notes) == 10


def test_p0_list(tmp_path):
    result, page = fixture_build(tmp_path, "p0")
    t = tree(page)
    box = one(t, "id", "p0")
    lines = find(t, "li", ["p0-leaf"], box)
    expected = [(s["name"], entry["key"]) for s in result.plan.sites for entry in s["p0"]]
    assert [(attr(t, i, "data-site"), attr(t, i, "data-leaf")) for i in lines] == expected
    assert all(find(t, "a", within=i) for i in lines)
    held = {(attr(t, i, "data-site"), attr(t, i, "data-leaf")): texts(t, "span", ["held"], i) for i in lines}
    assert held[("a", "Issue-3")] == ["held by b:Issue-3"]
    assert held[("a", "Issue-1")] == []
    assert "wbs order" not in " ".join(texts(t, "p", within=box))
    assert texts(t, "p", within=box) == [week.P0_CAPTION]


def test_no_p0_box_without_p0(tmp_path):
    assert with_attr(tree(fixture_page(tmp_path, "two_sites")), "id", "p0") == []


@pytest.mark.parametrize("base", ["states", "cross_site", "cross_site_cycle"])
def test_unplanned_reasons_in_words(tmp_path, base):
    result, page = fixture_build(tmp_path, base)
    t = tree(page)
    count = 0
    for s in result.plan.sites:
        box = site_box(t, s["name"])
        for entry in s["unplanned"]:
            if entry["reason"] == "beyond_horizon":
                continue
            (line,) = [i for i in with_attr(t, "data-unplanned", entry["reason"], box)
                       if attr(t, i, "data-leaf") == entry["key"]]
            assert texts(t, "span", ["reason"], line) == [week.REASON_TEXTS[entry["reason"]]]
            assert render.reason_class(entry["reason"]) in t.elements[line]["classes"]
            count += 1
    assert count == sum(1 for s in result.plan.sites for e in s["unplanned"] if e["reason"] != "beyond_horizon")
    assert count > 0


def forty_beyond():
    others = [r for r in UNPLANNED_REASONS if r != "beyond_horizon"]
    unplanned = [(f"Issue-{n}", "beyond_horizon") for n in range(1, 41)]
    unplanned += [(f"Issue-{41 + n}", reason) for n, reason in enumerate(others)]
    plan = make_plan(sites=[site_record("alpha", unplanned=unplanned)])
    result = make_result([("alpha", key, f"Title {key}", None) for key, _ in unplanned])
    return plan, result, others


def test_beyond_horizon_count():
    plan, result, others = forty_beyond()
    t = tree(render_week(plan, result, STAMP))
    count = one(t, "data-beyond", "40")
    assert text_of(t, count) == "40 leaves beyond the 10 days"
    (listed,) = find(t, "ul", ["beyond-list"])
    items = with_attr(t, "data-unplanned", "beyond_horizon", listed)
    assert len(items) == 40 and all(find(t, "a", within=i) for i in items)
    assert all("why-beyond" in t.elements[i]["classes"] for i in items)
    words = [i for i in with_attr(t, "data-unplanned") if attr(t, i, "data-unplanned") != "beyond_horizon"]
    assert [attr(t, i, "data-unplanned") for i in words] == others
    assert [texts(t, "span", ["reason"], i) for i in words] == [[week.REASON_TEXTS[r]] for r in others]
    assert all(attr(t, find(t, "a", within=i)[0], "href").startswith("sites/alpha.html#leaf-") for i in words)


def test_beyond_only_site_has_no_nothing_line():
    plan = make_plan(sites=[site_record("alpha", unplanned=[("Issue-1", "beyond_horizon")])])
    t = tree(render_week(plan, make_result(), STAMP))
    assert text_of(t, one(t, "data-beyond", "1")) == "1 leaf beyond the 10 days"
    assert texts(t, "p", ["none"], one(t, "id", "unplanned")) == []


def test_beyond_extremes(tmp_path):
    t = tree(fixture_page(tmp_path, "states"))
    b = site_box(t, "b")
    assert texts(t, "p", ["none"], b) == ["no unplanned leaves"]
    assert with_attr(t, "data-beyond", within=b) == [] and find(t, "button", within=b) == []
    t = tree(fixture_page(tmp_path, "no_share"))
    c = site_box(t, "c")
    assert text_of(t, one(t, "data-beyond", within=c)) == "1 leaf beyond the 10 days"
    t = tree(demo(tmp_path / "demo")[1])
    orchard = site_box(t, "orchard")
    assert with_attr(t, "data-beyond", within=orchard) == []
    assert len(with_attr(t, "data-unplanned", within=orchard)) == 5


def test_day_extremes(tmp_path):
    result, page = fixture_build(tmp_path, "binds_lanes")
    t = tree(page)
    blockless = [d["date"] for d in result.plan.days if not d["blocks"]]
    assert len(blockless) == 9
    none_days = [attr(t, i, "data-day") for i in with_attr(t, "data-day") if find(t, "p", ["none"], i)]
    assert none_days == blockless
    assert all(not with_attr(t, "data-block", within=i) for i in with_attr(t, "data-day")
               if attr(t, i, "data-day") in blockless)
    assert all(texts(t, "p", ["none"], i) == ["no block planned"] for i in with_attr(t, "data-day")
               if attr(t, i, "data-day") in blockless)
    result, page = demo(tmp_path / "demo")
    t = tree(page)
    lanes_days = [d["date"] for d in result.plan.days if d["lanes_row"]]
    assert len(lanes_days) == 2
    assert [attr(t, t.elements[i]["parent"], "data-day") for i in with_attr(t, "data-lanes")] == lanes_days
    assert all(len(find(t, "p", ["left"], i)) == 1 for i in with_attr(t, "data-day"))


def test_shareless_one_site(tmp_path):
    t = tree(demo(tmp_path)[1])
    assert shareless_lines(t) == [
        "1 site has no intended share set; it is planned only into capacity left after the sites with shares",
        "lighthouse: on every planned day"]


def test_unknown_reason_as_text():
    plan = make_plan(sites=[site_record("alpha", unplanned=[("Issue-1", "later_reason")])])
    t = tree(render_week(plan, make_result(), STAMP))
    (line,) = with_attr(t, "data-unplanned", "later_reason")
    assert texts(t, "span", ["reason"], line) == ["not planned: later_reason"]
    assert "why-other" in t.elements[line]["classes"]


# --- AC 10: per site --------------------------------------------------------------------------


def site_rows(t):
    table = one(t, "id", "per-site")
    return [[text_of(t, c) for c in find(t, "td", within=r)] for r in find(t, "tr", within=table)
            if find(t, "td", within=r)]


def test_per_site_rows(tmp_path):
    t = tree(fixture_page(tmp_path, "two_sites"))
    assert texts(t, "th", within=one(t, "id", "per-site")) == [
        "site", "share on Monday 2026-01-19", "operator attention hours", "agent lanes"]
    assert site_rows(t) == [["a", "50%", "28 h charged, 30 h target", "26.75 h charged, 28.63 h target"],
                            ["b", "50%", "32 h charged, 30 h target", "30.5 h charged, 28.63 h target"]]
    t = tree(fixture_page(tmp_path, "states"))
    assert site_rows(t) == [["a", "50%", "1.75 h charged, 0.88 h target", "15 h charged, 7.5 h target"],
                            ["b", "50%", "2.15 h charged, 3.03 h target", "12 h charged, 19.5 h target"]]
    plan = make_plan(sites=[site_record("x", share=100 / 3), site_record("y", shareless=HORIZON)])
    t = tree(render_week(plan, make_result(pages=("x", "y")), STAMP))
    assert [row[:2] for row in site_rows(t)] == [["x", "33.33%"], ["y", "no intended share set"]]


def test_share_text():
    assert [week.share_text(s) for s in (50.0, 100 / 3, 200 / 3, 100.0)] == ["50%", "33.33%", "66.67%", "100%"]


# --- AC 11: no plan -----------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "case", "changes"), NO_PLAN_CASES, ids=[f"{k}: {c}" for k, c, _ in NO_PLAN_CASES])
def test_no_plan_pages(tmp_path, key, case, changes):
    result, page = fixture_build(tmp_path, "binds_lanes", **changes)
    assert result.plan.status == "no_plan"
    t = tree(page)
    assert text_of(t, one(t, "id", "no-plan")) == "no week plan: " + NO_PLAN[key]
    assert texts(t, "p", ["stamp"]) == [result.stamp]
    for name in ("data-day", "data-figure", "data-unestimated"):
        assert with_attr(t, name) == [], name
    assert with_attr(t, "id", "lines") == [] and with_attr(t, "id", "per-site") == []
    # test_no_plan_has_no_legend (#170): the legend is shown only with a plan.
    assert with_attr(t, "id", "legend") == []


def test_no_plan_cases_cover_every_reason():
    assert len(NO_PLAN_CASES) == 15
    assert {key for key, _, _ in NO_PLAN_CASES} == set(NO_PLAN)


# --- safety, attributes and invariants -----------------------------------------------------------


def built_week_pages(tmp_path):
    plan, result, _ = forty_beyond()
    pages = {"demo": demo(tmp_path / "demo")[1], "forty-beyond": render_week(plan, result, STAMP),
             "no-plan": fixture_page(tmp_path, "binds_lanes", config={"portfolio_file": None})}
    pages.update({base: fixture_page(tmp_path, base) for base in FIXTURE_NAMES})
    return pages


def test_new_attrs_all_used(tmp_path):
    plan, result, _ = forty_beyond()
    used = set()
    for page in (demo(tmp_path)[1], render_week(plan, result, STAMP)):
        used |= {name for el in tree(page).elements for name in el["attrs"]}
    assert set(NEW_ATTRS) <= used
    assert set(NEW_ATTRS) <= set(RULES["allowed_attrs"])


def test_render_does_not_change_plan(tmp_path):
    result = sample_build(tmp_path)
    before = copy.deepcopy(asdict(result.plan))
    render_week(result.plan, result, result.stamp)
    assert asdict(result.plan) == before


def test_page_is_safe_static(tmp_path):
    pages = built_week_pages(tmp_path)
    assert len(pages) == 17
    for name, page in pages.items():
        assert_no_external_resources(page)
        assert_page_safe(page, [])
        assert page.startswith("<!doctype html>\n"), name
        assert page.count('<p class="label stamp">') == 1, name


def test_data_block_covers_named_leaves(tmp_path):
    result, page = demo(tmp_path)
    data = assert_page_safe(page, [])
    assert set(data) == {"stamp", "plan", "leaves", "not_built"}
    assert data["plan"] == json.loads(json.dumps(asdict(result.plan)))
    named = {(entry["site"], entry["key"]) for entry in result.plan.leaves}
    named |= {(s["name"], e["key"]) for s in result.plan.sites for e in [*s["unplanned"], *s["p0"]]}
    assert {(site, key) for site, key, *_ in data["leaves"]} == named
    # Each named leaf's priority and model tiers, from the inputs leaf the box reads (#170).
    leaves = {(site, leaf.key): leaf for site, i in result.inputs.items() for leaf in i.data.leaves}
    entries = {(site, key): rest for site, key, *rest in data["leaves"]}
    assert all(len(rest) == 5 for rest in entries.values())
    for site, key in placed_nodes(result.plan):
        leaf = leaves[(site, key)]
        tiers = [None, None]
        if leaf.models is not None and (leaf.models.thinking or leaf.models.checking):
            tiers = [leaf.models.thinking or None, leaf.models.checking or None]
        assert entries[(site, key)][2:] == [leaf.priority, *tiers], (site, key)
    assert any(rest[3] is not None for rest in entries.values())
    assert data["not_built"] == [["bakery", "no WBS.md in the planning directory"],
                                 ["marsh", "WBS.md is not valid UTF-8 at byte 88"]]


@pytest.mark.parametrize("name", ["demo", "forty-beyond", "hostile"])
def test_week_classes_come_from_fixed_sets(tmp_path, name):
    plan, result, _ = forty_beyond()
    page = {"demo": lambda: demo(tmp_path)[1], "forty-beyond": lambda: render_week(plan, result, STAMP),
            "hostile": lambda: render_week(*hostile_plan_and_result(), STAMP)}[name]()
    used = {c for el in tree(page).elements for c in el["classes"]}
    assert used - ALLOWED_CLASSES == set()


def test_visible_text_has_no_record_markup(tmp_path):
    # Titles are text: a demo page shows no raw angle bracket from a record.
    assert "<script" not in visible_text(demo(tmp_path)[1])


# --- the page script: 5 or 10 days, the beyond lists (AC 6, AC 9; D3) ------------------------


def write(tmp_path, page, name="week.html"):
    path = tmp_path / name
    path.write_bytes(page.encode("utf-8"))
    return path


def shown_days(page):
    return [("gone" not in d.classes) for d in page.query("[data-day]")]


def pressed(page):
    return {b.attrs["data-value"]: b.attrs.get("aria-pressed") for b in page.query("button[data-control]")
            if b.attrs["data-control"] == "days"}


def test_days_control_on_demo(node, tmp_path):
    _, html = demo(tmp_path / "demo")
    with open_page(write(tmp_path, html)) as page:
        assert shown_days(page) == [True] * 5 + [False] * 5
        assert pressed(page) == {"5": "true", "10": "false"}
        first = [(e.tag, e.attrs) for e in page.query("[data-block], li[data-leaf]")]
        page.drive("days", "10")
        assert shown_days(page) == [True] * 10
        assert pressed(page) == {"5": "false", "10": "true"}
        page.drive("days", "5")
        assert shown_days(page) == [True] * 5 + [False] * 5
        assert pressed(page) == {"5": "true", "10": "false"}
        assert [(e.tag, e.attrs) for e in page.query("[data-block], li[data-leaf]")] == first
        assert all("gone" not in b.classes for b in page.query("button[data-control]"))


def test_count_line_unchanged_by_control(node, tmp_path):
    _, html = demo(tmp_path / "demo")
    with open_page(write(tmp_path, html)) as page:
        before = page.query("[data-unestimated]")
        page.drive("days", "10")
        assert page.query("[data-unestimated]") == before
        assert len(before) == 1


def test_beyond_control(node, tmp_path):
    plan, result, _ = forty_beyond()
    with open_page(write(tmp_path, render_week(plan, result, STAMP))) as page:
        (listed,) = page.query("#beyond-1")
        assert "gone" in listed.classes
        (button,) = [b for b in page.query("button[data-control]") if b.attrs["data-control"] == "beyond-1"]
        assert (button.attrs["aria-expanded"], button.text) == ("false", "show or hide")
        page.drive("beyond-1", "toggle")
        (listed,) = page.query("#beyond-1")
        assert "gone" not in listed.classes and len(page.query("#beyond-1 li")) == 40
        assert [b.attrs["aria-expanded"] for b in page.query("button[data-control]")
                if b.attrs["data-control"] == "beyond-1"] == ["true"]
        page.drive("beyond-1", "toggle")
        assert "gone" in page.query("#beyond-1")[0].classes
        assert [b.attrs["aria-expanded"] for b in page.query("button[data-control]")
                if b.attrs["data-control"] == "beyond-1"] == ["false"]


def test_static_page_hides_only_controls(tmp_path):
    plan, result, _ = forty_beyond()
    demo_html = demo(tmp_path / "demo")[1]
    for html in (demo_html, render_week(plan, result, STAMP)):
        t = tree(html)
        assert ".gone { display: none; }" in texts(t, "style")[0]
        controls = with_attr(t, "data-control")
        assert controls and all("gone" in t.elements[i]["classes"] for i in controls)
        assert all(t.elements[i]["tag"] == "button" for i in controls)
        hidden = [i for i in find(t, classes=["gone"]) if i not in controls]
        assert hidden == []
    t = tree(demo_html)
    assert sorted(attr(t, i, "data-value") for i in with_attr(t, "data-control", "days")) == ["10", "5"]


# --- hostile values (AC 14) ------------------------------------------------------------------


# The box's own fields (#170), planted on the key leaf: a priority and a
# thinking model, each a HOSTILE member made unique, and a checking model
# short enough to render uncut; <b> is an allowed tag, so only the live
# page shows it was not made an element.
PRIO = HOSTILE[3] + " prio"
THINK = HOSTILE[5] + " think"
CHECK = "<b>x</b>"


def hostile_plan_and_result(page_models=None):
    """One plan and result planting HOSTILE by index: est [0] (in no other
    plant); title and note [1]; key and mapping.rejected [2]; site name and a
    beyond title [3]; reserved label and a waits_on_unplanned title [4];
    unknown reason [5], tag [6], state [7], tier [8]; basis [9]; pool name [10].
    The site name also reaches the lanes row, the P0 list (as site and
    holder) and the share-less lines; a not-built site carries [3] and [4];
    the mapping source is [5] and the allocation rule [9]. The key leaf
    also carries PRIO, THINK and CHECK."""
    h = HOSTILE
    site, key = h[3], h[2]
    assert not any(h[0] in other for other in h[1:])
    plants = [*h, PRIO, THINK, CHECK]
    for value in (PRIO, THINK):
        assert sum(value in other for other in plants) == 1
    plan = make_plan(
        mapping={"values": [0.0, 0.1, 0.25, 0.5, 1.0], "source": h[5], "rejected": h[2]},
        allocation_rule=h[9],
        pools=[{"name": h[10], "capacity_source": "portfolio file", "days_run_out": [HORIZON[0]]},
               {"name": LANES, "capacity_source": "portfolio file", "days_run_out": []}],
        binding={"pool": h[10], "first_day": HORIZON[0], "text": h[10]},
        notes=[{"kind": "no_allocation", "date": HORIZON[0], "projects": [], "text": h[1]}],
        days=[day(HORIZON[0], [block(1, site, [(key, 1.0)])], [lane(site, "Issue-L", 4.0)],
                  reserved=[{"start": "09:00", "hours": 1.0, "label": h[4]}])],
        leaves=[leaf_record(site, key, tag=h[6], state=h[7], tier=h[8], basis=h[9], unestimated=True),
                leaf_record(site, "Issue-L", need=0, rate=0.0)],
        sites=[site_record(site, unplanned=[("Issue-B", "beyond_horizon"), ("Issue-W", "waits_on_unplanned"),
                                            ("Issue-U", h[5])],
                           p0=[{"key": key, "held_by": [site]}], shareless=[HORIZON[1]])],
        unestimated_planned=1)
    planted = {"priority": PRIO, "models": Models(THINK, CHECK, "estimates_file")}
    result = make_result([(site, key, h[1], h[0], planted), (site, "Issue-B", h[3], None),
                          (site, "Issue-W", h[4], None)],
                         pages=(site,), not_built=[(h[3], h[4])], page_models=page_models)
    return plan, result


def test_week_hostile(node, tmp_path):
    plan, result = hostile_plan_and_result()
    html = render_week(plan, result, STAMP)
    assert_page_safe(html, [*HOSTILE, PRIO, THINK, CHECK])
    assert_no_external_resources(html)
    t = tree(html)
    assert texts(t, "span", ["reason"]).count("not planned: " + HOSTILE[5]) == 1
    (line,) = leaf_lines(t, HOSTILE[2])
    assert {"tag-other", "st-other", "tier-other"} <= set(t.elements[line]["classes"])
    assert texts(t, "span", ["unest"], line) == ["unestimated"]
    assert texts(t, "h4") == [HOSTILE[3]]
    assert texts(t, "span", ["held"]) == ["held by " + HOSTILE[3]]
    with open_page(write(tmp_path, html)) as page:
        page.drive("days", "10")
        page.drive("beyond-1", "toggle")
        # The cut priority and thinking model never render whole; CHECK renders as text.
        assert page.planted_nodes([*HOSTILE, PRIO, THINK, CHECK]) == Planted([], [], [HOSTILE[0], PRIO, THINK])


def test_week_hostile_page_model_path():
    plan, result = hostile_plan_and_result({HOSTILE[3]: {"leaves": [{"key": HOSTILE[2], "sclass": "crit"}]}})
    html = render_week(plan, result, STAMP)
    assert_page_safe(html, [*HOSTILE, PRIO, THINK, CHECK])
    t = tree(html)
    (line,) = leaf_lines(t, HOSTILE[2])
    assert "crit" in t.elements[line]["classes"]


def test_week_hostile_no_plan(node, tmp_path):
    plan = make_plan(status="no_plan", reason=HOSTILE[5], horizon_days=[], planned_week=[])
    html = render_week(plan, make_result(), STAMP)
    assert_page_safe(html, [HOSTILE[5]])
    with open_page(write(tmp_path, html)) as page:
        assert page.planted_nodes([HOSTILE[5]]) == Planted([], [], [])


# --- the demo build (AC 12) --------------------------------------------------------------------


def test_demo_week_page_byte_identical(tmp_path, monkeypatch):
    from helpers_config import demo_build

    pages = []
    for n in (1, 2):
        code, out, _, stderr = demo_build(tmp_path / f"run{n}", monkeypatch)
        assert code == 0, stderr
        pages.append((out / "week.html").read_bytes())
    assert pages[0] == pages[1]


# --- the docs (AC 17) ---------------------------------------------------------------------


def week_texts():
    """Every text the page can show, as week.py states it."""
    found = {name: value for name, value in vars(week).items()
             if name.isupper() and isinstance(value, str) and name != "DOT"}  # DOT is the worksite page's
    for table in ("STATE_TEXTS", "MAPPING_SOURCES", "REASON_TEXTS"):
        found.update({f"{table}[{key}]": value for key, value in getattr(week, table).items()})
    found.update({f"DAYS_BUTTONS[{value}]": label for value, label in week.DAYS_BUTTONS})
    found["LINK"] = week.LINK[0]
    return found


def test_week_doc_states_texts():
    from helpers_docs import section
    from test_samples import ROOT

    text = section((ROOT / "docs" / "week-plan.md").read_bytes().decode("utf-8"), "## The week view page")
    assert text is not None
    flat = " ".join(text.split())
    texts_ = week_texts()
    assert len(texts_) >= 60
    missing = [name for name, value in texts_.items() if f"`{' '.join(value.split())}`" not in flat]
    assert missing == []
    assert "[Unplanned leaves](#unplanned-leaves)" in flat
