"""devagent's portfolio JSON read into Gantry's site model (#83): gantry/portfolio_json.py.

Every test here runs on every leg: the documents are the committed
tests/fixtures/portfolio/written.json, which the pinned devagent release
wrote, and mutations of it built in the test. The one gated test ties a
suppression rule to the pinned parser's own texts.
"""

import ast
import copy
import dataclasses
import random
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import helpers_devagent as hd
import helpers_portfolio as hp
from gantry import crosssite, deps, inputs
from gantry import portfolio_json as pj
from gantry.attention import check_h, read_h
from gantry.deps import refs
from gantry.est_rules import classify_est
from gantry.model import (REPORT_KINDS, STATE_GLYPHS, Leaf, ParsedValue, ReportEntry, Row, SiteData, meta_scalar,
                          meta_value, meta_values, report_order)
from gantry.portfolio_json import (DevagentEntry, PortfolioInput, PortfolioLoadError, ProjectInput, map_document,
                                   read_portfolio)
from gantry.priority import rank
from gantry.roots import directory_roots
from gantry.sites import SiteEntry
from page_safety import HOSTILE

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


def test_meta_value_returns_stored_shapes():
    """No value is read a second time (#12): a list and a string come back as the same object, a
    plain str included, whatever brackets, quotes or spaces it holds."""
    for value in ("[a, 'b']", '"x"', " x ", "[a]", ParsedValue("[x]"), [ParsedValue("a")], []):
        assert meta_value(value) is value
    assert meta_scalar("[a]") == "[a]" and meta_scalar([ParsedValue("a")]) is None
    assert meta_values('"x"') == ['"x"'] and meta_values([ParsedValue("a")]) == ["a"]
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
    assert (pj.SCHEMA_ID, pj.MAJOR, pj.MINOR, pj.MAX_DEPTH) == ("devagent-portfolio", 1, 2, 64)
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
                              "Gantry reads major 1 (schema 1.2)")
        assert f'"{found}"' in str(error) and "schema 1.2" in str(error)
        with pytest.raises(PortfolioLoadError) as info:
            map_document(doc)
        assert info.value.kind == "version" and info.value.source == "the portfolio document"
        assert str(info.value).startswith(f'the portfolio document: portfolio schema_version "{found}"')


def test_newer_minor_with_unknown_fields_is_read(tmp_path):
    plain = hp.read_doc(tmp_path, hp.written_doc())
    assert isinstance(plain, PortfolioInput) and plain.schema_version == "1.2"
    doc = hp.written_doc()
    doc["schema_version"] = "1.9"
    doc["later"] = {"deep": [1, {"x": None}]}
    doc["projects"][CYCLE]["later"] = [1]
    doc["projects"][CYCLE]["nodes"][0]["later"] = "x"
    doc["parse_report"][0]["later"] = None
    got = hp.read_doc(tmp_path, doc)
    assert got.schema_version == "1.9"
    assert dataclasses.replace(got, schema_version="1.2") == plain
    # An unknown field is ignored at the reader's own minor too.
    doc["schema_version"] = "1.2"
    assert hp.read_doc(tmp_path, doc) == plain


def test_models_field_is_not_read():
    # Schema 1.2 gives every leaf a `models` object. The reader does not use it (#14 will), so a
    # 1.1 document, which has none, and a 1.2 document with any value there read alike.
    doc = hp.written_doc()
    plain = map_document(doc)
    leaves = [n for p in doc["projects"] for n in p["nodes"] if n["leaf"]]
    assert len(leaves) == 121 and all(list(n["models"]) == ["thinking", "checking"] for n in leaves)
    assert not any("models" in n for p in doc["projects"] for n in p["nodes"] if not n["leaf"])
    for node in leaves:
        node["models"] = {"thinking": "<script>", "checking": ["x"]}
    assert map_document(doc) == plain
    for node in leaves:
        del node["models"]
    doc["schema_version"] = "1.1"
    assert dataclasses.replace(map_document(doc), schema_version="1.2") == plain


def test_older_minor_is_read(tmp_path):
    plain = hp.read_doc(tmp_path, hp.written_doc())
    doc = hp.written_doc()
    doc["schema_version"] = "1.0"
    got = hp.read_doc(tmp_path, doc)
    assert got.schema_version == "1.0"
    assert dataclasses.replace(got, schema_version="1.2") == plain


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
                                "roots"),
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
        (b"[" * (pj.JSON_DEPTH + 1) + b"]" * (pj.JSON_DEPTH + 1), "nesting is too deep"),
        (b'{"x": "' + b"[" * 100_000, "syntax error at line 1 column 7"),  # an unterminated string
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


def test_depth_limit_is_the_readers_not_the_decoders(tmp_path):
    """The limit is counted before decoding, so the stack size and the Python version do not decide it:
    since 3.14 the decoder raises RecursionError only when the C stack runs out (#186's 3.15 CI leg)."""
    at_limit = b"[" * pj.JSON_DEPTH + b"]" * pj.JSON_DEPTH
    assert load_error(tmp_path, at_limit).kind == "schema"  # parsed: a list is not a portfolio document
    in_strings = b'{"x": "' + b"[{" * 100_000 + b'", "y": ["]]]}}}"]}'
    assert load_error(tmp_path, in_strings).kind == "schema"
    escaped = b'{"x": "\\"' + b"[" * 100_000 + b'"}'
    assert load_error(tmp_path, escaped).kind == "schema"
    assert 7 < pj.JSON_DEPTH < 500  # above written.json's depth, below any decoder's recursion limit


def _parent(doc, path):
    for step in path[:-1]:
        doc = doc[step]
    return doc


def _set(path, value):
    """A mutation: the value at `path` (keys and indexes) becomes `value`."""
    def change(doc):
        _parent(doc, path)[path[-1]] = value
    return change


