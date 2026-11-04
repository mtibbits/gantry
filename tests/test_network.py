"""The dependency network: gantry/network.py (#9).

Every literal is hand-derived from tests/fixtures/wbs/page.md and cycle.md.
"""

import time

from gantry.network import build, critical_chain, heaviest_chain
from gantry.render import slack_class
from gantry.wbs_stopgap import parse_wbs
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"
CRITICAL = ("Issue-2", "Issue-3", "Issue-4", "Issue-5", "Issue-9", "1.7")


def network(name):
    data = parse_wbs((FIXTURES / f"{name}.md").read_bytes().decode("utf-8"), "alpha")
    keys = [leaf.key for leaf in data.leaves if leaf.open and leaf.key not in data.held_out]
    return build(keys, data.links, {leaf.key: leaf.epic for leaf in data.leaves}), data


def test_nodes_are_open_linked_leaves():
    net, _ = network("page")
    assert net.nodes == ("Issue-2", "Issue-3", "Issue-4", "Issue-5", "Issue-9", "1.7",
                         "Issue-11", "Issue-12", "Issue-13", "Issue-7", "Issue-14")


def test_transitive_reduction():
    net, data = network("page")
    assert len(data.links) == 10
    assert ("Issue-2", "Issue-4") in data.links
    assert net.edges == (
        ("Issue-2", "Issue-3"), ("Issue-3", "Issue-4"), ("Issue-4", "Issue-5"),
        ("Issue-5", "Issue-9"), ("Issue-9", "1.7"), ("Issue-11", "Issue-12"),
        ("Issue-4", "Issue-13"), ("Issue-13", "Issue-7"), ("Issue-13", "Issue-14"),
    )


def test_earliest_slots():
    net, _ = network("page")
    assert net.es == {"Issue-2": 0, "Issue-3": 1, "Issue-4": 2, "Issue-5": 3, "Issue-9": 4, "1.7": 5,
                      "Issue-11": 0, "Issue-12": 1, "Issue-13": 3, "Issue-7": 4, "Issue-14": 4}
    assert net.length == 6


def test_slack_classes():
    net, _ = network("page")
    classes = {key: slack_class(net.slack[key]) for key in net.nodes}
    assert classes == {**dict.fromkeys(CRITICAL, "crit"),
                       **dict.fromkeys(("Issue-13", "Issue-7", "Issue-14"), "near"),
                       **dict.fromkeys(("Issue-11", "Issue-12"), "free")}
    assert {net.slack[k] for k in ("Issue-13", "Issue-7", "Issue-14")} == {1}
    assert {net.slack[k] for k in ("Issue-11", "Issue-12")} == {4}


def test_critical_path():
    net, _ = network("page")
    assert net.critical == CRITICAL
    assert net.crit_edges == tuple(zip(CRITICAL, CRITICAL[1:]))


def test_critical_chain_is_one_chain():
    net, _ = network("page")
    assert critical_chain(net) == CRITICAL
    # A fork: both b and c have slack 0, but they are not one chain.
    fork = build(["a", "b", "c"], [("a", "b"), ("a", "c")], dict.fromkeys("abc"))
    assert fork.critical == ("a", "b", "c")
    assert critical_chain(fork) == ("a", "b")
    diamond = build(list("abcd"), [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")], dict.fromkeys("abcd"))
    assert critical_chain(diamond) == ("a", "b", "d")
    assert critical_chain(build(["a"], [], {"a": None})) == ()


def test_only_cross_epic_link_is_dashed():
    net, _ = network("page")
    assert net.cross == {("Issue-4", "Issue-13")}
    assert ("Issue-4", "Issue-13") not in net.crit_edges
    # A linked top-level leaf is in the no-epic lane: its link to an epic crosses.
    top = build(["a", "b", "c"], [("a", "b"), ("b", "c")], {"a": None, "b": "Issue-1", "c": "Issue-1"})
    assert top.cross == {("a", "b")}


def test_held_out_leaves_left_out():
    net, data = network("cycle")
    assert data.held_out == ("Issue-1", "Issue-2", "Issue-3")
    assert net.nodes == ("Issue-4", "Issue-5")
    assert net.edges == (("Issue-5", "Issue-4"),)


def test_heaviest_chain():
    net, _ = network("page")
    assert heaviest_chain(net, dict.fromkeys(net.nodes, 24)) == (CRITICAL, 144.0)
    # c has no link, so it is not in the network.
    two = build(["a", "b", "c"], [("a", "b")], dict.fromkeys("abc"))
    assert heaviest_chain(two, {"a": 1, "b": 1, "c": 5}) == (("a", "b"), 2.0)


def test_empty_network():
    net = build(["a"], [], {"a": None})
    assert (net.nodes, net.edges, net.length, net.critical) == ((), (), 0, ())
    assert heaviest_chain(net, {"a": 1}) == ((), 0.0)


def test_long_chain_no_recursion():
    keys = [f"k{i}" for i in range(2000)]
    links = list(zip(keys, keys[1:]))
    began = time.perf_counter()
    net = build(keys, links, dict.fromkeys(keys))
    chain = heaviest_chain(net, dict.fromkeys(keys, 1))
    elapsed = time.perf_counter() - began
    print(f"2,000-node chain built in {elapsed:.3f} s")
    assert net.length == 2000
    assert chain == (tuple(keys), 2000.0)
    assert elapsed < 5
