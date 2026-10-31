"""depends_on entries: classification, resolution, rows and cycles (#8)."""

import pytest

from gantry.deps import classify, find_cycles
from gantry.model import DepRef
from gantry.priority import effective
from gantry.wbs_stopgap import parse_wbs
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"


def fixture_text(name):
    return (FIXTURES / f"{name}.md").read_bytes().decode("utf-8")


def parse(name):
    return parse_wbs(fixture_text(name), "alpha")


def at(data, line, kind=None):
    return [e for e in data.report if e.line == line and (kind is None or e.kind == kind)]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("alpha:Issue-1", DepRef("alpha:Issue-1", "local", "alpha", "Issue-1")),
        ("Issue-1", DepRef("Issue-1", "local", "alpha", "Issue-1")),
        ("beta:Issue-1", DepRef("beta:Issue-1", "foreign", "beta", "Issue-1")),
        ("Alpha:Issue-1", DepRef("Alpha:Issue-1", "foreign", "Alpha", "Issue-1")),
        ("beta: Issue-1", DepRef("beta: Issue-1", "malformed", None, None)),
        ("https://x", DepRef("https://x", "malformed", None, None)),
        ("a:b:c", DepRef("a:b:c", "malformed", None, None)),
        (":Issue-1", DepRef(":Issue-1", "malformed", None, None)),
        ("alpha:", DepRef("alpha:", "malformed", None, None)),
    ],
)
def test_classify_table(entry, expected):
    assert classify(entry, "alpha") == expected


# --- resolution --------------------------------------------------------------


def test_refs_fixture():
    data = parse("refs")
    assert data.links == (("Issue-1", "Issue-2"),)
    two = next(leaf for leaf in data.leaves if leaf.key == "Issue-2")
    foreign = [d for d in two.deps if d.kind == "foreign"]
    assert [(d.raw, d.site) for d in foreign] == [("beta:Issue-1", "beta"), ("Alpha:Issue-1", "Alpha")]
    malformed = at(data, 4, "malformed_ref")
    assert len(malformed) == 4
    for entry, found in zip(["'beta: Issue-1'", "'https://x'", "'a:b:c'", "':Issue-1'"], malformed):
        assert entry in found.message
    assert at(data, 4, "unresolved_ref") == []
    assert not [link for link in data.links if "Issue-3" in link]


def test_finished_leaf_still_reported():
    data = parse("refs")
    found = at(data, 5)
    assert [e.kind for e in found] == ["unresolved_ref", "malformed_ref", "bad_priority"]
    for text, e in zip(["'Issue-99'", "'a:b:c'", "'P7'"], found):
        assert text in e.message
    assert not [link for link in data.links if "Issue-4" in link]


def test_entry_naming_row_means_its_open_leaves():
    data = parse("rows")
    assert data.links == (("Issue-2", "Issue-11"), ("Issue-2", "Issue-12"), ("Issue-41", "Issue-50"))
    assert not [e for e in data.report if e.kind in ("unresolved_ref", "cycle")]


def test_row_with_no_open_leaf_is_satisfied():
    data = parse("rows")
    assert not [link for link in data.links if link[1] == "Issue-22"]
    assert at(data, 10) == []


def test_finished_row_own_entry_adds_no_link():
    links = parse("rows").links
    assert ("Issue-12", "Issue-41") not in links
    assert ("Issue-41", "Issue-50") in links


def test_leaf_naming_own_ancestor_row_adds_no_link():
    data = parse("rows")
    assert ("Issue-12", "Issue-11") not in data.links
    assert ("Issue-11", "Issue-11") not in data.links
    assert not [e for e in data.report if e.kind == "cycle"]


def test_row_naming_node_inside_itself_adds_no_link():
    data = parse("rows")
    assert ("Issue-21", "Issue-22") not in data.links
    assert not [e for e in data.report if e.kind == "cycle"]


# --- cycles ------------------------------------------------------------------


def test_cycle_reported_and_held_out():
    data = parse("cycle")
    cycles = [e for e in data.report if e.kind == "cycle"]
    assert [e.line for e in cycles] == [2]
    assert "'Issue-1', 'Issue-2'" in cycles[0].message
    assert data.held_out == ("Issue-1", "Issue-2", "Issue-3")
    assert [e.line for e in data.report if e.kind == "waits_on_cycle"] == [2, 3, 4]
    assert data.links == (
        ("Issue-2", "Issue-1"), ("Issue-1", "Issue-2"), ("Issue-2", "Issue-3"),
        ("Issue-9", "Issue-3"), ("Issue-5", "Issue-4"),
    )


def test_cycle_broken_by_finished_leaf():
    data = parse("cycle")
    assert {"Issue-6", "Issue-7"}.isdisjoint(data.held_out)
    assert not [link for link in data.links if "Issue-7" in link]
    assert len([e for e in data.report if e.kind == "cycle"]) == 1


def test_find_cycles_iterative():
    keys = [f"k{i}" for i in range(3000)]
    links = [(keys[i], keys[i + 1]) for i in range(2999)] + [(keys[-1], keys[0])]
    assert [sorted(c) for c in find_cycles(keys, links)] == [sorted(keys)]
    assert find_cycles(keys, links[:-1]) == []


def effective_map(data):
    return effective([leaf for leaf in data.leaves if leaf.open and leaf.key not in data.held_out], data.links)


def test_cycle_leaves_others_unaffected():
    text = fixture_text("cycle")
    lines = text.splitlines(keepends=True)
    cut = parse_wbs("".join(lines[:1] + lines[4:]), "alpha")
    data = parse("cycle")
    kept = tuple(link for link in data.links if not set(link) & set(data.held_out))
    assert kept == cut.links == (("Issue-5", "Issue-4"),)
    expected = {"Issue-4": 1, "Issue-5": 1, "Issue-6": 3, "Issue-9": 3}
    assert effective_map(data) == effective_map(cut) == expected


def test_long_chain_no_recursion():
    lines = [f"- [ ] L{i} {{issue: Issue-{i}, depends_on: [Issue-{i - 1}]}}" for i in range(1, 3000)]
    lines.insert(0, "- [ ] L0 {issue: Issue-0}")
    lines[-1] = lines[-1][:-1] + ", priority: P1}"
    data = parse_wbs("".join(line + "\n" for line in lines), "alpha")
    assert len(data.leaves) == 3000
    assert data.held_out == ()
    assert effective_map(data)["Issue-0"] == 1
