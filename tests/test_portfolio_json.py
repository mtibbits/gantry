"""devagent's portfolio JSON read into Gantry's site model (#83): gantry/portfolio_json.py.

Every test here runs on every leg: the documents are the committed
tests/fixtures/portfolio/written.json, which the pinned devagent release
wrote, and mutations of it built in the test. The one gated test ties a
suppression rule to the pinned parser's own texts.
"""

from types import SimpleNamespace

from gantry.attention import read_h
from gantry.deps import refs
from gantry.est_rules import classify_est
from gantry.model import ParsedValue, meta_scalar, meta_value, meta_values
from gantry.priority import rank
from gantry.wbs_stopgap import parse_wbs

# --- ParsedValue and the accessors (Task 1) --------------------------------------------------


def test_parsed_shapes_are_not_re_read():
    listed = [ParsedValue("a"), ParsedValue("[b]")]
    assert meta_value(listed) is listed
    assert meta_values(listed) is listed
    bracket = ParsedValue("[x]")
    assert meta_value(bracket) == "[x]" and meta_value(bracket) is bracket
    assert meta_scalar(bracket) is bracket
    quoted = ParsedValue('"a"')
    assert meta_values(quoted) == ['"a"'] and meta_values(quoted)[0] is quoted
    assert meta_scalar(["a"]) is None
    assert meta_scalar(None) is None
    assert meta_values(None) == []
    assert meta_values(ParsedValue("")) == []
    assert meta_values([]) == []
    # Surrounding space is devagent's to strip, not the accessor's.
    assert meta_value(ParsedValue(" x ")) == " x "
    assert ParsedValue.__slots__ == () and not hasattr(ParsedValue("x"), "__dict__")


def test_plain_str_keeps_the_raw_text_reading():
    assert meta_value("[a, 'b']") == ["a", "b"]
    assert meta_values("[a, 'b']") == ["a", "b"]
    assert meta_value('"x"') == "x"
    assert meta_scalar('"x"') == "x"
    assert meta_scalar("[a]") is None
    (leaf,) = parse_wbs('- [ ] A {issue: I-1, note: "[x]"}\n', "s").leaves
    note = leaf.meta["note"]
    assert type(note) is str and note == '"[x]"'
    assert meta_value(note) == "[x]"
    # A str method gives a plain str back, so the tag is read off the stored object only.
    assert type(ParsedValue("[x]").strip()) is str


def test_consumers_take_parsed_values():
    assert rank(ParsedValue("P0")) == 0
    assert rank(["P1"]) == 3
    assert rank(ParsedValue('"P1"')) == 3  # quotes are part of a parsed value
    assert classify_est(ParsedValue("3h")).kind == "time"
    assert classify_est(ParsedValue("[3h]")).kind == "unparseable"
    assert read_h(SimpleNamespace(meta={"h": ParsedValue("2")})) == 2
    assert read_h(SimpleNamespace(meta={"h": [ParsedValue("2")]})) is None
    assert read_h(SimpleNamespace(meta={"h": ["2"]})) is None
    (ref,) = refs({"depends_on": ParsedValue("[I-1]")}, "a")
    assert ref.raw == "[I-1]"
    assert [r.raw for r in refs({"depends_on": [ParsedValue("I-1"), ParsedValue("b:I-2")]}, "a")] == ["I-1", "b:I-2"]
