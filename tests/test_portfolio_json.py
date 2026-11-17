"""devagent's portfolio JSON read into Gantry's site model (#83): gantry/portfolio_json.py.

Every test here runs on every leg: the documents are the committed
tests/fixtures/portfolio/written.json, which the pinned devagent release
wrote, and mutations of it built in the test. The one gated test ties a
suppression rule to the pinned parser's own texts.
"""

import copy
import dataclasses
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import helpers_portfolio as hp
from gantry import portfolio_json as pj
from gantry.attention import read_h
from gantry.deps import refs
from gantry.est_rules import classify_est
from gantry.model import STATE_GLYPHS, ParsedValue, meta_scalar, meta_value, meta_values
from gantry.portfolio_json import PortfolioInput, PortfolioLoadError, map_document, read_portfolio
from gantry.priority import rank
from gantry.roots import directory_roots
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


# --- load, version and schema checks (Task 3) -----------------------------------------------

# written.json's projects, by index: the order of tools/sample_portfolio.py's FIXTURE_SOURCES.
CYCLE, EMPTY, FENCES, IDS, NESTING, NOT_UTF8 = range(6)
AGAIN = "Run devagent's aggregator again (/devagent:portfolio)."


def doc_path(tmp_path):
    return tmp_path / hp.DOC_NAME


def load_error(tmp_path, doc):
    with pytest.raises(PortfolioLoadError) as info:
        hp.read_doc(tmp_path, doc)
    assert info.value.source == str(doc_path(tmp_path))
    return info.value


def schema_text(source, where, problem):
    return f"{source}: not a portfolio document Gantry can read: {where}: {problem}"


def bare_node(n, parent=None, name="c"):
    """A node holding only the fields the reader uses, on line n."""
    return {"id": f"{name}{n}", "parent": parent, "text": f"Node {n}", "state": "pending", "glyph": " ",
            "source_line": n, "issue": None, "meta": {}}


def chain(depth):
    """`depth` nodes, each the child of the one before."""
    return [bare_node(n, f"c{n - 1}" if n > 1 else None) for n in range(1, depth + 1)]


def test_constants():
    assert (pj.SCHEMA_ID, pj.MAJOR, pj.MINOR, pj.MAX_DEPTH) == ("devagent-portfolio", 1, 1, 64)
    assert pj.STATUSES == ("read", "no_wbs", "no_devdoc_dir", "unreadable")
    doc = hp.written_doc()
    assert doc["schema_version"] == f"{pj.MAJOR}.{pj.MINOR}"
    for good in ("0.0", "1.10", "123456789.0"):
        assert pj.VERSION_RE.fullmatch(good), good
    for bad in ("1", "01.1", "1.01", "1.1.0", "1.", " 1.1", "1234567890.0", "1." + chr(0x661)):
        assert not pj.VERSION_RE.fullmatch(bad), bad
    for good in ("2026-01-12T09:30:00Z", "2026-01-12T09:30:00+00:00", "2026-01-12T09:30:00.5Z",
                 "2026-01-12T09:30:00.123456Z"):
        assert pj.TIME_RE.fullmatch(good), good
        assert datetime.fromisoformat(good).utcoffset().total_seconds() == 0
    for bad in ("2026-01-12T09:30:00", "2026-01-12 09:30:00Z", "2026-01-12T09:30:00+01:00",
                "2026-01-12T09:30:00.1234567Z", "2026-01-12T09:30Z", "2026-01-12T09:30:00z"):
        assert not pj.TIME_RE.fullmatch(bad), bad


def test_unknown_major_names_both_versions(tmp_path):
    for found in ("2.0", "0.9", "10.1"):
        doc = hp.written_doc()
        doc["schema_version"] = found
        error = load_error(tmp_path, doc)
        assert error.kind == "version"
        assert str(error) == (f'{doc_path(tmp_path)}: portfolio schema_version "{found}" is not supported: '
                              "Gantry reads major 1 (schema 1.1)")
        assert f'"{found}"' in str(error) and "schema 1.1" in str(error)
        with pytest.raises(PortfolioLoadError) as info:
            map_document(doc)
        assert info.value.kind == "version" and info.value.source == "the portfolio document"
        assert str(info.value).startswith(f'the portfolio document: portfolio schema_version "{found}"')


