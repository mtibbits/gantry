"""The path to a selected requirement's work (#77): gantry/path.py, the
`path` record on each trace row, the path box and its marks.

The model tests need no Node; the page-script tests take the `node` fixture.
Sample literals are hand-derived from samples/lighthouse/.
"""

import copy
import re
import random
import time
from dataclasses import replace

import pytest

from gantry import build
from gantry.config import load_config
from gantry.inputs import scheduled
from gantry.path import build_paths, heaviest, path_set, work_order
from gantry.schedule import chain_hours, plan
from gantry.sites import config_roots, load_sites
from gantry.trace import build_site_trace, load_catalog, page_trace
from gantry.views import worksite
from gantry.model import HOURS_PER_DAY, ForeignOutcome, SiteData
from gantry.render import CLASS_TOKENS
from gantry.views.worksite import page_model, plan_finish_day
from page_safety import HOSTILE, _strings, assert_no_external_resources, assert_page_safe
from page_script.harness import Planted, open_page, run_function
from sample_roles import READABLE, SMALL
from test_build import heading_slugs
from test_own_requirements import own_set
from test_samples import GANTRY_SAMPLE, ROOT
from test_schedule_agreement import constructed, it, js_plan, random_dags
from test_trace_page import (CYCLE, CYCLE_REQS, EMPTY_MARKS, ONE_LEAF, built, by_id, hand_trace,  # noqa: F401
                             hostile_leaf, hostile_trace_site, installation, lit_counts, marked, marks_after, render_traced, row,
                             sample, sample_page, select, trace_of)
from test_worksite_page import STRUCTURAL, tree


def site_read(config_path, site):
    """(page_trace, SiteData) of one site, read and joined as the build does."""
    config = load_config(config_path)
    roots = config_roots(config)
    sites = load_sites(config, roots)[0]
    catalog = load_catalog(roots, config, sites)
    inputs, reasons = build.read_sites(config, sites, roots=roots)
    assert site in inputs, reasons
    data = inputs[site].data
    return page_trace(build_site_trace(site, data.leaves, catalog), catalog, data), data


# --- Task 1: the sample path --------------------------------------------------------


def test_sample_has_a_path():
    page, data = site_read(GANTRY_SAMPLE, SMALL)
    work = {leaf.key for leaf in scheduled(data)}
    found = {}
    for r in page["requirements"]:
        lit = {k for k, _ in r["light"]}
        into = {(a, b) for a, b in data.links if b in lit and a in work and a not in lit}
        if into:
            found[r["label"]] = into
    assert found == {label: {("Issue-12", "Issue-10")} for label in ("UC-3", "SR-1", "FR-1")}


# --- Task 2: the set, the order, the chain -------------------------------------------


def lit_of(*pairs):
    return [list(p) for p in pairs]


def one_row(items, lit, held=(), finished=0, *, unestimated=(), links=None, foreign=None):
    """build_paths over a single row "R"; links default to the items' preds."""
    if links is None:
        links = [(p, item["key"]) for item in items for p in item["preds"]]
    return build_paths({"R": (lit, list(held), finished)}, items, unestimated=set(unestimated), links=links,
                       foreign=foreign or {})["R"]


def test_walk_collects_scheduled_predecessors():
    items = [it("C", 1, ix=0), it("A", 1, ["C"], ix=1), it("B", 1, ix=2), it("T", 1, ["A", "B"], ix=3),
             it("X", 1, ix=4)]
    p = one_row(items, lit_of(("T", "direct")))
    assert {k for k, _ in p.order} == {"T", "A", "B", "C"}
    assert dict(p.order) == {"T": "direct", "A": "upstream", "B": "upstream", "C": "upstream"}
    assert p.targets == ("T",)


def test_order_respects_waits_and_priority():
    items = [it("C", 1, eff=3, ix=0), it("B", 1, eff=1, ix=1), it("A", 1, ["C"], ix=2),
             it("T", 1, ["A", "B"], ix=3)]
    p = one_row(items, lit_of(("T", "direct")))
    keys = [k for k, _ in p.order]
    assert keys == ["B", "C", "A", "T"]
    for item in items:
        assert all(keys.index(pred) < keys.index(item["key"]) for pred in item["preds"])


def test_chain_weighs_hours():
    items = [it("a", 1, ix=0), it("b", 1, ["a"], ix=1), it("c", 1, ["b"], ix=2), it("big", 10, ix=3),
             it("T", 1, ["c", "big"], ix=4)]
    p = one_row(items, lit_of(("T", "direct")))
    assert p.chain == ("big", "T")
    assert p.chain_hours == 11.0


def test_unlinked_target_is_its_own_chain():
    p = one_row([it("T", 4)], lit_of(("T", "direct")))
    assert p.chain == ("T",) and p.chain_hours == 4.0
    assert p.order == (("T", "direct"),)


def test_target_upstream_of_target_stays_target():
    items = [it("A", 1, ix=0), it("T", 1, ["A"], ix=1)]
    p = one_row(items, lit_of(("A", "indirect"), ("T", "direct")))
    assert dict(p.order) == {"A": "indirect", "T": "direct"}