def _drop(path):
    def change(doc):
        del _parent(doc, path)[path[-1]]
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
    ("time has hour 24", _set(("generated_at",), "2026-01-12T24:00:00Z"), "$.generated_at",
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
    ("no reason key on a read project", _drop(("projects", CYCLE, "status_reason")), "$.projects[0].status_reason",
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
    ("no parent key", _drop(NODE + ("parent",)), f"{NODE_AT}.parent", "not a string or null"),
    ("text is null", _set(NODE + ("text",), None), f"{NODE_AT}.text", "not a string"),
    ("unknown glyph", _set(NODE + ("glyph",), "X"), f"{NODE_AT}.glyph", f"not one of {GLYPHS}"),
    ("glyph is a list", _set(NODE + ("glyph",), [" "]), f"{NODE_AT}.glyph", f"not one of {GLYPHS}"),
    ("state against the glyph", _set(NODE + ("state",), "done"), f"{NODE_AT}.state", "does not match the glyph"),
    ("no state", _drop(NODE + ("state",)), f"{NODE_AT}.state", "does not match the glyph"),
    ("source line is a string", _set(NODE + ("source_line",), "7"), f"{NODE_AT}.source_line",
     "not an integer from 1 to 999999999"),
    ("source line is a fraction", _set(NODE + ("source_line",), 7.5), f"{NODE_AT}.source_line",
     "not an integer from 1 to 999999999"),
    ("source line is true", _set(NODE + ("source_line",), True), f"{NODE_AT}.source_line",
     "not an integer from 1 to 999999999"),
    ("no source line", _drop(NODE + ("source_line",)), f"{NODE_AT}.source_line", "not an integer from 1 to 999999999"),
    ("source line 0", _set(NODE + ("source_line",), 0), f"{NODE_AT}.source_line", "not an integer from 1 to 999999999"),
    ("source line 10**9", _set(NODE + ("source_line",), 10**9), f"{NODE_AT}.source_line",
     "not an integer from 1 to 999999999"),
    ("source line used twice", _set(NODE + ("source_line",), 2), f"{NODE_AT}.source_line",
     "used twice in this project"),
    ("issue is empty", _set(NODE + ("issue",), ""), f"{NODE_AT}.issue", "not a non-empty string or null"),
    ("issue is a number", _set(NODE + ("issue",), 3), f"{NODE_AT}.issue", "not a non-empty string or null"),
    ("no issue key", _drop(NODE + ("issue",)), f"{NODE_AT}.issue", "not a non-empty string or null"),
    ("meta is a list", _set(NODE + ("meta",), []), f"{NODE_AT}.meta", "not an object"),
    ("a meta value is a number", _set(NODE + ("meta", "priority"), 0), f"{NODE_AT}.meta.priority",
     "not a string or a list of strings"),
    ("a meta list holds a number", _set(NODE + ("meta", "depends_on"), ["Issue-1", 2]), f"{NODE_AT}.meta.depends_on",
     "not a string or a list of strings"),
    ("no parse report", _drop(("parse_report",)), "$.parse_report", "not a list"),
    ("an entry is a list", _set(ENTRY, []), "$.parse_report[0]", "not an object"),
    ("an entry's project is a number", _set(ENTRY + ("project",), 3), "$.parse_report[0].project", "not a string"),
    ("an entry's line is 0", _set(ENTRY + ("source_line",), 0), "$.parse_report[0].source_line",
     "not an integer from 1 to 999999999 or null"),
    ("an entry's line is 10**9", _set(ENTRY + ("source_line",), 10**9), "$.parse_report[0].source_line",
     "not an integer from 1 to 999999999 or null"),
    ("an entry's line is a string", _set(ENTRY + ("source_line",), "2"), "$.parse_report[0].source_line",
     "not an integer from 1 to 999999999 or null"),
    ("no line key on an entry", _drop(ENTRY + ("source_line",)), "$.parse_report[0].source_line",
     "not an integer from 1 to 999999999 or null"),
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
    ("shapes are checked before parent links", _both_passes, "$.parse_report[70].message", "not a string"),
)


def test_schema_breaking_documents(tmp_path):
    assert len(SCHEMA_ROWS) == 66
    doc = hp.written_doc()
    assert (doc["projects"][IDS]["nodes"][1]["id"], doc["projects"][IDS]["nodes"][0]["id"]) == ("n13", "n12")
    assert doc["projects"][CYCLE]["nodes"][0]["id"] == "n1" and len(doc["parse_report"]) == 71
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


def test_each_key_removed_raises_only_load_errors():
    # A missing key is a wrong type: never a KeyError, whichever key of a level goes.
    levels = [(), ("projects", IDS), ("projects", NOT_UTF8), NODE, ENTRY]
    read = refused = 0
    for level in levels:
        for key in list(_parent(hp.written_doc(), (*level, None))):
            doc = hp.written_doc()
            _drop(level + (key,))(doc)
            try:
                got = map_document(doc)
            except PortfolioLoadError as error:
                assert error.kind == "schema", (level, key)
                refused += 1
            else:
                assert isinstance(got, PortfolioInput), (level, key)
                read += 1
    # The 16 read lack a field the reader does not use: a project's wbs_title, issues or rollup, a node's
    # typed and derived fields, or its models.
    assert (read, refused) == (16, 25)


def test_a_line_too_long_to_format_is_a_load_error():
    # Only a parsed document can hold it: json.loads refuses the number first.
    doc = hp.written_doc()
    doc["parse_report"][0].update(source_line=10**5000, kind="near_miss_line")
    with pytest.raises(PortfolioLoadError) as info:
        map_document(doc)
    assert str(info.value) == schema_text("the portfolio document", "$.parse_report[0].source_line",
                                          "not an integer from 1 to 999999999 or null")


def test_whole_document_replaced_by_each_type():
    for value in (None, 1, "x", [], True, 3.5, "devagent-portfolio", [hp.written_doc()]):
        with pytest.raises(PortfolioLoadError) as info:
            map_document(value)
        assert (info.value.kind, str(info.value)) == (
            "schema", schema_text("the portfolio document", "$", "not an object"))
    with pytest.raises(PortfolioLoadError) as info:
        map_document({})
    assert str(info.value) == schema_text("the portfolio document", "$.schema", 'not "devagent-portfolio"')


def test_generated_at_is_utc(tmp_path):
    got = hp.read_doc(tmp_path, hp.written_doc())
    assert got.generated_at == datetime(2026, 1, 12, 9, 30, tzinfo=timezone.utc)
    assert got.generated_at.tzinfo is not None and got.generated_at.utcoffset() == timedelta(0)
    assert got.schema_version == "1.2"
    doc = hp.written_doc()
    doc["generated_at"] = "2026-01-12T09:30:00.250+00:00"
    later = map_document(doc).generated_at
    assert later == datetime(2026, 1, 12, 9, 30, 0, 250000, tzinfo=timezone.utc)
    assert later.utcoffset() == timedelta(0)


def test_module_docstring_names_its_first_callers():
    text = pj.__doc__
    assert "#12" in text and "#84" in text and "provisional" in text


# --- nodes into Leaf, Row and SiteData (Task 4) ----------------------------------------------


def mapped():
    return map_document(hp.written_doc())


def project(got, name):
    (found,) = [p for p in got.projects if p.name == name]
    return found


def nodes_by_line(data):
    return {node.line: node for node in (*data.leaves, *data.rows)}


def at(data, line, kind=None):
    return [e for e in data.report if e.line == line and (kind is None or e.kind == kind)]


def test_field_lists():
    names = lambda cls: [f.name for f in dataclasses.fields(cls)]  # noqa: E731
    assert names(DevagentEntry) == ["project", "line", "kind", "message"]
    assert names(ProjectInput) == ["name", "status", "reason", "data"]
    assert names(PortfolioInput) == ["projects", "devagent_report", "generated_at", "schema_version"]
    for cls in (DevagentEntry, ProjectInput, PortfolioInput):
        assert cls.__dataclass_params__.frozen
    got = mapped()
    assert isinstance(got.projects, tuple) and isinstance(got.devagent_report, tuple)
    assert [p.name for p in got.projects] == [p["project"] for p in hp.written_doc()["projects"]]
    assert all(isinstance(p.data, SiteData) for p in got.projects)


def test_issue_less_leaf_keeps_its_outline_name():
    data = project(mapped(), "ids").data
    leaf = nodes_by_line(data)[6]
    assert isinstance(leaf, Leaf)
    assert (leaf.key, leaf.issue, leaf.outline, leaf.title) == ("1.4", None, "1.4", "Second unnamed leaf")
    assert (leaf.glyph, leaf.state, leaf.open, leaf.epic) == (" ", "pending", True, "Issue-Fork-1")
    assert leaf.meta == {} and leaf.deps == () and leaf.priority is None


def test_later_duplicate_is_named_by_outline_and_references_reach_the_first():
    data = project(mapped(), "ids").data
    nodes = nodes_by_line(data)
    assert (nodes[3].key, nodes[3].issue, nodes[3].outline) == ("Issue-Fork-2", "Issue-Fork-2", "1.1")
    assert (nodes[4].key, nodes[4].issue, nodes[4].outline) == ("1.2", "Issue-Fork-2", "1.2")
    assert ("Issue-Fork-2", "Issue-3") in data.links
    assert data.links == (("Issue-Fork-2", "Issue-3"), ("Issue-3", "Issue-4"))
    assert data.held_out == ()


def test_outline_name_equal_to_an_issue_gets_key_collision():
    data = project(mapped(), "ids").data
    nodes = nodes_by_line(data)
    assert (nodes[5].key, nodes[5].outline, nodes[5].issue) == ("1.3~5", "1.3", None)
    assert (nodes[7].key, nodes[7].outline, nodes[7].issue) == ("1.3", "2", "1.3")
    (entry,) = [e for e in data.report if e.kind == "key_collision"]
    assert entry == ReportEntry("ids", 5, "key_collision", "WBS.md line 5: outline position '1.3' is the issue: id "
                                                           "on line 7; this node is named '1.3~5'")
    assert entry.message == pj.MESSAGES["key_collision"].format(line=5, outline="1.3", other=7, key="1.3~5")


def test_outline_ignores_ids_and_array_order():
    plain = mapped()
    doc = hp.written_doc()
    shuffle = random.Random(83).shuffle
    moved = 0
    for entry in doc["projects"]:
        before = [n["source_line"] for n in entry["nodes"]]
        rename = {n["id"]: f"renamed-{len(entry['nodes']) - k}-{n['id']}" for k, n in enumerate(entry["nodes"])}
        for node in entry["nodes"]:
            node["id"] = rename[node["id"]]
            node["parent"] = rename.get(node["parent"])
        shuffle(entry["nodes"])
        moved += [n["source_line"] for n in entry["nodes"]] != before
    assert moved >= 10
    assert map_document(doc) == plain


def test_meta_holds_parsed_values_in_document_order():
    got = mapped()
    scalars = lists = priorities = nodes = 0
    for entry in hp.written_doc()["projects"]:
        found = nodes_by_line(project(got, entry["project"]).data)
        assert sorted(found) == sorted(n["source_line"] for n in entry["nodes"])
        for raw in entry["nodes"]:
            node = found[raw["source_line"]]
            nodes += 1
            assert type(node.meta) is dict and node.meta == raw["meta"]
            assert list(node.meta) == list(raw["meta"])
            for key, value in node.meta.items():
                if isinstance(raw["meta"][key], list):
                    assert type(value) is list and all(type(v) is ParsedValue for v in value), (raw["id"], key)
                    lists += 1
                else:
                    assert type(value) is ParsedValue, (raw["id"], key)
                    scalars += 1
            if isinstance(node, Leaf):
                assert node.priority is node.meta.get("priority")
                if node.priority is not None:
                    # A list priority (edges line 14) is a list of ParsedValue, as any list value.
                    assert type(node.priority) in (ParsedValue, list)
                    priorities += 1
    assert (nodes, scalars, lists, priorities) == META_COUNTS
    # A key order that is not alphabetical, and a quoted value devagent has already unquoted.
    case_10 = nodes_by_line(project(got, "h_values").data)[11]
    assert list(case_10.meta) == ["issue", "h", "priority"]
    lights = nodes_by_line(project(got, "ids").data)[8]
    assert lights.meta["depends_on"] == "Issue-Fork-2" and [d.raw for d in lights.deps] == ["Issue-Fork-2"]
    assert type(lights.deps[0].raw) is ParsedValue


META_COUNTS = (138, 187, 45, 25)  # nodes, scalar values, list values, leaf priorities


def shape(data):
    """{line: (kind, key, outline, the row above or the epic)} for every node."""
    out = {}
    for node in data.leaves:
        out[node.line] = ("leaf", node.key, node.outline, node.epic)
    for node in data.rows:
        out[node.line] = ("row", node.key, node.outline, node.parent)
    return out


def test_rows_parents_and_epics():
    got = mapped()
    rows = project(got, "rows").data
    assert shape(rows) == {
        2: ("row", "Issue-1", "1", None), 3: ("leaf", "Issue-2", "1.1", "Issue-1"),
        4: ("leaf", "Issue-3", "1.2", "Issue-1"), 5: ("row", "Issue-10", "2", None),
        6: ("leaf", "Issue-11", "2.1", "Issue-10"), 7: ("leaf", "Issue-12", "2.2", "Issue-10"),
        8: ("row", "Issue-20", "3", None), 9: ("leaf", "Issue-21", "3.1", "Issue-20"),
        10: ("leaf", "Issue-22", "3.2", "Issue-20"), 11: ("row", "Issue-30", "4", None),
        12: ("leaf", "Issue-31", "4.1", "Issue-30"), 13: ("row", "Issue-40", "5", None),
        14: ("leaf", "Issue-41", "5.1", "Issue-40"), 15: ("leaf", "Issue-50", "6", None),
    }
    assert [leaf.line for leaf in rows.leaves] == [3, 4, 6, 7, 9, 10, 12, 14, 15]
    assert [row.line for row in rows.rows] == [2, 5, 8, 11, 13]
    assert all(isinstance(row, Row) for row in rows.rows)
    assert rows.links == (("Issue-2", "Issue-11"), ("Issue-2", "Issue-12"), ("Issue-41", "Issue-50"))
    swept = nodes_by_line(rows)[12]
    assert (swept.glyph, swept.state, swept.open) == ("x", "done", False)
    # A row under a row, an issue-less row keyed by its outline, and a jump of two levels.
    assert shape(project(got, "nesting").data) == {
        2: ("row", "Issue-1", "1", None), 3: ("row", "Issue-2", "1.1", "Issue-1"),
        4: ("leaf", "Issue-3", "1.1.1", "Issue-1"), 5: ("leaf", "1.1.2", "1.1.2", "Issue-1"),
        6: ("leaf", "Issue-4", "1.2", "Issue-1"), 7: ("leaf", "Issue-5", "1.3", "Issue-1"),
        8: ("row", "2", "2", None), 9: ("leaf", "Issue-6", "2.1", "2"), 10: ("leaf", "Issue-7", "2.2", "2"),
        11: ("leaf", "3", "3", None),
    }
    # Cycles and held-out keys come from gantry.deps, as for the stopgap.
    cycle = project(got, "cycle").data
    assert cycle.held_out == ("Issue-1", "Issue-2", "Issue-3")
    assert [e.message for e in at(cycle, 2, "cycle")] == ["WBS.md line 2: dependency cycle among 'Issue-1', 'Issue-2'"]
    assert len([e for e in cycle.report if e.kind == "waits_on_cycle"]) == 3


def test_bad_priority_is_gantrys_and_p0_is_legal():
    got = mapped()
    (entry,) = at(project(got, "h_values").data, 11, "bad_priority")
    assert entry.message == "WBS.md line 11: priority 'P9' is not P0, P1, P2, P3 or P4; it counts as P3"
    (on_row,) = at(project(got, "rows").data, 5, "bad_priority")
    assert on_row.message == "WBS.md line 5: priority 'high' is not P0, P1, P2, P3 or P4; it counts as P3"
    assert isinstance(nodes_by_line(project(got, "rows").data)[5], Row)
    est = project(got, "est").data
    assert nodes_by_line(est)[9].priority == "P0" and rank(nodes_by_line(est)[9].priority) == 0
    assert at(est, 9, "bad_priority") == []
    assert sum(e.kind == "bad_priority" for p in got.projects for e in p.data.report) == BAD_PRIORITIES


BAD_PRIORITIES = 10  # ids 4, rows 5, refs 5, est 13 and 14, h_values 11, edges 14 and 15, fires 2 and 3


def test_statuses_are_returned_as_written():
    doc = hp.written_doc()
    reason = doc["projects"][NOT_UTF8]["status_reason"]
    got = map_document(doc)
    unreadable = project(got, "not_utf8")
    assert (unreadable.status, unreadable.reason) == ("unreadable", reason)
    assert unreadable.data == SiteData((), (), (), (), (ReportEntry("not_utf8", None, "site_unreadable", reason),))
    empty = project(got, "empty")
    assert (empty.status, empty.reason) == ("read", None)
    assert empty.data == SiteData((), (), (), (), (ReportEntry("empty", None, "no_nodes", "WBS.md has no node lines"),))
    for status in ("no_wbs", "no_devdoc_dir"):
        doc = hp.written_doc()
        doc["projects"][EMPTY].update(status=status, status_reason=f"reason for {status}")
        other = project(map_document(doc), "empty")
        assert (other.status, other.reason) == (status, f"reason for {status}")
        assert other.data == SiteData((), (), (), (), ())
    assert {p.status for p in got.projects} == {"read", "unreadable"}


def test_site_data_holds_gantry_kinds_in_report_order():
    for entry in mapped().projects:
        report = entry.data.report
        assert all(isinstance(e, ReportEntry) and e.kind in REPORT_KINDS and e.site == entry.name for e in report)
        assert list(report) == sorted(report, key=report_order)


def test_map_document_equals_read_portfolio(tmp_path):
    read = hp.read_doc(tmp_path, hp.written_doc())
    again = mapped()
    assert read == again
    assert len(read.projects) == 15 and sum(len(p.data.leaves) + len(p.data.rows) for p in read.projects) == 138


def test_reader_imports_no_parser_of_its_own():
    """The reader's texts are its own (#12 deleted the stopgap they were copied from)."""
    assert tuple(pj.MESSAGES) == ("key_collision", "bad_priority", "no_nodes", "no_issue", "duplicate_issue")
    tree = ast.parse(hp.sp.ROOT.joinpath("gantry", "portfolio_json.py").read_bytes())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "gantry.deps" in imported and not any("stopgap" in name for name in imported)


# --- one disposition per devagent report entry (Task 5) -------------------------------------

MAPPED_KINDS = ("no_issue", "duplicate_issue", "skipped_line", "unresolved_ref")


def entry(project_name, line, kind, message):
    return {"project": project_name, "source_line": line, "kind": kind, "message": message}


def with_entries(*entries, change=None):
    """written.json with report entries added (and `change` applied), then mapped."""
    doc = hp.written_doc()
    if change is not None:
        change(doc)
    doc["parse_report"] += [dict(e) for e in entries]
    return map_document(doc)


def shown_at(got, name, line):
    return [(e.kind, e.message) for e in got.devagent_report if e.project == name and e.line == line]


def as_shown(raw):
    return DevagentEntry(raw["project"], raw["source_line"], raw["kind"], raw["message"])


def mapped_counts(got, names):
    return {kind: sum(e.kind == kind for p in got.projects if p.name in names for e in p.data.report)
            for kind in MAPPED_KINDS}


def test_disposition_constants():
    assert pj.MAPPED == {"leaf_no_issue": "no_issue", "duplicate_issue": "duplicate_issue",
                         "near_miss_line": "skipped_line", "dep_unresolved": "unresolved_ref"}
    assert tuple(pj.MAPPED.values()) == MAPPED_KINDS
    assert pj.SUPPRESSED_ALWAYS == ("h_malformed",)
    assert pj.CYCLE_MEMBER_RE.pattern == (r"(?:^|, |: )(?:(?P<project>[A-Za-z0-9][A-Za-z0-9_.-]*):)?(?P<key>[^ ,()]+) "
                                          r"\(line (?P<line>[0-9]{1,9})\)")
    assert pj.WARNED_KEY_RE.pattern == r"(?:line [0-9]{1,9}: )?(priority|depends_on|satisfies|objective|h) "
    members = [(m.group("project"), m.group("key"), m.group("line")) for m in pj.CYCLE_MEMBER_RE.finditer(
        "dependency cycle: Issue-1 (line 2), beta:Issue-1 (line 4), 1.3 (line 12)")]
    assert members == [(None, "Issue-1", "2"), ("beta", "Issue-1", "4"), (None, "1.3", "12")]


def test_every_entry_has_one_disposition():
    doc = hp.written_doc()
    got = map_document(doc)
    names = [p["project"] for p in doc["projects"]]
    later = ("edges", "fires")  # #83's own source, and #12's
    earlier = [n for n in names if n not in later]
    # devagent_report is a sub-sequence of parse_report, unchanged and in document order.
    written = [as_shown(raw) for raw in doc["parse_report"]]
    assert list(got.devagent_report) == [e for e in written if e in set(got.devagent_report)]

    def partition(projects):
        total = sum(raw["project"] in projects for raw in doc["parse_report"])
        shown = sum(e.project in projects for e in got.devagent_report)
        mapped = sum(mapped_counts(got, projects).values())
        return total, mapped, total - mapped - shown, shown

    # Before `edges`: 46 = 13 mapped + 28 suppressed + 5 shown.
    assert partition(earlier) == (46, 13, 28, 5)
    assert mapped_counts(got, earlier) == {"no_issue": 6, "duplicate_issue": 1, "skipped_line": 2, "unresolved_ref": 4}
    assert [(e.project, e.line, e.kind) for e in got.devagent_report if e.project not in later] == [
        ("cycle", 7, "dep_cycle"), ("fences", 17, "parser_warning"), ("nesting", 10, "unknown_meta_key"),
        ("rows", 9, "dep_cycle"), ("rows", 13, "parent_done_leaf_pending")]
    # With `edges`: four near misses and two unresolved refs mapped; two est_unparseable, an
    # issue_malformed and a meta_unreadable shown.
    assert partition(names) == (71, 31, 28, 12)
    assert partition(["edges"]) == (10, 6, 0, 4)
    # With `fires` (#12): five issue-less leaves and seven near misses mapped; two meta_unreadable and an
    # unknown_meta_key shown (the kinds the fires do not read yet).
    assert partition(["fires"]) == (15, 12, 0, 3)
    assert [(e.line, e.kind) for e in got.devagent_report if e.project == "fires"] == [
        (12, "meta_unreadable"), (13, "unknown_meta_key"), (16, "meta_unreadable")]
    # What is suppressed, by devagent's kind: each is an entry Gantry's own passes report.
    gone = {}
    kept = set(got.devagent_report)
    mapped_from = {"leaf_no_issue", "duplicate_issue", "near_miss_line"}
    for raw in doc["parse_report"]:
        if as_shown(raw) not in kept and raw["kind"] not in mapped_from:
            gone[raw["kind"]] = gone.get(raw["kind"], 0) + 1
    assert gone == {"h_malformed": 14, "est_unparseable": 1, "dep_cycle": 2, "dep_unresolved": 17}
    assert gone["dep_unresolved"] - mapped_counts(got, names)["unresolved_ref"] == 11  # foreign or malformed
    assert 14 + 1 + 2 + 11 == 28


def test_mapped_entries_take_gantrys_texts():
    got = mapped()
    ids = project(got, "ids").data
    assert [e.message for e in at(ids, 4, "duplicate_issue")] == [
        "WBS.md line 4: issue 'Issue-Fork-2' is also on line 3; references resolve to line 3, and this node is "
        "named '1.2'"]
    assert [e.message for e in at(ids, 5, "no_issue")] == ["WBS.md line 5: leaf has no issue: id; it is named '1.3~5'"]
    assert [e.message for e in at(ids, 6, "no_issue")] == ["WBS.md line 6: leaf has no issue: id; it is named '1.4'"]
    assert [e.message for e in at(ids, 9, "unresolved_ref")] == [
        "WBS.md line 9: depends_on entry '1.4' names no issue: id in this file",
        "WBS.md line 9: depends_on entry 'Issue-99' names no issue: id in this file"]
    assert [e.message for e in at(ids, 10)] == ["WBS.md line 10: looks like a node but is not one: - [X] Unknown glyph"]
    assert [(e.kind, e.message) for e in at(ids, 11)] == [
        ("skipped_line", "WBS.md line 11: looks like a node but is not one: - [ ]")]
    assert shown_at(got, "ids", 4) == shown_at(got, "ids", 9) == shown_at(got, "ids", 10) == []


def test_foreign_entry_yields_only_gantrys_entry():
    doc = hp.written_doc()
    assert sum(e["project"] == "refs" and e["source_line"] == 3 and e["kind"] == "dep_unresolved"
               for e in doc["parse_report"]) == 3
    got = map_document(doc)
    data = project(got, "refs").data
    resolved, _ = crosssite.resolve_foreign([SiteEntry("refs", None, None)], {"refs": inputs.Inputs(data, None, None)}, {})
    on_line = [e for e in resolved["refs"].data.report if e.line == 3]
    assert [e.kind for e in on_line] == ["foreign_site_absent"] * 3
    assert sorted(e.message for e in on_line) == sorted(
        crosssite.MESSAGES["foreign_site_absent"].format(line=3, entry=raw)
        for raw in ("alpha:Issue-1", "beta:Issue-1", "Alpha:Issue-1"))
    assert shown_at(got, "refs", 3) == []
    assert [e for e in got.devagent_report if e.project == "refs"] == []


def apply(tmp_path, got, name):
    """gantry.inputs.apply_inputs over a mapped project, with no estimates file (as tests/test_inputs.py runs it)."""
    folder = tmp_path / name
    folder.mkdir()
    config = SimpleNamespace(sites={}, default_unit_hours=6)
    return inputs.apply_inputs(directory_roots([folder]), SiteEntry(name, folder, None), project(got, name).data,
                               config).data


def test_unparsed_est_appears_once(tmp_path):
    got = mapped()
    est = apply(tmp_path, got, "est")
    (once,) = at(est, 7, "est_unparseable")
    assert once.message == inputs.MESSAGES["est_unparseable"].format(line=7, raw="2-3d", key="Issue-5")
    assert len(at(est, 16, "est_unparseable")) == 1
    # devagent reads `2-3d` as a time range since 3.2.0, so only line 16's entry is devagent's too.
    assert shown_at(got, "est", 7) == shown_at(got, "est", 16) == []
    # A finished leaf and a row: Gantry's pass reports neither, so devagent's entry stays.
    edges = apply(tmp_path, got, "edges")
    assert [e for e in edges.report if e.kind == "est_unparseable"] == []
    text = 'est "?3d" is not a time, non-time or placeholder estimate'
    assert shown_at(got, "edges", 8) == shown_at(got, "edges", 9) == [("est_unparseable", text)]
    nodes = nodes_by_line(project(got, "edges").data)
    assert isinstance(nodes[8], Leaf) and not nodes[8].open and isinstance(nodes[9], Row)


def _cycle_through_a_row(doc):
    """`empty` gets a cycle of its own over lines 2 and 4, through the row on line 3."""
    one, row, two = bare_node(2, name="k"), bare_node(3, name="k"), bare_node(4, parent="k3", name="k")
    for node, issue, wants in ((one, "Issue-1", ["Issue-9"]), (row, "Issue-9", None), (two, "Issue-2", ["Issue-1"])):
        node["issue"] = issue
        node["meta"] = {"issue": issue, **({"depends_on": wants} if wants else {})}
    doc["projects"][EMPTY]["nodes"] = [one, row, two]


def test_cross_project_cycle_is_shown():
    own = entry("empty", 2, "dep_cycle", "dependency cycle: Issue-1 (line 2), Issue-2 (line 4)")
    crossing = entry("empty", 2, "dep_cycle", "dependency cycle: Issue-1 (line 2), beta:Issue-1 (line 4)")
    got = with_entries(own, crossing, change=_cycle_through_a_row)
    data = project(got, "empty").data
    assert [e.message for e in at(data, 2, "cycle")] == ["WBS.md line 2: dependency cycle among 'Issue-1', 'Issue-2'"]
    assert data.held_out == ("Issue-1", "Issue-2")
    # The same lines, but one member is another project's: no local cycle, so it is shown as written.
    assert shown_at(got, "empty", 2) == [("dep_cycle", crossing["message"])]


def test_edge_entries_are_shown():
    def no_wbs(doc):
        doc["projects"][EMPTY].update(status="no_wbs", status_reason="the devdoc dir has no WBS.md")

    edge = (
        entry("ids", None, "leaf_no_issue", "leaf 'Unnamed leaf' has no issue"),
        entry("ids", None, "near_miss_line", "looks like a node but is not one: - [q] no line"),
        entry("ids", 3, "near_miss_line", "looks like a node but is not one: - [ ] Dock"),
        entry("empty", 4, "duplicate_issue", "issue 'Issue-1' also appears at line 2"),
        entry("gone", 2, "leaf_no_issue", "leaf 'Elsewhere' has no issue"),
        entry("not_utf8", 2, "h_malformed", 'h "9" is not one digit 0-4; h reads as null'),
        entry("ids", None, "h_malformed", 'h "9" is not one digit 0-4; h reads as null'),
        entry("ids", 12, "h_malformed", 'h "9" is not one digit 0-4; h reads as null'),
        entry("ids", 3, "h_malformed", 'h "9" is not one digit 0-4; h reads as null'),
        entry("ids", 12, "dep_unresolved", "depends_on 'Issue-99' matches no issue in this project"),
        entry("ids", 2, "leaf_no_issue", "leaf 'Ferry works' has no issue"),
        entry("ids", 3, "leaf_no_issue", "leaf 'Dock' has no issue"),
    )
    plain = mapped()
    got = with_entries(*edge, change=no_wbs)
    assert list(got.devagent_report[len(plain.devagent_report):]) == [as_shown(e) for e in edge]
    assert got.devagent_report[:len(plain.devagent_report)] == plain.devagent_report
    # Nothing was mapped from them: every site's own report is as it was.
    for before in plain.projects:
        if before.name != "empty":
            assert project(got, before.name).data == before.data
    assert project(got, "empty").data == SiteData((), (), (), (), ())


def test_bad_priority_and_malformed_entry_appear_once():
    got = mapped()
    h_values = project(got, "h_values").data
    assert len(at(h_values, 11, "bad_priority")) == 1
    assert shown_at(got, "h_values", 11) == []
    refs_data = project(got, "refs").data
    malformed = [e.message for e in at(refs_data, 4)]
    assert [e.kind for e in at(refs_data, 4)] == ["malformed_ref"] * 4
    assert malformed.count(deps.MESSAGES["malformed_ref"].format(line=4, entry=":Issue-1")) == 1
    assert shown_at(got, "refs", 4) == []
    # Line 5 holds one of each: a local entry naming nothing (mapped) and a malformed one (Gantry's).
    assert [(e.kind, e.message) for e in at(refs_data, 5) if e.kind != "bad_priority"] == [
        ("unresolved_ref", "WBS.md line 5: depends_on entry 'Issue-99' names no issue: id in this file"),
        ("malformed_ref", "WBS.md line 5: depends_on entry 'a:b:c' is not an id or <site>:<id>")]
    assert shown_at(got, "refs", 5) == []


def test_dep_unresolved_is_matched_to_its_entry():
    def longer(doc):
        horns = doc["projects"][IDS]["nodes"][7]
        assert horns["source_line"] == 9
        horns["meta"]["depends_on"] = ["Issue-3", "Issue-99", "Issue-99' x"]

    named = entry("ids", 9, "dep_unresolved", "depends_on 'Issue-99' x' matches no issue in this project")
    absent = entry("ids", 9, "dep_unresolved", "depends_on 'Issue-98' matches no issue in this project")
    resolves = entry("ids", 9, "dep_unresolved", "depends_on 'Issue-3' matches no issue in this project")
    no_key = entry("ids", 3, "dep_unresolved", "depends_on 'Issue-99' matches no issue in this project")
    got = with_entries(named, absent, resolves, no_key, change=longer)
    data = project(got, "ids").data
    # The longest entry the message opens with is the one it is about.
    assert [e.message for e in at(data, 9, "unresolved_ref")] == [
        "WBS.md line 9: depends_on entry 'Issue-99' names no issue: id in this file",
        "WBS.md line 9: depends_on entry \"Issue-99' x\" names no issue: id in this file"]
    # An entry the node no longer holds, another it never held, one that Gantry resolves: each shown.
    assert shown_at(got, "ids", 9) == [
        ("dep_unresolved", "depends_on '1.4' matches no issue in this project"),
        ("dep_unresolved", absent["message"]), ("dep_unresolved", resolves["message"])]
    # And so is one on a node with no depends_on.
    assert shown_at(got, "ids", 3) == [("dep_unresolved", no_key["message"])]


# The pinned parser's wording for a priority it refuses (devagent reads P0 as legal since 3.1.0).
PRIORITY_WARNING = "line 13: priority 'P5' is not P0, P1, P2, P3 or P4; ignored"


def test_priority_warning_is_not_shown():
    got = with_entries(entry("est", 13, "parser_warning", PRIORITY_WARNING))
    est = project(got, "est").data
    assert nodes_by_line(est)[13].meta["priority"] == "P5"
    # Gantry's own bad_priority is the one entry on the line; devagent's warning is not shown beside it.
    assert [e.kind for e in at(est, 13)] == ["bad_priority"] and shown_at(got, "est", 13) == []
    assert got == mapped()
    # The same warning on a node with no priority names nothing Gantry checks there: shown.
    assert "priority" not in nodes_by_line(est)[10].meta
    moved = with_entries(entry("est", 10, "parser_warning", PRIORITY_WARNING))
    assert shown_at(moved, "est", 10) == [("parser_warning", PRIORITY_WARNING)]
    # A warning with no line, or on a line with no node, is shown too.
    for line in (None, 2):
        loose = with_entries(entry("est", line, "parser_warning", PRIORITY_WARNING))
        assert shown_at(loose, "est", line) == [("parser_warning", PRIORITY_WARNING)]


def _objective_on_brace(doc):
    brace = doc["projects"][NESTING]["nodes"][8]
    assert brace["source_line"] == 10 and brace["meta"]["satisfies"] == "a:X"
    brace["meta"]["objective"] = ["a:X"]


def test_satisfies_objective_and_h_warnings_are_not_shown_but_bad_h_is():
    satisfies = "line 10: satisfies entry 'x' is not <namespace>:<id>"
    objective = "line 10: objective entry 'x' is not <namespace>:<id>"
    bad_h = "line 8: h 'high' is not an integer from 0 to 4"
    plain = with_entries(change=_objective_on_brace)
    got = with_entries(entry("nesting", 10, "parser_warning", satisfies),
                       entry("nesting", 10, "parser_warning", objective),
                       entry("h_values", 8, "parser_warning", bad_h), change=_objective_on_brace)
    assert got == plain
    assert shown_at(got, "nesting", 10) == [("unknown_meta_key", "unknown metadata key 'a'")]
    # The 14 real h_malformed entries are not shown; Gantry's own pass reports each, and the row on line 18.
    doc = hp.written_doc()
    malformed = [e["source_line"] for e in doc["parse_report"] if e["kind"] == "h_malformed"]
    assert len(malformed) == 14 and all(e["project"] == "h_values" for e in doc["parse_report"]
                                         if e["kind"] == "h_malformed")
    assert not any(e.kind == "h_malformed" for e in got.devagent_report)
    assert shown_at(got, "h_values", 8) == []
    checked = check_h(project(got, "h_values").data, "h_values")
    assert [e.line for e in checked.report if e.kind == "bad_h"] == sorted(malformed + [18])
    assert not any(e.kind == "bad_h" for e in project(got, "h_values").data.report)
    # Each warning on a node that does not hold its key is shown: Gantry reports nothing there.
    moved = with_entries(entry("nesting", 9, "parser_warning", satisfies),
                         entry("nesting", 9, "parser_warning", objective),
                         entry("h_values", 2, "parser_warning", bad_h))
    assert shown_at(moved, "nesting", 9) == [("parser_warning", satisfies), ("parser_warning", objective)]
    assert shown_at(moved, "h_values", 2) == [("parser_warning", bad_h)]
    # satisfies and objective are read on leaves only, depends_on on open leaves only.
    on_rows = with_entries(entry("rows", 13, "parser_warning", "depends_on entry 'x' is odd"),
                           entry("rows", 4, "parser_warning", "depends_on entry 'x' is odd"),
                           entry("rows", 6, "parser_warning", "depends_on entry 'x' is odd"))
    assert shown_at(on_rows, "rows", 13)[-1] == ("parser_warning", "depends_on entry 'x' is odd")
    assert shown_at(on_rows, "rows", 4) == [("parser_warning", "depends_on entry 'x' is odd")]
    assert shown_at(on_rows, "rows", 6) == []


def test_matched_dep_cycle_appears_once_and_unmatched_as_written():
    got = mapped()
    cycle, est, rows = (project(got, name).data for name in ("cycle", "est", "rows"))
    # devagent and Gantry agree on these two: Gantry's `cycle` entry is the one shown.
    assert [e.message for e in at(cycle, 2, "cycle")] == ["WBS.md line 2: dependency cycle among 'Issue-1', 'Issue-2'"]
    assert [e.message for e in at(est, 22, "cycle")] == ["WBS.md line 22: dependency cycle among 'Issue-30', 'Issue-31'"]
    assert shown_at(got, "cycle", 2) == shown_at(got, "est", 22) == []
    # A cycle through a finished leaf, and a leaf under a row that waits on it: devagent's alone.
    assert at(cycle, 7, "cycle") == [] and at(rows, 9, "cycle") == []
    assert shown_at(got, "cycle", 7) == [("dep_cycle", "dependency cycle: Issue-6 (line 7), Issue-7 (line 8)")]
    assert shown_at(got, "rows", 9) == [("dep_cycle", "dependency cycle: Issue-21 (line 9)")]
    # The same members on other lines match no cycle of Gantry's.
    other = with_entries(entry("cycle", 2, "dep_cycle", "dependency cycle: Issue-1 (line 2), Issue-2 (line 4)"),
                         entry("cycle", 2, "dep_cycle", "a cycle with no member list"))
    assert shown_at(other, "cycle", 2) == [("dep_cycle", "dependency cycle: Issue-1 (line 2), Issue-2 (line 4)"),
                                           ("dep_cycle", "a cycle with no member list")]


def test_unnamed_and_colliding_kinds_are_shown_as_written():
    later = entry("ids", 3, "kind_from_a_later_release", "something new about this node")
    collides = entry("ids", 3, "duplicate_issue", "issue 'Issue-Fork-2' also appears at line 3")
    est = entry("ids", 3, "est_unparseable", 'est "soon" is not a time, non-time or placeholder estimate')
    whole = entry("ids", None, "checklist_unreadable", "Issue-3/checklist.md could not be read")
    gantrys = entry("ids", 3, "bad_priority", "a devagent kind that shares a Gantry name")
    got = with_entries(later, collides, est, whole, gantrys)
    assert shown_at(got, "ids", 3) == [(e["kind"], e["message"]) for e in (later, collides, est, gantrys)]
    assert shown_at(got, "ids", None) == [("checklist_unreadable", whole["message"])]
    assert project(got, "ids").data == project(mapped(), "ids").data
    for site in got.projects:
        assert all(e.kind in REPORT_KINDS for e in site.data.report)
        assert sorted(site.data.report, key=report_order) == list(site.data.report)
    assert {e.kind for e in got.devagent_report} - set(REPORT_KINDS) >= {"kind_from_a_later_release", "dep_cycle"}


def test_hostile_strings_are_returned_unchanged(tmp_path):
    assert len(HOSTILE) == 11
    for text in HOSTILE:
        doc = hp.written_doc()
        doc["projects"][NOT_UTF8]["status_reason"] = text
        doc["projects"][EMPTY].update(status="no_wbs", status_reason=text)
        doc["parse_report"] += [entry("ids", 3, "kind_from_a_later_release", text), entry("ids", 3, text, "as a kind"),
                                entry(text, 3, text, text), entry("ids", 12, "near_miss_line", text)]
        for got in (map_document(doc), hp.read_doc(tmp_path, doc)):
            unreadable = project(got, "not_utf8")
            assert unreadable.reason == text
            assert unreadable.data.report == (ReportEntry("not_utf8", None, "site_unreadable", text),)
            assert project(got, "empty").reason == text
            assert shown_at(got, "ids", 3) == [("kind_from_a_later_release", text), (text, "as a kind")]
            assert DevagentEntry(text, 3, text, text) in got.devagent_report
            # A mapped near miss carries devagent's message, whole, after Gantry's line prefix.
            assert [e.message for e in at(project(got, "ids").data, 12)] == [f"WBS.md line 12: {text}"]


def _constants(path, names):
    """Module-level string constants of a Python source, by name; nothing is executed."""
    tree = ast.parse(path.read_bytes())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in names and isinstance(node.value, ast.Constant):
                found[node.targets[0].id] = node.value.value
    return found


def test_warned_key_re_matches_the_pinned_parser_texts():
    checkout = hd.require_devagent()
    texts = _constants(checkout / "scripts" / "lib" / "wbs-parser.py",
                       ("PRIORITY_WARNING", "NAMESPACED_WARNING", "H_WARNING"))
    assert sorted(texts) == ["H_WARNING", "NAMESPACED_WARNING", "PRIORITY_WARNING"]
    cases = (
        (texts["PRIORITY_WARNING"].format("P5"), "priority"),
        (texts["NAMESPACED_WARNING"].format("satisfies", "x"), "satisfies"),
        (texts["NAMESPACED_WARNING"].format("objective", "x"), "objective"),
        (texts["H_WARNING"].format("high"), "h"),
    )
    for text, key in cases:
        for message in (text, f"line 4: {text}"):
            match = pj.WARNED_KEY_RE.match(message)
            assert match is not None and match.group(1) == key, message
    # The one warning the pinned parser does print is not about a key.
    assert pj.WARNED_KEY_RE.match("wbs-parser: unclosed code fence at line 17 ignored; lines after it are parsed "
                                  "as WBS nodes") is None
    assert texts["PRIORITY_WARNING"].format("P5") == PRIORITY_WARNING.removeprefix("line 13: ")


def test_many_unresolved_entries_cost_one_pass(monkeypatch):
    # One node with 2,000 entries that name nothing: each devagent entry is matched through one index of the
    # node's depends_on, built once, so the work grows with the entries, not with their square.
    doc = hp.written_doc()
    edges = next(p for p in doc["projects"] if p["project"] == "edges")
    node = next(n for n in edges["nodes"] if n["source_line"] == 11)
    template = next(e for e in doc["parse_report"] if e["project"] == "edges" and e["source_line"] == 11)
    names = [f"Gone-{i}" for i in range(2000)]
    node["meta"]["depends_on"] = names
    doc["parse_report"] = [e for e in doc["parse_report"] if e is not template and not (
        e["project"] == "edges" and e["source_line"] == 11)]
    doc["parse_report"] += [dict(template, message=template["message"].replace(
        template["message"].split("'")[1], name)) for name in sorted(names)]
    calls = []
    real = pj.meta_values
    monkeypatch.setattr(pj, "meta_values", lambda raw: calls.append(raw) or real(raw))
    got = map_document(doc)
    found = at(project(got, "edges").data, 11, "unresolved_ref")
    assert [e.message for e in found] == [deps.MESSAGES["unresolved_ref"].format(line=11, entry=name)
                                          for name in names]
    assert sum(raw is not None and len(real(raw)) == 2000 for raw in calls) == 1
