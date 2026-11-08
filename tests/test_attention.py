"""A leaf's attention need and rate (#36): h:, the heuristic, the mapping and the four sources."""

import pytest

from gantry import model
from gantry.attention import MESSAGES, check_h, read_h
from gantry.model import REPORT_KINDS, Leaf, SiteData, report_order
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