def test_chain_hours_agree_with_schedule():
    for name, items, _ in constructed():
        keys, total = heaviest(items)
        assert total == chain_hours(items), name
        by_key = {item["key"]: item for item in items}
        added = 0.0
        for i, k in enumerate(keys):
            if i:
                assert keys[i - 1] in by_key[k]["preds"], name
            added += by_key[k]["hours"]
        assert added == total, name


def test_sub_plan_is_closed():
    for items, _ in random_dags():
        links = [(p, item["key"]) for item in items for p in item["preds"]]
        keys = [item["key"] for item in items]
        for targets in (keys[-1:], keys[::3], keys[1::2]):
            members = path_set(targets, [], links, set(keys))
            subset = [item for item in items if item["key"] in members]
            assert all(p in members for item in subset for p in item["preds"])
            order = work_order(subset)
            assert sorted(order) == sorted(members)


def test_sub_plan_is_closed_on_samples():
    for site in sorted(READABLE):
        page, site_data = site_read(GANTRY_SAMPLE, site)
        items = page_model(site, site_data, default_unit_hours=6)["schedule"]["items"]
        keys = {item["key"] for item in items}
        for r in page["requirements"]:
            members = path_set([k for k, _ in r["light"]], [k for k, *_ in r["held"]], site_data.links, keys)
            subset = [item for item in items if item["key"] in members]
            assert sorted(work_order(subset)) == sorted(members), (site, r["id"])


def test_totals_count_unestimated():
    items = [it("A", 0.1, ix=0), it("B", 0.2, ["A"], ix=1), it("T", 0.3, ["B"], ix=2)]
    p = one_row(items, lit_of(("T", "direct")), unestimated={"A", "T", "X"})
    expected = 0.0
    for item in items:
        expected += item["hours"]
    assert p.hours == expected and p.unestimated == 2
    assert one_row(items, lit_of(("T", "direct"))).unestimated == 0


def test_long_chain_needs_no_recursion():
    n = 5000
    items = [it(f"k{i}", 1, [f"k{i - 1}"] if i else [], ix=i) for i in range(n)]
    links = [(f"k{i - 1}", f"k{i}") for i in range(1, n)]
    assert len(path_set([f"k{n - 1}"], [], links, {item["key"] for item in items})) == n
    keys, total = heaviest(items)
    assert len(keys) == n and total == float(n)


def test_walk_terminates_on_cycle():
    # H1 and H2 are held out in a cycle; H1 also waits on scheduled S.
    links = [("H2", "H1"), ("H1", "H2"), ("S", "H1")]
    assert path_set([], ["H1"], links, {"S"}) == {"S"}


def test_heaviest_tie_takes_lower_wbs_index():
    # Two equal-hours ends, and two equal-hours predecessors.
    items = [it("r", 90, ix=1), it("late", 6, ["r"], ix=4), it("early", 6, ["r"], ix=2)]
    assert heaviest(items) == (("r", "early"), 96.0)
    items = [it("p2", 5, ix=3), it("p1", 5, ix=1), it("T", 1, ["p2", "p1"], ix=5)]
    assert heaviest(items) == (("p1", "T"), 6.0)


def test_heaviest_tie_on_hostile_site(tmp_path):
    page, trace, planted, first = hostile_trace_site(tmp_path)
    items = assert_page_safe(page, planted)["schedule"]["items"]
    lit = {k for k, _ in row(trace, first)["light"]}
    keys, total = heaviest([item for item in items if item["key"] in lit])
    assert (keys, total) == (("Issue-1", "Issue-2"), 96.0)


# --- Task 3: states and reasons -------------------------------------------------------


def test_held_serving_leaf_is_a_reason_and_feeds_upstream():
    # H waits on held-out H2, H2 on scheduled S.
    items = [it("S", 5, ix=0)]
    p = one_row(items, [], held=["H"], links=[("H2", "H"), ("S", "H2")])
    assert p.order == (("S", "upstream"),) and p.targets == ()
    assert p.reasons == (("held", "H", None),) and p.state == "path"
    assert p.hours == p.chain_hours == 5.0 and p.chain == ("S",)


def test_held_leaf_with_no_scheduled_work():
    p = one_row([], [], held=["H"], links=[])
    assert p.order == () and p.hours == 0 and p.chain == ()
    assert p.reasons == (("held", "H", None),)


def test_foreign_waits_of_path_leaves_only():
    items = [it("U", 1, ix=0), it("T", 1, ["U"], ix=1), it("N", 1, ix=2)]
    p = one_row(items, lit_of(("T", "direct")), held=["H"],
                foreign={"U": ["beta:Issue-9 (on another site)"], "N": ["beta:Issue-8 (on another site)"]})
    assert p.reasons == (("held", "H", None), ("foreign", ("U",), "beta:Issue-9 (on another site)"))


def test_row_foreign_once():
    # One ancestor row's entry reaches three path leaves: one reason, naming all three.
    items = [it("A", 1, ix=0), it("B", 1, ["A"], ix=1), it("T", 1, ["B"], ix=2)]
    text = "beta:Issue-8 (on another site)"
    p = one_row(items, lit_of(("T", "direct")), foreign={"A": [text], "B": [text], "T": [text, "x"]})
    assert p.reasons == (("foreign", ("A", "B", "T"), text), ("foreign", ("T",), "x"))
    assert [worksite._reason_text(r) for r in p.reasons] == [f"A, B, T wait on {text}", "T waits on x"]