def test_newer_minor_with_unknown_fields_is_read(tmp_path):
    plain = hp.read_doc(tmp_path, hp.written_doc())
    assert isinstance(plain, PortfolioInput) and plain.schema_version == "1.1"
    doc = hp.written_doc()
    doc["schema_version"] = "1.9"
    doc["later"] = {"deep": [1, {"x": None}]}
    doc["projects"][CYCLE]["later"] = [1]
    doc["projects"][CYCLE]["nodes"][0]["later"] = "x"
    doc["parse_report"][0]["later"] = None
    got = hp.read_doc(tmp_path, doc)
    assert got.schema_version == "1.9"
    assert dataclasses.replace(got, schema_version="1.1") == plain
    # An unknown field is ignored at the reader's own minor too.
    doc["schema_version"] = "1.1"
    assert hp.read_doc(tmp_path, doc) == plain


def test_older_minor_is_read(tmp_path):
    plain = hp.read_doc(tmp_path, hp.written_doc())
    doc = hp.written_doc()
    doc["schema_version"] = "1.0"
    got = hp.read_doc(tmp_path, doc)
    assert got.schema_version == "1.0"
    assert dataclasses.replace(got, schema_version="1.1") == plain


def test_missing_or_outside_roots_document_names_path_and_remedy(tmp_path):
    inside, outside = tmp_path / "in", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    (outside / hp.DOC_NAME).write_bytes(hp.sp.WRITTEN.read_bytes())
    roots = directory_roots([inside])
    missing = inside / hp.DOC_NAME
    nul = inside / ("a" + chr(0) + "b.json")
    cases = (
        (missing, f"{missing}: devagent's portfolio JSON does not exist. Run devagent's aggregator first "
                  "(/devagent:portfolio)."),
        (outside / hp.DOC_NAME, f"{outside / hp.DOC_NAME}: devagent's portfolio JSON is outside the configured "
                                "roots; add its folder to the installation's roots"),
        (nul, f"{nul}: cannot read devagent's portfolio JSON: embedded null character"),
    )
    for path, text in cases:
        with pytest.raises(PortfolioLoadError) as info:
            read_portfolio(roots, path)
        assert (info.value.kind, info.value.source) == ("unreadable", str(path))
        assert str(info.value) == text
        assert ("aggregator" in text) is (path == missing)
    assert len({text.split(": ", 1)[1] for _, text in cases}) == 3
    # A folder where the document should be is unreadable too, in the system's own words.
    (inside / "folder.json").mkdir()
    with pytest.raises(PortfolioLoadError) as info:
        read_portfolio(roots, inside / "folder.json")
    assert info.value.kind == "unreadable"
    assert str(info.value).startswith(f"{inside / 'folder.json'}: cannot read devagent's portfolio JSON: ")
    assert "aggregator" not in str(info.value)


def test_duplicate_key_is_refused(tmp_path):
    text = hp.sp.WRITTEN.read_bytes().decode("utf-8")
    assert text.startswith('{\n  "schema"')
    error = load_error(tmp_path, ('{\n  "projects": [],' + text[1:]).encode("utf-8"))
    assert error.kind == "schema"
    assert str(error) == schema_text(doc_path(tmp_path), "$", "key 'projects' used twice")
    first_meta = '"meta": {\n'
    assert hp.written_doc()["projects"][CYCLE]["nodes"][0]["meta"]["issue"] == "Issue-1"
    twice = text.replace(first_meta, '"meta": {\n "issue": "Issue-77",\n', 1)
    assert twice != text
    error = load_error(tmp_path, twice.encode("utf-8"))
    assert error.kind == "schema"
    assert str(error) == schema_text(doc_path(tmp_path), "$.projects[0].nodes[0].meta", "key 'issue' used twice")
    # The outer object is named first when both repeat a key.
    both = ('{\n  "projects": [],' + twice[1:]).encode("utf-8")
    assert str(load_error(tmp_path, both)) == schema_text(doc_path(tmp_path), "$", "key 'projects' used twice")


