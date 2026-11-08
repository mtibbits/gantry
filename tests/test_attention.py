"""A leaf's attention need and rate (#36): h:, the heuristic, the mapping and the four sources."""

from dataclasses import replace

import pytest

from gantry import model, portfolio_file
from gantry.attention import (
    DEFAULT_MAPPING,
    MAPPING_ABOVE_MAX,
    MAPPING_KEY_PATH,
    MAPPING_NOT_FINITE,
    MAPPING_REJECTED,
    MAX_RATE,
    MESSAGES,
    NO_INPUTS,
    AttentionMapping,
    check_h,
    heuristic_need,
    mapping_in_use,
    rate_problem,
    read_h,
)
from gantry.model import REPORT_KINDS, Leaf, Models, SiteData, report_order
from gantry.portfolio_file import read_portfolio_file
from gantry.wbs_stopgap import parse_wbs
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "attention"
SITE = "alpha"

# (written, the #12 parsed form, the score): in h_values.md's order, Issue-2 on.
H_CASES = (
    ("3", "3", 3),
    ('"3"', "3", 3),
    ("'3'", "3", 3),
    ("5", "5", None),
    ("2.5", "2.5", None),
    ("high", "high", None),
    ('"3 "', "3 ", None),
    ("", "", None),
    ("+3", "+3", None),
    ("03", "03", None),
    ("-1", "-1", None),
    (chr(0xFF13), chr(0xFF13), None),
    ("\"'3'\"", "'3'", None),
    ("[3, 4]", ["3", "4"], None),
    ("[3]", ["3"], None),
)
CASE_IDS = [ascii(c[0]) for c in H_CASES]
# h_values.md's other nodes with h:, each reported: a row, a done and a skipped leaf.
EXTRA_BAD = ("Issue-17", "Issue-19", "Issue-20")


def fixture_data(name):
    return parse_wbs((FIXTURES / name).read_bytes().decode("utf-8"), SITE)


def h_data():
    return fixture_data("h_values.md")


def node(data, key):
    found = [n for n in (*data.leaves, *data.rows) if n.key == key]
    assert len(found) == 1, key
    return found[0]


def case_leaf(data, i):
    return node(data, f"Issue-{i + 2}")


def entries_on(data, line):
    return [e for e in data.report if e.kind == "bad_h" and e.line == line]


def bad_h_message(leaf, raw):
    return MESSAGES["bad_h"].format(line=leaf.line, raw=raw, key=leaf.key)


def make_leaf(key="Issue-1", line=1, meta=None, **fields):
    values = dict(key=key, issue=key, outline="1", title=key, glyph=" ", state="pending", open=True,
                  priority=None, deps=(), meta=meta if meta is not None else {"issue": key}, epic=None,
                  line=line)
    values.update(fields)
    return Leaf(**values)


def site_of(*leaves, rows=()):
    return SiteData(tuple(leaves), tuple(rows), (), (), ())


def parsed_12(raw):
    """#12's meta_values contract: a string or a list of strings, no further reading."""
    if raw is None or raw == "":
        return []
    return list(raw) if isinstance(raw, list) else [raw]


# --- h: ----------------------------------------------------------------------


@pytest.mark.parametrize("i", range(len(H_CASES)), ids=CASE_IDS)
def test_h_text_forms(i):
    written, _, expected = H_CASES[i]
    data = h_data()
    leaf = case_leaf(data, i)
    assert leaf.meta["h"] == written
    assert read_h(leaf) == expected
    found = entries_on(check_h(data, SITE), leaf.line)
    if expected is not None:
        assert found == []
        return
    assert len(found) == 1
    entry = found[0]
    assert (entry.site, entry.line) == (SITE, leaf.line)
    assert entry.message == bad_h_message(leaf, written)
    assert repr(leaf.key) in entry.message and repr(written) in entry.message


@pytest.mark.parametrize("i", range(len(H_CASES)), ids=CASE_IDS)
def test_h_parsed_forms(i, monkeypatch):
    _, parsed, expected = H_CASES[i]
    monkeypatch.setattr(model, "meta_values", parsed_12)
    leaf = make_leaf(meta={"issue": "Issue-1", "h": parsed})
    assert read_h(leaf) == expected
    found = entries_on(check_h(site_of(leaf), SITE), leaf.line)
    assert len(found) == (0 if expected is not None else 1)
    if found:
        assert found[0].message == bad_h_message(leaf, parsed)
        assert repr(parsed) in found[0].message