def test_empty_states():
    unserved = one_row([], [], finished=0)
    finished = one_row([], [], finished=2)
    assert (unserved.state, finished.state) == ("unserved", "finished")
    for p in (unserved, finished):
        assert p.order == () and p.targets == () and p.reasons == ()
        assert not hasattr(p, "notes")


def test_not_read_and_other_site_reasons():
    # #21: the sites not read are the last reason, and only on a row the installation trace covers.
    items = [it("T", 1)]
    serving = {"R": ([["T", "direct"]], [], 0)}

    def built(**kw):
        return build_paths(serving, items, unestimated=set(), links=[], foreign={},
                           **kw)["R"]
    p = built(sites_not_read=("marsh", "bakery"), other_serving={"R": [("harbor", "Issue-4"), ("bay", None)]})
    assert p.reasons == (("other_site", "harbor", "Issue-4"), ("other_site", "bay", None),
                         ("not_read", ("marsh", "bakery"), None))
    assert built(sites_not_read=(), other_serving={"R": []}).reasons == ()
    assert built(sites_not_read=("marsh",)).reasons == ()
    assert built(sites_not_read=("marsh",), other_serving={"Q": []}).reasons == ()
    assert built().reasons == ()
    assert worksite._reason_text(("not_read", ("marsh", "bakery"), None)) == "sites not read: marsh, bakery"


def test_not_read_in_empty_state_is_a_reason():
    def built(**kw):
        return build_paths({"R": ([], [], 0)}, [], unestimated=set(), links=[], foreign={},
                           sites_not_read=("marsh",), **kw)["R"]
    p = built(other_serving={"R": []})
    assert p.state == "unserved" and p.reasons == (("not_read", ("marsh",), None),)
    assert built().reasons == ()


def test_other_site_only_is_path():
    p = build_paths({"R": ([], [], 1)}, [], unestimated=set(), links=[], foreign={},
                    other_serving={"R": [("harbor", "Issue-4")]})["R"]
    assert p.state == "path" and p.order == ()
    assert p.reasons == (("other_site", "harbor", "Issue-4"),)


# --- Task 4: the view: `path` on every row, the texts and E -------------------------

FR1 = "# R\n\n## Functional requirements\n\n- FR-1: One.\n"


def site_model(tmp_path, wbs, reqs=FR1, name="alpha"):
    """The page model of site alpha, read as a --site build reads it (no #68 resolution)."""
    config_path = installation(tmp_path, {name: wbs}, {name: reqs})
    return render_traced(config_path, name)[1]


def path_of(m, rid):
    return row(m["trace"], rid)["path"]


def test_sample_fr1_path(tmp_path):
    m = render_traced(GANTRY_SAMPLE, SMALL)[1]
    p = path_of(m, "lighthouse:FR-1")
    assert p["order"] == [["Issue-7", "direct"], ["Issue-12", "upstream"], ["Issue-10", "direct"]]
    assert p["lines"] == ["3 leaves, 108 agent-hours to finish its serving work",
                          "Longest chain: 102 agent-hours, about 17 working days"]
    assert p["chain"] == ["Issue-7", "Issue-10"] and p["targets"] == ["Issue-7", "Issue-10"]
    assert p["end_day"] == 17 and p["incomplete"] == []
    sr1 = path_of(m, "lighthouse:SR-1")
    assert [k for k, mark in sr1["order"] if mark == "upstream"] == ["Issue-12"]


def test_fixture_site_path_set_and_order(tmp_path):
    wbs = ("# Alpha\n\n"
           "- [x] Done {issue: Issue-1}\n"
           "- [ ] Third {issue: Issue-2, est: 1d}\n"
           "- [ ] Urgent {issue: Issue-3, est: 1d, priority: P1}\n"
           "- [ ] Middle {issue: Issue-4, est: 1d, depends_on: [Issue-2]}\n"
           "- [ ] Target {issue: Issue-5, est: 1d, depends_on: [Issue-3, Issue-4, Issue-1], "
           "satisfies: alpha:FR-1}\n"
           "- [ ] Apart {issue: Issue-6, est: 1d}\n")
    m = site_model(tmp_path, wbs)
    p = path_of(m, "alpha:FR-1")
    keys = [k for k, _ in p["order"]]
    assert keys == ["Issue-3", "Issue-2", "Issue-4", "Issue-5"]
    assert [mark for _, mark in p["order"]] == ["upstream", "upstream", "upstream", "direct"]
    items = {item["key"]: item for item in m["schedule"]["items"]}
    for k in keys:
        assert all(keys.index(pred) < keys.index(k) for pred in items[k]["preds"])


def test_use_case_path_from_serving_set(tmp_path):
    reqs = ("# R\n\n## Use cases\n\n- UC-1: Needs FR-1. {needs: [FR-1]}\n\n"
            "## Functional requirements\n\n- FR-1: Needed.\n")
    wbs = ("# Alpha\n\n- [ ] A {issue: Issue-1, est: 1d, satisfies: alpha:UC-1}\n"
           "- [ ] B {issue: Issue-2, est: 1d, depends_on: [Issue-1], satisfies: alpha:FR-1}\n")
    m = site_model(tmp_path, wbs, reqs)
    uc1 = row(m["trace"], "alpha:UC-1")
    assert uc1["path"]["order"] == [["Issue-1", "direct"], ["Issue-2", "indirect"]] == uc1["light"]


