"""The path to a selected requirement's work (#77): gantry/path.py, the
`path` record on each trace row, the path box and its marks.

The model tests need no Node; the page-script tests take the `node` fixture.
Sample literals are hand-derived from samples/lighthouse/.
"""

from gantry import build
from gantry.config import load_config
from gantry.inputs import scheduled
from gantry.path import build_paths, heaviest, path_set, work_order
from gantry.schedule import chain_hours
from gantry.sites import config_roots, load_sites
from gantry.trace import build_site_trace, load_catalog, page_trace
from gantry.views.worksite import page_model
from page_safety import assert_page_safe
from sample_roles import READABLE, SMALL
from test_samples import GANTRY_SAMPLE
from test_schedule_agreement import constructed, it, random_dags
from test_trace_page import hostile_trace_site, row


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


def test_empty_states():
    unserved = one_row([], [], finished=0)
    finished = one_row([], [], finished=2)
    assert (unserved.state, finished.state) == ("unserved", "finished")
    for p in (unserved, finished):
        assert p.order == () and p.targets == () and p.reasons == () and p.notes == ()


def test_not_read_and_other_site_reasons():
    # Provisional (#21): nothing in the repository passes either argument yet.
    items = [it("T", 1)]
    serving = {"R": ([["T", "direct"]], [], 0)}

    def built(**kw):
        return build_paths(serving, items, unestimated=set(), links=[], foreign={},
                           **kw)["R"]
    p = built(sites_not_read=("marsh", "bakery"), other_serving={"R": [("harbor", "Issue-4"), ("bay", None)]})
    assert p.notes == ("marsh", "bakery")
    assert p.reasons == (("other_site", "harbor", "Issue-4"), ("other_site", "bay", None))
    assert built(sites_not_read=()).notes == () and built().notes == ()
    assert built().reasons == ()


def test_not_read_in_empty_state_is_a_note():
    p = build_paths({"R": ([], [], 0)}, [], unestimated=set(), links=[], foreign={},
                    sites_not_read=("marsh",))["R"]
    assert p.state == "unserved" and p.notes == ("marsh",) and p.reasons == ()


def test_other_site_only_is_path():
    p = build_paths({"R": ([], [], 1)}, [], unestimated=set(), links=[], foreign={},
                    other_serving={"R": [("harbor", "Issue-4")]})["R"]
    assert p.state == "path" and p.order == ()
    assert p.reasons == (("other_site", "harbor", "Issue-4"),)