def test_read_h_calls_meta_values(monkeypatch):
    calls = []
    real = model.meta_values

    def spy(raw):
        calls.append(raw)
        return real(raw)

    monkeypatch.setattr(model, "meta_values", spy)
    data = h_data()
    for i, (written, _, _) in enumerate(H_CASES):
        calls.clear()
        read_h(case_leaf(data, i))
        assert calls == ([] if written.startswith("[") else [written]), ascii(written)


def test_h_on_row_with_children():
    data = check_h(h_data(), SITE)
    row = node(data, "Issue-17")
    found = entries_on(data, row.line)
    assert len(found) == 1
    assert found[0].message == MESSAGES["h_on_row"].format(line=row.line, raw="2", key="Issue-17")
    assert "on a row with children, not a leaf" in found[0].message
    assert entries_on(data, node(data, "Issue-18").line) == []


def test_h_on_finished_leaves():
    data = check_h(h_data(), SITE)
    for key, state in (("Issue-19", "done"), ("Issue-20", "skipped")):
        leaf = node(data, key)
        assert leaf.state == state
        assert [e.message for e in entries_on(data, leaf.line)] == [bad_h_message(leaf, "high")]


def test_no_h_no_entry():
    data = h_data()
    leaf = node(data, "Issue-21")
    assert "h" not in leaf.meta
    assert read_h(leaf) is None
    assert entries_on(check_h(data, SITE), leaf.line) == []


def test_check_h_keeps_report_sorted():
    data = h_data()
    old = data.report
    assert [e.kind for e in old] == ["bad_priority"]
    bad_keys = [f"Issue-{i + 2}" for i, c in enumerate(H_CASES) if c[2] is None] + list(EXTRA_BAD)
    report = check_h(data, SITE).report
    assert len(report) == len(old) + len(bad_keys)
    assert list(report) == sorted(report, key=report_order)
    # bad_priority sits on a bad h: line, between the bad h: entries of the lines around it.
    expected = sorted([(e.line, e.kind) for e in old] + [(node(data, k).line, "bad_h") for k in bad_keys],
                      key=lambda p: (p[0], REPORT_KINDS.index(p[1])))
    assert [(e.line, e.kind) for e in report] == expected
    kinds = [e.kind for e in report]
    at = kinds.index("bad_priority")
    assert kinds[at - 1] == kinds[at + 1] == "bad_h"


def test_check_h_never_raises():
    leaves = [make_leaf(f"Issue-{n}", line=n, meta={"issue": f"Issue-{n}", "h": raw})
              for n, raw in ((1, 3), (2, {}), (3, None))]
    for leaf in leaves:
        assert read_h(leaf) is None
    data = check_h(site_of(*leaves), SITE)
    for leaf, raw in zip(leaves, (3, {}, None)):
        assert [e.message for e in entries_on(data, leaf.line)] == [bad_h_message(leaf, raw)]


# --- the mapping and rate_problem ----------------------------------------------


def portfolio(name):
    return read_portfolio_file(FIXTURES / f"portfolio_{name}.md", br_namespace="life")


def report_paths(p, kind):
    return [e.key_path for e in p.report if e.kind == kind]


DEFAULT_IN_USE = AttentionMapping(DEFAULT_MAPPING, "default", None)


def test_mapping_constants():
    assert DEFAULT_MAPPING == (0.0, 0.1, 0.25, 0.5, 1.0)
    assert MAX_RATE == 24
    assert MAPPING_KEY_PATH in {k.path for k in portfolio_file.SETTINGS_KEYS}


def test_mapping_without_portfolio():
    assert mapping_in_use(None) == DEFAULT_IN_USE


def test_mapping_unset():
    p = portfolio("unset")
    assert p.capacity.attention_mapping is None
    assert mapping_in_use(p) == DEFAULT_IN_USE


def test_mapping_set():
    m = mapping_in_use(portfolio("set"))
    assert m == AttentionMapping((0, 0.2, 0.4, 0.6, 0.8), "portfolio_file", None)
    assert all(isinstance(v, float) for v in m.values)


def test_mapping_at_max():
    assert mapping_in_use(portfolio("max")) == AttentionMapping((0, 0.1, 0.25, 0.5, 24), "portfolio_file", None)