def test_heading_counts_unestimated_and_carries_label(tmp_path):
    m = site_model(tmp_path, "# Alpha\n\n- [ ] No est {issue: Issue-1, satisfies: alpha:FR-1}\n")
    assert path_of(m, "alpha:FR-1")["lines"][0].endswith(worksite.PATH_UNEST.format(n=1))
    assert "agent capacity only" in m["texts"]["label"]


def lone(tmp_path, est):
    return path_of(site_model(tmp_path / est, f"# Alpha\n\n- [ ] T {{issue: Issue-1, est: {est}, "
                                                   "satisfies: alpha:FR-1}\n"), "alpha:FR-1")


def test_chain_days_half_up_and_end_day_up(tmp_path):
    p = lone(tmp_path, "14.4h")
    assert p["lines"][1].endswith("about 2 working days") and p["end_day"] == 3
    p = lone(tmp_path, "2h")
    assert p["end_day"] == 1 and p["lines"][1].endswith("about 0 working days")
    assert "working day 1" in worksite.path_end_text(1, 4)
    assert worksite.path_end_text(1, 4) == ("In the current schedule on 4 lanes, its serving work ends in "
                                            "working day 1")
    assert lone(tmp_path, "15h")["lines"][1].endswith("about 3 working days")
    assert lone(tmp_path, "1d")["lines"][1].endswith("about 1 working day")


CYCLE_UP = ("# Alpha\n\n"
            "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
            "- [ ] Two {issue: Issue-2, depends_on: [Issue-1, Issue-3]}\n"
            "- [ ] Three {issue: Issue-3, est: 1d}\n")


def test_all_held_has_no_end_day(tmp_path):
    p = path_of(site_model(tmp_path, CYCLE_UP), "alpha:FR-1")
    assert p["order"] == [["Issue-3", "upstream"]] and p["targets"] == []
    assert p["lines"] == [worksite.PATH_HEAD_HELD_ONE.format(hours="6"),
                          "Longest chain: 6 agent-hours, about 1 working day"]
    assert p["end_day"] is None
    assert p["incomplete"] == [worksite.PATH_HELD.format(key="Issue-1")]


def test_site_build_names_foreign_waits_unchecked(tmp_path):
    wbs = ("# Alpha\n\n"
           "- [ ] Epic {issue: Issue-10, depends_on: [beta:Issue-8]}\n"
           "  - [ ] Up {issue: Issue-2, est: 1d}\n"
           "- [ ] Target {issue: Issue-1, est: 1d, depends_on: [Issue-2, beta:Issue-9], satisfies: alpha:FR-1}\n")
    p = path_of(site_model(tmp_path, wbs), "alpha:FR-1")
    assert sorted(p["incomplete"]) == sorted([
        worksite.FBOX_TIP.format(key="Issue-1", text="beta:Issue-9 (on another site) not checked in this build"),
        worksite.FBOX_TIP.format(key="Issue-2", text="beta:Issue-8 (on another site) not checked in this build"),
    ])


def test_full_build_uses_outcomes(tmp_path):
    config_path = installation(tmp_path, {
        "alpha": "# Alpha\n\n- [ ] T {issue: Issue-1, est: 1d, depends_on: [beta:Issue-1, gamma:Issue-1], "
                 "satisfies: alpha:FR-1}\n",
        "beta": "# Beta\n\n- [x] Done {issue: Issue-1}\n",
    }, {"alpha": FR1})
    trace = trace_of(built(config_path).pages["alpha"])
    assert row(trace, "alpha:FR-1")["path"]["incomplete"] == [
        worksite.FBOX_TIP.format(key="Issue-1", text=worksite.FOREIGN_ABSENT.format(raw="gamma:Issue-1"))]


def test_path_status_follows_what_the_build_read():
    w = worksite
    assert [w.path_status(*case) for case in (
        ("unserved", "site", None), ("unserved", "installation", ()), ("finished", "site", None),
        ("finished", "installation", ("marsh",)), ("finished", "installation", ()))] == [
        w.PATH_UNSERVED_SITE, w.PATH_UNSERVED, w.PATH_FINISHED_SITE, w.PATH_FINISHED_SITE, w.PATH_FINISHED]


def test_empty_cases_have_no_list_or_figures(tmp_path):
    reqs = "# R\n\n## Functional requirements\n\n- FR-1: Nobody.\n- FR-2: Done.\n"
    m = site_model(tmp_path, "# Alpha\n\n- [x] Done {issue: Issue-1, satisfies: alpha:FR-2}\n"
                             "- [ ] Open {issue: Issue-2}\n", reqs)
    empty = {"state": "unserved", "lines": [worksite.PATH_UNSERVED_SITE], "order": [], "chain": [],
             "targets": [], "end_day": None, "incomplete": []}
    assert path_of(m, "alpha:FR-1") == empty
    assert path_of(m, "alpha:FR-2") == {**empty, "state": "finished", "lines": [worksite.PATH_FINISHED_SITE]}