def test_map_document_does_not_mutate():
    doc = hp.written_doc()
    doc["projects"][IDS]["nodes"].reverse()
    before = copy.deepcopy(doc)
    map_document(doc)
    assert doc == before
    for project in doc["projects"]:
        assert [n["id"] for n in project["nodes"]] == [n["id"] for n in before["projects"][
            doc["projects"].index(project)]["nodes"]]
        for node in project["nodes"]:
            for value in node["meta"].values():
                assert type(value) in (str, list)
                assert all(type(v) is str for v in value) or type(value) is str


def test_not_json_names_path(tmp_path):
    path = doc_path(tmp_path)
    whole = hp.sp.WRITTEN.read_bytes()
    cases = (
        (b'{"schema": "devagent-portfolio",\n  "projects": [\n', "syntax error at line 3 column 1"),
        (whole[:len(whole) // 2], None),
        (b"", "syntax error at line 1 column 1"),
        (b'{"schema": "\xff"}', "not UTF-8 at byte 12"),
        (b"[" * 100_000, "nesting is too deep"),
        (b'{"source_line": ' + b"7" * 5000 + b"}", "a number is too long"),
        (b'{"x": NaN}', "NaN or Infinity is not JSON"),
        (b'{"x": -Infinity}', "NaN or Infinity is not JSON"),
        (b'{"x": [Infinity]}', "NaN or Infinity is not JSON"),
    )
    for data, reason in cases:
        error = load_error(tmp_path, data)
        assert error.kind == "not_json", reason
        if reason is None:  # the fixture cut in half: only the place is the decoder's
            assert str(error).startswith(f"{path}: devagent's portfolio JSON is not valid JSON: syntax error at line ")
            assert str(error).endswith(f". {AGAIN}")
            continue
        assert str(error) == f"{path}: devagent's portfolio JSON is not valid JSON: {reason}. {AGAIN}"


def _set(path, value):
    """A mutation: the value at `path` (keys and indexes) becomes `value`."""
    def change(doc):
        at = doc
        for step in path[:-1]:
            at = at[step]
        at[path[-1]] = value
    return change


def _drop(path):
    def change(doc):
        at = doc
        for step in path[:-1]:
            at = at[step]
        del at[path[-1]]
    return change


def _cycle_of_two(doc):
    a, b = doc["projects"][CYCLE]["nodes"][:2]
    a["parent"], b["parent"] = b["id"], a["id"]


def _late_parent(doc):
    nodes = doc["projects"][CYCLE]["nodes"]
    nodes[0]["parent"] = nodes[1]["id"]


def _both_passes(doc):
    doc["projects"][CYCLE]["nodes"][0]["parent"] = "nope"
    doc["parse_report"][-1]["message"] = None


NODE = ("projects", IDS, "nodes", 1)
NODE_AT = "$.projects[3].nodes[1]"
ENTRY = ("parse_report", 0)
GLYPHS = "' ', 'x', '-', '!', '~', '?', 'P'"
# (what breaks, the mutation, where, the problem): one row per problem the reader names.
SCHEMA_ROWS = (
    ("top level is a list", lambda doc: [doc], "$", "not an object"),
    ("schema id", _set(("schema",), "portfolio"), "$.schema", 'not "devagent-portfolio"'),
    ("no schema id", _drop(("schema",)), "$.schema", 'not "devagent-portfolio"'),
    ("version is a number", _set(("schema_version",), 1.1), "$.schema_version", "not MAJOR.MINOR"),
    ("version has three parts", _set(("schema_version",), "1.1.0"), "$.schema_version", "not MAJOR.MINOR"),
    ("version has a leading zero", _set(("schema_version",), "01.1"), "$.schema_version", "not MAJOR.MINOR"),
    ("no version", _drop(("schema_version",)), "$.schema_version", "not MAJOR.MINOR"),
    ("time is a word", _set(("generated_at",), "yesterday"), "$.generated_at", "not an RFC 3339 UTC time"),
    ("time has month 13", _set(("generated_at",), "2026-13-01T00:00:00Z"), "$.generated_at",
     "not an RFC 3339 UTC time"),
    ("time has no zone", _set(("generated_at",), "2026-01-12T09:30:00"), "$.generated_at", "not an RFC 3339 UTC time"),
    ("time is not UTC", _set(("generated_at",), "2026-01-12T09:30:00+01:00"), "$.generated_at",
     "not an RFC 3339 UTC time"),
    ("time is a number", _set(("generated_at",), 1768210200), "$.generated_at", "not an RFC 3339 UTC time"),
    ("no project list", _drop(("projects",)), "$.projects", "missing or not a list"),
    ("projects is an object", _set(("projects",), {}), "$.projects", "missing or not a list"),
    ("a project is a string", _set(("projects", CYCLE), "cycle"), "$.projects[0]", "not an object"),
    ("project name is empty", _set(("projects", CYCLE, "project"), ""), "$.projects[0].project",
     "not a non-empty string"),
    ("project name used twice", _set(("projects", EMPTY, "project"), "cycle"), "$.projects[1].project", "used twice"),
    ("unknown status", _set(("projects", CYCLE, "status"), "ok"), "$.projects[0].status",
     "not one of read, no_wbs, no_devdoc_dir, unreadable"),
    ("a reason on a read project", _set(("projects", CYCLE, "status_reason"), "why"), "$.projects[0].status_reason",
     "not null on a read project"),
    ("no reason on an unreadable project", _set(("projects", NOT_UTF8, "status_reason"), None),
     "$.projects[5].status_reason", "not a non-empty string"),
    ("an empty reason", _set(("projects", NOT_UTF8, "status_reason"), ""), "$.projects[5].status_reason",
     "not a non-empty string"),
    ("nodes is null", _set(("projects", CYCLE, "nodes"), None), "$.projects[0].nodes", "not a list"),
    ("nodes on an unreadable project", _set(("projects", NOT_UTF8, "nodes"), [bare_node(1)]), "$.projects[5].nodes",
     "not empty on a project that is not read"),
    ("a node is a number", _set(NODE, 3), NODE_AT, "not an object"),
    ("node id is empty", _set(NODE + ("id",), ""), f"{NODE_AT}.id", "not a non-empty string"),
    ("node id used twice in a project", _set(NODE + ("id",), "n12"), f"{NODE_AT}.id", "used twice"),
    ("node id used twice across projects", _set(NODE + ("id",), "n1"), f"{NODE_AT}.id", "used twice"),
    ("parent is a number", _set(NODE + ("parent",), 7), f"{NODE_AT}.parent", "not a string or null"),
    ("text is null", _set(NODE + ("text",), None), f"{NODE_AT}.text", "not a string"),
    ("unknown glyph", _set(NODE + ("glyph",), "X"), f"{NODE_AT}.glyph", f"not one of {GLYPHS}"),
    ("glyph is a list", _set(NODE + ("glyph",), [" "]), f"{NODE_AT}.glyph", f"not one of {GLYPHS}"),
    ("state against the glyph", _set(NODE + ("state",), "done"), f"{NODE_AT}.state", "does not match the glyph"),
    ("no state", _drop(NODE + ("state",)), f"{NODE_AT}.state", "does not match the glyph"),
    ("source line is a string", _set(NODE + ("source_line",), "7"), f"{NODE_AT}.source_line",
     "not an integer of 1 or more"),
    ("source line is a fraction", _set(NODE + ("source_line",), 7.5), f"{NODE_AT}.source_line",
     "not an integer of 1 or more"),
    ("source line is true", _set(NODE + ("source_line",), True), f"{NODE_AT}.source_line",
     "not an integer of 1 or more"),
    ("no source line", _drop(NODE + ("source_line",)), f"{NODE_AT}.source_line", "not an integer of 1 or more"),
    ("source line 0", _set(NODE + ("source_line",), 0), f"{NODE_AT}.source_line", "not an integer of 1 or more"),
    ("source line used twice", _set(NODE + ("source_line",), 2), f"{NODE_AT}.source_line",
     "used twice in this project"),
    ("issue is empty", _set(NODE + ("issue",), ""), f"{NODE_AT}.issue", "not a non-empty string or null"),
    ("issue is a number", _set(NODE + ("issue",), 3), f"{NODE_AT}.issue", "not a non-empty string or null"),
    ("meta is a list", _set(NODE + ("meta",), []), f"{NODE_AT}.meta", "not an object"),
    ("a meta value is a number", _set(NODE + ("meta", "priority"), 0), f"{NODE_AT}.meta.priority",
     "not a string or a list of strings"),
    ("a meta list holds a number", _set(NODE + ("meta", "depends_on"), ["Issue-1", 2]), f"{NODE_AT}.meta.depends_on",
     "not a string or a list of strings"),
    ("no parse report", _drop(("parse_report",)), "$.parse_report", "not a list"),
    ("an entry is a list", _set(ENTRY, []), "$.parse_report[0]", "not an object"),
    ("an entry's project is a number", _set(ENTRY + ("project",), 3), "$.parse_report[0].project", "not a string"),
    ("an entry's line is 0", _set(ENTRY + ("source_line",), 0), "$.parse_report[0].source_line",
     "not an integer of 1 or more or null"),
    ("an entry's line is a string", _set(ENTRY + ("source_line",), "2"), "$.parse_report[0].source_line",
     "not an integer of 1 or more or null"),
    ("an entry's kind is empty", _set(ENTRY + ("kind",), ""), "$.parse_report[0].kind", "not a non-empty string"),
    ("an entry's message is null", _set(ENTRY + ("message",), None), "$.parse_report[0].message", "not a string"),
    # Pass 2: parent links.
    ("a missing parent", _set(NODE + ("parent",), "nope"), f"{NODE_AT}.parent", "names no node"),
    ("a parent in another project", _set(NODE + ("parent",), "n1"), f"{NODE_AT}.parent",
     "names a node of project 'cycle'"),
    ("a two-node cycle", _cycle_of_two, "$.projects[0].nodes[0].parent", "is not on an earlier line"),
    ("a self-parent", _set(NODE + ("parent",), "n13"), f"{NODE_AT}.parent", "is not on an earlier line"),
    ("a parent on a later line", _late_parent, "$.projects[0].nodes[0].parent", "is not on an earlier line"),
    ("a chain 65 deep", _set(("projects", EMPTY, "nodes"), chain(65)), "$.projects[1].nodes[64].parent",
     "nests deeper than 64 levels"),
    ("a chain 5,000 deep", _set(("projects", EMPTY, "nodes"), chain(5000)), "$.projects[1].nodes[64].parent",
     "nests deeper than 64 levels"),
    ("shapes are checked before parent links", _both_passes, "$.parse_report[53].message", "not a string"),
)


def test_schema_breaking_documents(tmp_path):
    assert len(SCHEMA_ROWS) == 59
    doc = hp.written_doc()
    assert (doc["projects"][IDS]["nodes"][1]["id"], doc["projects"][IDS]["nodes"][0]["id"]) == ("n13", "n12")
    assert doc["projects"][CYCLE]["nodes"][0]["id"] == "n1" and len(doc["parse_report"]) == 54
    assert ", ".join(repr(g) for g in STATE_GLYPHS) == GLYPHS
    seen = set()
    for what, change, where, problem in SCHEMA_ROWS:
        doc = hp.written_doc()
        changed = change(doc)
        doc = doc if changed is None else changed
        error = load_error(tmp_path, doc)
        assert error.kind == "schema", what
        assert str(error) == schema_text(doc_path(tmp_path), where, problem), what
        seen.add((re.sub(r"\[[0-9]+\]", "[]", where), problem))
        with pytest.raises(PortfolioLoadError) as info:
            map_document(doc)
        assert str(info.value) == schema_text("the portfolio document", where, problem), what
    # Distinct (field, problem) pairs, array indexes aside: the plan asks for at least 30.
    assert len(seen) == 36


def test_deepest_allowed_chain_is_read(tmp_path):
    doc = hp.written_doc()
    doc["projects"][EMPTY]["nodes"] = chain(64)
    assert isinstance(hp.read_doc(tmp_path, doc), PortfolioInput)


SWEEP_VALUES = (None, 1, "x", [], {}, True)
# Every field the reader uses, and each container on the way to it.
SWEEP_TARGETS = (
    ("schema",), ("schema_version",), ("generated_at",), ("projects",), ("parse_report",),
    ("projects", IDS), ("projects", IDS, "project"), ("projects", IDS, "status"),
    ("projects", IDS, "status_reason"), ("projects", NOT_UTF8, "status_reason"), ("projects", IDS, "nodes"),
    NODE, NODE + ("id",), NODE + ("parent",), NODE + ("text",), NODE + ("glyph",), NODE + ("state",),
    NODE + ("source_line",), NODE + ("issue",), NODE + ("meta",), NODE + ("meta", "issue"),
    NODE + ("meta", "priority"),
    ENTRY, ENTRY + ("project",), ENTRY + ("source_line",), ENTRY + ("kind",), ENTRY + ("message",),
)


def test_type_sweep_raises_only_load_errors():
    read = refused = 0
    for target in SWEEP_TARGETS:
        for value in SWEEP_VALUES:
            doc = hp.written_doc()
            _set(target, copy.deepcopy(value))(doc)
            try:
                got = map_document(doc)
            except PortfolioLoadError as error:
                assert error.kind in ("schema", "version"), (target, value)
                refused += 1
            else:
                assert isinstance(got, PortfolioInput), (target, value)
                read += 1
    assert read + refused == len(SWEEP_TARGETS) * len(SWEEP_VALUES) == 162
    # Most are refused; the 21 read are legal values (a null parent, any message text, an empty list, ...).
    assert (read, refused) == (21, 141)


def test_whole_document_replaced_by_each_type():
    for value in (None, 1, "x", [], True, 3.5, "devagent-portfolio", [hp.written_doc()]):
        with pytest.raises(PortfolioLoadError) as info:
            map_document(value)
        assert (info.value.kind, str(info.value)) == (
            "schema", schema_text("the portfolio document", "$", "not an object"))
    with pytest.raises(PortfolioLoadError) as info:
        map_document({})
    assert str(info.value) == schema_text("the portfolio document", "$.schema", 'not "devagent-portfolio"')


def test_generated_at_and_version_are_returned(tmp_path):
    got = hp.read_doc(tmp_path, hp.written_doc())
    assert got.generated_at == datetime(2026, 1, 12, 9, 30, tzinfo=timezone.utc)
    assert got.schema_version == "1.1"
    doc = hp.written_doc()
    doc["generated_at"] = "2026-01-12T09:30:00.250+00:00"
    assert map_document(doc).generated_at == datetime(2026, 1, 12, 9, 30, 0, 250000, tzinfo=timezone.utc)


def test_module_docstring_names_its_first_callers():
    text = pj.__doc__
    assert "#12" in text and "#84" in text and "provisional" in text