def test_mapping_rejected():
    p = portfolio("rejected")
    assert MAPPING_KEY_PATH in report_paths(p, "bad_value")
    assert mapping_in_use(p) == AttentionMapping(DEFAULT_MAPPING, "default", MAPPING_REJECTED)


def test_mapping_other_bad_value():
    p = portfolio("other_bad")
    assert report_paths(p, "bad_value") == ["capacity.block_min_hours"]
    assert mapping_in_use(p) == DEFAULT_IN_USE


def test_mapping_not_finite():
    p = portfolio("inf")
    assert report_paths(p, "bad_value") == []
    assert mapping_in_use(p) == AttentionMapping(DEFAULT_MAPPING, "default", MAPPING_NOT_FINITE)


def test_mapping_above_max():
    p = portfolio("huge")
    assert report_paths(p, "bad_value") == []
    assert mapping_in_use(p) == AttentionMapping(DEFAULT_MAPPING, "default", MAPPING_ABOVE_MAX)


def test_mapping_reasons_name_the_limit():
    assert MAPPING_ABOVE_MAX.endswith(f"above {MAX_RATE}")


@pytest.mark.parametrize("value, reason", [
    (0, None), (24, None), (24.0, None), (0.5, None),
    (24.01, "above 24"), (-0.01, "negative"),
    (float("inf"), "not finite"), (float("-inf"), "not finite"), (float("nan"), "not finite"),
    (True, "not a number"), ("1", "not a number"), (None, "not a number"),
    (10 ** 400, "above 24"),
], ids=repr)
def test_rate_problem(value, reason):
    assert rate_problem(value) == reason


# --- the heuristic --------------------------------------------------------------


def heuristic_site():
    return fixture_data("heuristic.md")


def need_of(key, **fields):
    data = heuristic_site()
    leaf = replace(node(data, key), **fields)
    return heuristic_need(leaf, data)


def test_heuristic_no_inputs():
    assert need_of("Issue-10") == (1, "1 to start; no model tiers or flags recorded")
    assert NO_INPUTS == "no model tiers or flags recorded"


def test_heuristic_top_tier():
    assert need_of("Issue-10", models=Models("opus", "fable", "estimates_file")) == (2, "1 to start; checking tier fable")
    for checking in ("opus", "inherit", "Fable"):
        assert need_of("Issue-10", models=Models("opus", checking, "estimates_file")) == (1, "1 to start"), checking


@pytest.mark.parametrize("flags, need", [
    ("research", 2), ("Spike", 2), ("research spike", 2), ("research,spike", 2), ("researcher", 1),
    (["Research"], 2), (["spike", 3, None], 2), ([3], 1),
], ids=repr)
def test_heuristic_flags(flags, need):
    assert need_of("Issue-10", flags=flags)[0] == need


def test_heuristic_flag_basis():
    assert need_of("Issue-10", flags="Spike")[1] == "1 to start; flagged spike"
    assert need_of("Issue-10", flags="spike research")[1] == "1 to start; flagged research and spike"
    assert need_of("Issue-10", flags="researcher")[1] == "1 to start"


@pytest.mark.parametrize("flags", [None, "", "  ", [], ["", "  "], [3]], ids=repr)
def test_heuristic_no_flags_recorded(flags):
    assert need_of("Issue-10", flags=flags) == (1, "1 to start; no model tiers or flags recorded")


def test_heuristic_first_open_leaf():
    needs = {key: need_of(key)[0] for key in ("Issue-2", "Issue-3", "Issue-6", "Issue-9", "Issue-10", "Issue-13")}
    assert needs == {"Issue-2": 2, "Issue-3": 1, "Issue-6": 2, "Issue-9": 1, "Issue-10": 1, "Issue-13": 2}
    assert need_of("Issue-2")[1] == "1 to start; no model tiers or flags recorded; first open leaf of its epic"


def test_heuristic_every_input_is_4():
    assert need_of("Issue-2", models=Models("opus", "fable", "estimates_file"), flags="research spike") == (
        4, "1 to start; checking tier fable; flagged research and spike; first open leaf of its epic")


def test_heuristic_tier_source_alike():
    assert (need_of("Issue-10", models=Models("opus", "fable", "wbs"))
            == need_of("Issue-10", models=Models("opus", "fable", "estimates_file")))