def test_path_extremes(tmp_path):
    wbs = ("# Alpha\n\n- [ ] A {issue: Issue-1, est: 1d}\n- [ ] B {issue: Issue-2, est: 1d}\n"
           "- [ ] T {issue: Issue-3, est: 1d, depends_on: [Issue-1, Issue-2], satisfies: alpha:FR-1}\n")
    m = site_model(tmp_path / "whole", wbs)
    head = path_of(m, "alpha:FR-1")["lines"][0]
    assert head == worksite.PATH_HEADING.format(n=len(m["schedule"]["items"]), hours="18") and "(" not in head
    one = lone(tmp_path, "1d")["lines"][0]
    assert one == worksite.PATH_HEADING_ONE.format(hours="6")
    held_only = path_of(site_model(tmp_path / "cycle", CYCLE["alpha"], CYCLE_REQS), "alpha:FR-1")
    assert held_only["lines"] == [] and held_only["order"] == []
    assert held_only["incomplete"] == [worksite.PATH_HELD.format(key="Issue-1")]


PATH_KEYS = ["state", "lines", "order", "chain", "targets", "end_day", "incomplete"]


def test_path_keys_exact_and_argument_unchanged():
    config = load_config(GANTRY_SAMPLE)
    roots = config_roots(config)
    sites = load_sites(config, roots)[0]
    catalog = load_catalog(roots, config, sites)
    inputs = build.read_sites(config, sites, roots=roots)[0][SMALL]
    trace = page_trace(build_site_trace(SMALL, inputs.data.leaves, catalog), catalog, inputs.data)
    before = copy.deepcopy(trace)
    m = page_model(SMALL, inputs.data, default_unit_hours=6, inputs=inputs, trace=trace)
    assert trace == before
    assert all(list(r["path"]) == PATH_KEYS for r in m["trace"]["requirements"])
    assert all("path" not in r for r in trace["requirements"])


def test_constraint_row_has_no_box():
    m = render_traced(GANTRY_SAMPLE, SMALL)[1]
    assert path_of(m, "lighthouse:CON-1") == {"state": "constraint", "lines": [], "order": [], "chain": [],
                                              "targets": [], "end_day": None, "incomplete": []}


def test_hours_text_agrees_with_num():
    # worksite.js's num: Math.round(x * 10) / 10, then toString.
    assert [worksite._num1(x) for x in (2.25, 0.05, 123456.75, 6.0, 108.0)] == ["2.3", "0.1", "123456.8", "6",
                                                                                   "108"]


def test_build_paths_scales():
    rng = random.Random(77)
    n = 2000
    items = [it(f"k{j}", rng.choice((1, 2, 6)), [f"k{p}" for p in rng.sample(range(j), min(j, 2))],
                rng.randint(1, 4), j) for j in range(n)]
    links = [(p, item["key"]) for item in items for p in item["preds"]]
    serving = {f"R{r}": ([[f"k{j}", "direct"] for j in range(n) if (j + r) % 7], [], 0) for r in range(10)}
    start = time.perf_counter()
    paths = build_paths(serving, items, unestimated=set(), links=links, foreign={})
    assert time.perf_counter() - start < 2.0
    assert all(len(p.order) <= n for p in paths.values())


def test_work_order_is_the_one_lane_plan():
    # work_order is a heap form of schedule.plan(items, 1), held to it here.
    cases = [items for _, items, _ in constructed()] + [items for items, _ in random_dags(count=64, seed=77)]
    for items in cases:
        planned = plan(items, 1)
        assert work_order(items) == sorted(planned, key=lambda k: planned[k][0])
    for bad in ([it("a", 1, ["b"])], [it("a", 1, ["b"]), it("b", 1, ["a"])], [it("a", 0)]):
        with pytest.raises(ValueError):
            work_order(bad)


# --- Task 5: the selection function and endDay (Node) ---------------------------------

def test_select_marks_upstream_and_chain(node, sample_page):
    path, trace, _ = sample_page
    marks = marks_after(path, trace, [select("lighthouse:FR-1")])
    assert marks["leaves"] == row(trace, "lighthouse:FR-1")["light"]
    assert marks["upstream"] == ["Issue-12"] and marks["chain"] == ["Issue-7", "Issue-10"]


def test_clear_clears_upstream_and_chain(node, sample_page):
    path, trace, _ = sample_page
    assert marks_after(path, trace, [select("lighthouse:FR-1"), select("lighthouse:FR-1")]) == EMPTY_MARKS
    escape = {"type": "key", "key": "Escape", "id": None}
    assert marks_after(path, trace, [select("lighthouse:FR-1"), escape]) == EMPTY_MARKS


def test_hover_and_leave_keep_upstream_and_chain(node, sample_page):
    path, trace, _ = sample_page
    marks = marks_after(path, trace, [select("lighthouse:FR-1"), {"type": "hover", "leaf": "Issue-9"},
                                      {"type": "leave"}])
    assert marks["upstream"] == ["Issue-12"] and marks["chain"] == ["Issue-7", "Issue-10"]
    assert marks["tracing"] is None


def test_collapsed_lane_counts_upstream(node, sample_page):
    path, trace, data = sample_page
    view = {"lane_of": [[leaf["key"], leaf["lane"]] for leaf in data["leaves"]],
            "collapsed": list(range(len(data["lanes"])))}
    assert marks_after(path, trace, [select("lighthouse:FR-1")], view)["lanes"] == [[1, 3]]


def path_trace(order, light=()):
    trace = hand_trace([list(x) for x in light])
    trace["requirements"][0]["path"] = {"state": "path", "lines": [], "order": order, "chain": [], "targets": [],
                                        "end_day": None, "incomplete": []}
    return trace


def test_proto_key_path_marks_exactly_it(node, sample_page):
    path, _, _ = sample_page
    view = {"lane_of": [["__proto__", 0], ["constructor", 1]], "collapsed": [0, 1]}
    marks = marks_after(path, path_trace([["__proto__", "upstream"]]), [select("alpha:FR-1")], view)
    assert marks["upstream"] == ["__proto__"] and marks["lanes"] == [[0, 1]]


def test_upstream_only_selection_is_selecting(node, sample_page):
    path, _, _ = sample_page
    marks = marks_after(path, path_trace([["U", "upstream"]]), [select("alpha:FR-1")])
    assert marks["leaves"] == [] and marks["selecting"] is True


def test_row_without_path_marks_as_before(node, sample_page):
    path, _, _ = sample_page
    marks = marks_after(path, hand_trace([["Issue-1", "direct"]]), [select("alpha:FR-1")])
    assert marks["upstream"] == [] and marks["chain"] == [] and marks["leaves"] == [["Issue-1", "direct"]]


def end_day(path, pairs, targets):
    return run_function(path, "gantryTrace.endDay", [pairs, targets, HOURS_PER_DAY])


def test_end_day_agrees_with_plan_finish_day(node, sample_page):
    path = sample_page[0]
    for name, items, lanes in constructed():
        p = js_plan(path, items, lanes)
        targets = [item["key"] for item in items][::2]
        expected = plan_finish_day([item for item in items if item["key"] in targets], plan(items, lanes))
        assert end_day(path, [[k, list(v)] for k, v in p.items()], targets) == expected, name
        assert end_day(path, [[k, list(v)] for k, v in p.items()], []) is None, name
    ends = [2, 14.4, 12.000000000001, 108.000000003]
    ends += [d * HOURS_PER_DAY + s * k * 1e-10 for d in (1, 2, 17, 18) for k in range(1, 10) for s in (1, -1)]
    for end in ends:
        python = plan_finish_day([{"key": "a"}], {"a": (0.0, end, 0)})
        assert end_day(path, [["a", [0.0, end, 0]]], ["a"]) == python, end
    assert [end_day(path, [["a", [0, e, 0]]], ["a"]) for e in (2, 14.4, 12.000000000001)] == [1, 3, 2]
    assert end_day(path, [["a", [0, 2, 0]]], ["b"]) is None


def test_hours_text_agrees_with_num_in_the_script(node, sample_page):
    path = sample_page[0]
    for x in (2.25, 0.05, 123456.75, 6.0, 108.0, 14.4, 1 / 3):
        assert run_function(path, "gantryTrace.num", [x]) == worksite._num1(x), x


# --- Task 6: the path box, the marks and their CSS ------------------------------------

CSS_PATH = ROOT / "gantry" / "templates" / "worksite.html"


def box(page):
    (found,) = page.query("#req-path")
    return found


def box_items(page, ul):
    return [e.text for e in page.query(f"#req-path ul.{ul} li")]


def test_path_box_on_sample(node, sample_page):
    path, trace, _ = sample_page
    p = row(trace, "lighthouse:FR-1")["path"]
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:FR-1")
        assert "gone" not in box(page).classes
        items = box_items(page, "path-list")
        assert [i.split(" ")[0] for i in items] == ["Issue-7", "Issue-12", "Issue-10"]
        assert items[1].endswith(" · 6 h · upstream") and "chain" not in items[1]
        assert items[0].endswith(" · 90 h · direct · chain") and items[2].endswith(" · 12 h · direct · chain")
        assert items[0] == "Issue-7 · Issue-7 · Record a nightly check · 90 h · direct · chain"
        assert marked(page, "g.node", "req-upstream") == {"Issue-12"} == marked(page, "rect.seg", "req-upstream")
        assert marked(page, "g.node", "req-chain") == {"Issue-7", "Issue-10"} == marked(page, "rect.seg",
                                                                                         "req-chain")
        text = box(page).text
        assert worksite.SCHEDULE_LABEL in text and all(line in text for line in p["lines"])
        (end,) = page.query("#req-path .path-end")
        assert end.attrs.get("aria-live") == "polite" and "aria-live" not in box(page).attrs


def test_end_day_follows_lanes(node, sample_page):
    path, trace, data = sample_page
    p = row(trace, "lighthouse:FR-1")["path"]
    items = data["schedule"]["items"]
    targets = [item for item in items if item["key"] in p["targets"]]
    one = plan_finish_day(targets, plan(items, 1))
    assert (p["end_day"], one) == (17, 29)
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:FR-1")
        assert [e.text for e in page.query("#req-path .path-end")] == [worksite.path_end_text(17, 4)]
        page.drive("lanes", "1")
        assert [e.text for e in page.query("#req-path .path-end")] == [worksite.path_end_text(one, 1)]


def test_box_hidden_without_selection(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        assert "gone" in box(page).classes and page.query("#req-path *") == []
        page.drive("requirement", "lighthouse:FR-1")
        page.drive("requirement", "lighthouse:FR-1")
        assert "gone" in box(page).classes and page.query("#req-path *") == []
        assert page.query(".req-upstream, .req-chain") == []


def test_upstream_marks_survive_hover_and_redraw(node, sample_page):
    path, _, _ = sample_page

    def sets(page):
        return [marked(page, sel, cls) for sel in ("g.node", "rect.seg") for cls in ("req-upstream", "req-chain")]

    with open_page(path) as page:
        page.drive("requirement", "lighthouse:FR-1")
        first = sets(page)
        assert first[0] == {"Issue-12"}
        page.drive("network-detail", "Issue-9")
        page.leave("network-detail", "Issue-9")
        assert sets(page) == first
        page.drive("lanes", "2")
        assert sets(page) == first
        page.drive("group", "lane")
        assert sets(page) == first


def test_collapsed_row_counts_upstream(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:FR-1")
        assert lit_counts(page) == {1: "3 lit"}


def test_empty_case_box(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:UC-2")
        assert "gone" not in box(page).classes
        assert worksite.PATH_UNSERVED_SITE in box(page).text
        assert page.query("#req-path .path-list li, #req-path .path-end, #req-path .label") == []


def test_constraint_row_has_no_box_on_the_page(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:CON-1")
        assert "gone" in box(page).classes and page.query("#req-path *") == []


def test_incomplete_box(node, tmp_path):
    config_path = installation(tmp_path, {"alpha": CYCLE_UP}, {"alpha": FR1})
    path = tmp_path / "alpha.html"
    path.write_bytes(render_traced(config_path, "alpha")[0].encode("utf-8"))
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert worksite.PATH_INCOMPLETE in box(page).text
        assert box_items(page, "path-why") == [worksite.PATH_HELD.format(key="Issue-1")]
        assert [i.split(" ")[0] for i in box_items(page, "path-list")] == ["Issue-3"]
        assert box_items(page, "path-list")[0].endswith(" · upstream · chain")
        assert page.query("#req-path .path-end") == []


def css_rules(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]*)\{([^}]*)\}", text)]


def props(body):
    return {p.split(":")[0].strip(): p.split(":", 1)[1].strip() for p in body.split(";") if ":" in p}


def test_upstream_and_chain_css():
    rules = css_rules(CSS_PATH.read_bytes().decode("utf-8"))
    indirect = {props(b)["stroke-dasharray"] for s, b in rules if "req-indirect" in s and "stroke:" in b}
    upstream = [props(b) for s, b in rules if "req-upstream" in s and "stroke:" in b]
    assert len(upstream) == 2
    for p in upstream:
        assert p["stroke-dasharray"] not in ({"none"} | indirect)
    chain = [(i, props(b)) for i, (s, b) in enumerate(rules) if "req-chain" in s]
    assert len(chain) == 3 and all("stroke-width" in p and set(p) <= {"stroke-width", "paint-order"}
                                   for _, p in chain)
    # A small dot or a narrow bar keeps its fill: the chain stroke paints under it, outside the edge,
    # so only half its width shows, and that half must still be heavier than a direct mark's whole one.
    (how_dot,) = [props(b)["stroke-width"] for s, b in rules if "req-direct" in s and ".dot" in s]
    (how_seg,) = [props(b)["stroke-width"] for s, b in rules if s == ".seg.req-direct"]
    width = {s: p for s, p in ((rules[i][0], p) for i, p in chain)}
    (outline,) = [p for s, p in width.items() if ".outline" in s]
    (dot,) = [p for s, p in width.items() if ".dot" in s]
    (seg,) = [p for s, p in width.items() if s.startswith(".seg")]
    assert "paint-order" not in outline
    for p in (dot, seg):
        assert p["paint-order"] == "stroke"
    assert float(seg["stroke-width"]) / 2 > float(how_seg)
    assert float(dot["stroke-width"]) / 2 > float(how_dot)
    last_how = max(i for i, (s, b) in enumerate(rules)
                   if any(c in s for c in ("req-direct", "req-indirect", "req-upstream")) and "stroke:" in b)
    assert all(i > last_how for i, _ in chain)
    dims = [s for s, b in rules if "opacity" in b and ":not(.req-direct):not(.req-indirect)" in s]
    assert len(dims) == 2 and all(":not(.req-upstream)" in s for s in dims)
    assert not any("req-path" in s and "display" in b for s, b in rules)


def test_box_mounts_after_the_layer_lists(tmp_path):
    t = tree(sample(tmp_path).pages[SMALL])
    panel = by_id(t, "requirements")
    kids = [i for i, e in enumerate(t.elements) if e.get("parent") == panel]
    assert t.elements[kids[-1]]["attrs"].get("id") == "req-path"


# --- Task 7: hostile strings and __proto__ in the path --------------------------------


def hostile_path_site(tmp_path):
    """(page, planted): site alpha in the trace. FR-1's target waits on a leaf keyed
    and numbered HOSTILE[1], titled HOSTILE[0], whose foreign waits carry HOSTILE[2]
    (an open target's title) and HOSTILE[3] (an unresolved reason); FR-2's target
    waits on a leaf keyed __proto__. The leaves are built in memory, as
    hostile_trace_site builds them."""
    h = HOSTILE
    reqs = "# R\n\n## Functional requirements\n\n- FR-1: One.\n- FR-2: Two.\n"
    config_path = installation(tmp_path, {"alpha": ONE_LEAF}, {"alpha": reqs})
    config = load_config(config_path)
    roots = config_roots(config)
    catalog = load_catalog(roots, config, load_sites(config, roots)[0])
    outcome = dict(site="alpha", source_key=h[1], source_line=2, source_is_row=False, target_site="beta")
    waits = (ForeignOutcome(raw="beta:Issue-5", target_issue="Issue-5", status="open", target_key="Issue-5",
                            target_is_row=False, title=h[2], **outcome),
             ForeignOutcome(raw="beta:Issue-6", target_issue="Issue-6", status="unresolved", reason=h[3],
                            **outcome))
    leaves = (
        hostile_leaf("Issue-1", "Issue-1", 1, satisfies="alpha:FR-1"),
        replace(hostile_leaf(h[1], h[1], 2, title=h[0]), foreign_targets=waits, waits_on_other_site=True),
        hostile_leaf("Issue-3", "Issue-3", 3, satisfies="alpha:FR-2"),
        hostile_leaf("__proto__", "__proto__", 4),
    )
    data = SiteData(leaves, (), ((h[1], "Issue-1"), ("__proto__", "Issue-3")), (), ())
    trace = page_trace(build_site_trace("alpha", leaves, catalog), catalog, data)
    page = worksite.render_page("alpha", data, default_unit_hours=6, trace=trace)
    return page, [h[0], h[1], h[2], h[3], "__proto__"]


def test_hostile_path_page_is_safe(tmp_path):
    page, planted = hostile_path_site(tmp_path)
    # HOSTILE[3] is only ever inside its unresolved sentence, so not a whole embedded value.
    trace = assert_page_safe(page, [p for p in planted if p != HOSTILE[3]])["trace"]
    assert_no_external_resources(page)
    strings = list(_strings([r["path"] for r in trace["requirements"]]))
    # The title is in the leaves data, not the path; every other planted value is whole in it.
    assert [p for p in planted[1:] if not any(p in s for s in strings)] == []
    assert row(trace, "alpha:FR-1")["path"]["order"] == [[HOSTILE[1], "upstream"], ["Issue-1", "direct"]]
    assert row(trace, "alpha:FR-2")["path"]["order"] == [["__proto__", "upstream"], ["Issue-3", "direct"]]


def test_hostile_path_under_harness(node, tmp_path):
    page_text, planted = hostile_path_site(tmp_path)
    path = tmp_path / "hostile.html"
    path.write_bytes(page_text.encode("utf-8"))
    with open_page(path) as page:
        assert page.planted_nodes(planted) == Planted([], [], [])
        page.drive("requirement", "alpha:FR-1")
        assert page.planted_nodes(planted) == Planted([], [], [])
        (first, _) = box_items(page, "path-list")
        assert first.startswith(HOSTILE[1] + " · " + HOSTILE[1] + " · " + HOSTILE[0])
        why = box_items(page, "path-why")
        assert len(why) == 2 and HOSTILE[2] in why[0] and HOSTILE[3] in why[1]
        page.drive("requirement", "alpha:FR-2")
        assert page.planted_nodes(planted) == Planted([], [], [])
        assert marked(page, "g.node", "req-upstream") == {"__proto__"} == marked(page, "rect.seg", "req-upstream")
        assert [i.split(" ")[0] for i in box_items(page, "path-list")] == ["__proto__", "Issue-3"]
        used = {c for e in page.query("[class]") for c in e.classes}
        assert used - CLASS_TOKENS - STRUCTURAL == set()


# --- Task 8: requirements and docs -----------------------------------------------------

PATH_TEXTS = [value for name, value in vars(worksite).items()
              if (name.startswith("PATH_") or name == "NOT_CHECKED") and isinstance(value, str)]


def test_own_requirements_state_the_path():
    reqs = own_set().requirements
    fr25 = reqs["gantry:FR-25"]
    assert fr25.layer == "FR" and str(fr25.refines) == "gantry:SR-4"
    uc5 = reqs["gantry:UC-5"]
    assert "gantry:FR-25" in [str(n) for n in uc5.needs]
    assert "and the work they wait on" in uc5.text


def test_doc_states_path_texts():
    doc = (ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    assert "the-path-to-a-requirements-work" in heading_slugs(doc)
    assert len(PATH_TEXTS) >= 20
    assert [text for text in PATH_TEXTS if f"`{text}`" not in doc] == []
    assert [key for key in PATH_KEYS if f"`{key}`" not in doc] == []
    (classes,) = [s for s in re.split(r"(?<=\.)\s", " ".join(doc.split())) if s.startswith("A selection sets only")]
    assert "`req-upstream`" in classes and "`req-chain`" in classes
