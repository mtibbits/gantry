"""devagent's portfolio JSON read into Gantry's site model (#83): gantry/portfolio_json.py.

Every test here runs on every leg: the documents are the committed
tests/fixtures/portfolio/written.json, which the pinned devagent release
wrote, and mutations of it built in the test. The one gated test ties a
suppression rule to the pinned parser's own texts.
"""

import ast
import copy
import dataclasses
import functools
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
from gantry.model import (REPORT_KINDS, STATE_GLYPHS, Leaf, Models, ParsedValue, ReportEntry, Row, SiteData,
                          meta_scalar, meta_values, report_order)
from gantry.portfolio_json import (ActualsInput, DevagentEntry, EventRow, IssueActuals, PortfolioInput,
                                   PortfolioLoadError, ProjectInput, map_document, read_portfolio)
from gantry.priority import rank
from gantry.roots import directory_roots
from gantry.sites import SiteEntry
from page_safety import HOSTILE

# --- ParsedValue and the accessors (Task 1) --------------------------------------------------


def test_parsed_shapes_are_not_re_read():
    listed = [ParsedValue("a"), ParsedValue("[b]")]
    assert meta_values(listed) is listed
    bracket = ParsedValue("[x]")
    assert meta_scalar(bracket) == "[x]" and meta_scalar(bracket) is bracket
    quoted = ParsedValue('"a"')
    assert meta_values(quoted) == ['"a"'] and meta_values(quoted)[0] is quoted
    assert meta_scalar(["a"]) is None
    assert meta_scalar(None) is None
    assert meta_values(None) == []
    assert meta_values(ParsedValue("")) == []
    assert meta_values([]) == []
    # Surrounding space is devagent's to strip, not the accessor's.
    assert meta_scalar(ParsedValue(" x ")) == " x "
    assert ParsedValue.__slots__ == () and not hasattr(ParsedValue("x"), "__dict__")


def test_accessors_return_stored_shapes():
    """No value is read a second time (#12): a string or a non-empty list comes back as the same
    object, a plain str included, whatever brackets, quotes or spaces it holds."""
    for value in ("[a, 'b']", '"x"', " x ", "[a]", ParsedValue("[x]")):
        assert meta_scalar(value) is value and meta_values(value)[0] is value
    for value in ([ParsedValue("a")], ["[a]", '"b"']):
        assert meta_values(value) is value
    assert meta_values([]) == []
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
MODELS, MODELS_PROJECT = 15, 16  # #14's two projects, last in FIXTURE_SOURCES
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
    assert (pj.SCHEMA_ID, pj.MAJOR, pj.MINOR, pj.MAX_DEPTH) == ("devagent-portfolio", 1, 3, 64)
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
                              "Gantry reads major 1 (schema 1.3)")
        assert f'"{found}"' in str(error) and "schema 1.3" in str(error)
        with pytest.raises(PortfolioLoadError) as info:
            map_document(doc)
        assert info.value.kind == "version" and info.value.source == "the portfolio document"
        assert str(info.value).startswith(f'the portfolio document: portfolio schema_version "{found}"')


def test_newer_minor_with_unknown_fields_is_read(tmp_path):
    plain = hp.read_doc(tmp_path, hp.written_doc())
    assert isinstance(plain, PortfolioInput) and plain.schema_version == "1.3"
    doc = hp.written_doc()
    doc["schema_version"] = "1.9"
    doc["later"] = {"deep": [1, {"x": None}]}
    doc["projects"][CYCLE]["later"] = [1]
    doc["projects"][CYCLE]["nodes"][0]["later"] = "x"
    doc["parse_report"][0]["later"] = None
    got = hp.read_doc(tmp_path, doc)
    assert got.schema_version == "1.9"
    assert dataclasses.replace(got, schema_version="1.3") == plain
    # An unknown field is ignored at the reader's own minor too.
    doc["schema_version"] = "1.3"
    assert hp.read_doc(tmp_path, doc) == plain


# The models fixture (#14): tests/fixtures/portfolio/models.md, read as two projects.
MODELS_LINES = {"Epic": 1, "Both": 2, "Think only": 3, "Bad alone": 4, "Bad beside legal": 5, "Inherit": 6,
                "Done keyed": 7, "No keys": 8}
# The leaf keys the fixture gives each leaf, legal ones only.
MODELS_KEYS = {"Both": {"thinking": "fable", "checking": "sonnet"}, "Think only": {"thinking": "sonnet"},
               "Bad alone": {}, "Bad beside legal": {"checking": "fable"}, "Inherit": {"thinking": "inherit"},
               "Done keyed": {"checking": "opus"}, "No keys": {}}
LEAF_BAD = 'thinking "gpt" is not one of: sonnet opus haiku fable inherit; the key counts as absent'


def test_pinned_aggregator_models_contract():
    # What the pinned aggregator writes for model keys (#14): a legal leaf key is a `wbs` class, the
    # project's step_models fill the other classes as `project`, a malformed leaf key is one bad_model
    # entry at its line, and a parent row's key (legal or not) reaches no entry at all: the gap
    # mtibbits/devagent#1236 reports. A later pin that closes it fails here first.
    doc = hp.written_doc()
    by_name = {p["project"]: p for p in doc["projects"]}
    for name, project_checking in (("models", None), ("models_project", {"value": "opus", "source": "project"})):
        nodes = {n["text"]: n for n in by_name[name]["nodes"]}
        assert sorted(nodes) == sorted(MODELS_LINES), name
        assert {t: n["source_line"] for t, n in nodes.items()} == MODELS_LINES, name
        assert "models" not in nodes["Epic"] and not nodes["Epic"]["leaf"], name
        for text, keys in MODELS_KEYS.items():
            want = {cls: {"value": keys[cls], "source": "wbs"} if cls in keys else None
                    for cls in ("thinking", "checking")}
            if "checking" not in keys:
                want["checking"] = project_checking
            assert nodes[text]["models"] == want, (name, text)
        entries = [e for e in doc["parse_report"] if e["project"] == name]
        assert [(e["kind"], e["source_line"], e["message"]) for e in entries if e["kind"] == "bad_model"] == [
            ("bad_model", MODELS_LINES["Bad alone"], LEAF_BAD),
            ("bad_model", MODELS_LINES["Bad beside legal"], LEAF_BAD)]
        assert not [e for e in entries if e["kind"] == "parser_warning"], name
        assert not [e for e in entries if e["source_line"] == MODELS_LINES["Epic"]], name


MODEL_TOKENS = "sonnet opus haiku fable inherit"
# A model-key parser warning in the pinned parser's words (MODEL_WARNING), which no release reports today.
MODEL_KEY_WARNING = "line {line}: {cls} 'gpt' is not one of: " + MODEL_TOKENS
CONFIG_BAD = 'step_models.checking "gpt" is not one of: sonnet opus haiku fable inherit; models.checking is null'


def test_model_key_warnings_suppressed_on_leaves_only():
    lines = MODELS_LINES
    planted = [(lines["Bad alone"], "thinking"), (lines["Bad beside legal"], "thinking"),
               (lines["Bad beside legal"], "checking"), (lines["Epic"], "thinking"), (lines["No keys"], "thinking")]
    doc = hp.written_doc()
    doc["parse_report"] += [entry("models", line, "parser_warning", MODEL_KEY_WARNING.format(line=line, cls=cls))
                            for line, cls in planted]
    got = map_document(doc)
    warned = [(e.line, e.message) for e in got.devagent_report if e.project == "models" and e.kind == "parser_warning"]
    # On a leaf holding the key beside devagent's bad_model for it: suppressed. On a leaf whose bad_model
    # names the other key (Bad beside legal's legal checking), on the row, and on a leaf without the key:
    # shown as written.
    assert warned == [(lines["Bad beside legal"], MODEL_KEY_WARNING.format(line=lines["Bad beside legal"],
                                                                           cls="checking")),
                      (lines["Epic"], MODEL_KEY_WARNING.format(line=lines["Epic"], cls="thinking")),
                      (lines["No keys"], MODEL_KEY_WARNING.format(line=lines["No keys"], cls="thinking"))]
    assert [e.line for e in got.devagent_report if e.project == "models" and e.kind == "bad_model"] == [
        lines["Bad alone"], lines["Bad beside legal"]]


def test_bad_model_shown_once_each():
    doc = hp.written_doc()
    doc["parse_report"].append(entry("models_project", None, "bad_model", CONFIG_BAD))
    got = map_document(doc)
    for name in ("models", "models_project"):
        for line in (MODELS_LINES["Bad alone"], MODELS_LINES["Bad beside legal"]):
            assert got.devagent_report.count(DevagentEntry(name, line, "bad_model", LEAF_BAD)) == 1, (name, line)
    assert got.devagent_report.count(DevagentEntry("models_project", None, "bad_model", CONFIG_BAD)) == 1
    assert sum(e.kind == "bad_model" for e in got.devagent_report) == 5
    assert all(e.kind != "bad_model" for p in got.projects for e in p.data.report)


def models_of(got, name):
    """{leaf title: Leaf.models} of project `name`."""
    return {leaf.title: leaf.models for leaf in project(got, name).data.leaves}


def without_models(got):
    """`got` as an older minor reads it: schema_models False, and no leaf with models (#14)."""
    projects = []
    for p in got.projects:
        leaves = tuple(dataclasses.replace(leaf, models=None) for leaf in p.data.leaves)
        data = dataclasses.replace(p.data, leaves=leaves, schema_models=False)
        projects.append(dataclasses.replace(p, data=data))
    return dataclasses.replace(got, projects=tuple(projects))


def test_wbs_classes_become_leaf_models():
    got = map_document(hp.written_doc())
    wbs = {
        "Both": Models("fable", "sonnet", "wbs", ("wbs", "wbs")),
        "Think only": Models("sonnet", None, "wbs", ("wbs", None)),
        "Bad alone": None,
        "Bad beside legal": Models(None, "fable", "wbs", (None, "wbs")),
        "Inherit": Models("inherit", None, "wbs", ("wbs", None)),
        "Done keyed": Models(None, "opus", "wbs", (None, "wbs")),
        "No keys": None,
    }
    assert models_of(got, "models") == wbs
    # Beside a wbs class, the project's checking class is a tier; alone it is not (round-1 resolution 1).
    assert models_of(got, "models_project") == {
        **wbs, "Think only": Models("sonnet", "opus", "wbs", ("wbs", "project")),
        "Inherit": Models("inherit", "opus", "wbs", ("wbs", "project"))}
    assert sum(m is not None for m in wbs.values()) == 5
    for p in got.projects:
        if p.name not in ("models", "models_project"):
            assert all(leaf.models is None for leaf in p.data.leaves), p.name
        assert p.data.schema_models is True, p.name


def test_non_wbs_sources():
    def both(thinking, checking):
        doc = hp.set_models(hp.written_doc(), "models", 2, thinking=thinking, checking=checking)
        return models_of(map_document(doc), "models")["Both"]

    assert both(("opus", "project"), ("sonnet", "project")) is None
    assert both(("opus", "wbs"), ("sonnet", "config")) == Models("opus", None, "wbs", ("wbs", None))
    assert both(("opus", "config"), None) is None
    # The match is case-sensitive.
    assert both(("opus", "WBS"), None) is None


def test_wbs_value_outside_tokens_is_kept():
    doc = hp.set_models(hp.written_doc(), "models", 2, thinking=("sonnet-5", "wbs"), checking=None)
    assert models_of(map_document(doc), "models")["Both"] == Models("sonnet-5", None, "wbs", ("wbs", None))


def test_schema_models_flag_by_minor():
    for minor in ("1.2", "1.9"):
        doc = hp.written_doc()
        doc["schema_version"] = minor
        got = map_document(doc)
        assert all(p.data.schema_models is True for p in got.projects), minor
        assert models_of(got, "models")["Both"] == Models("fable", "sonnet", "wbs", ("wbs", "wbs"))
    for minor in ("1.1", "1.0"):
        doc = hp.written_doc()
        doc["schema_version"] = minor
        got = map_document(doc)
        assert all(p.data.schema_models is False for p in got.projects), minor
        assert all(leaf.models is None for p in got.projects for leaf in p.data.leaves), minor
        # At an older minor the field is unknown: garbage there is ignored, not checked.
        doc = hp.written_doc()
        doc["schema_version"] = minor
        doc["projects"][MODELS]["nodes"][1]["models"] = "x"
        assert map_document(doc).schema_version == minor


def test_older_minor_is_read(tmp_path):
    plain = hp.read_doc(tmp_path, hp.written_doc())
    for minor in ("1.0", "1.1"):
        doc = hp.written_doc()
        doc["schema_version"] = minor
        got = hp.read_doc(tmp_path, doc)
        assert got.schema_version == minor
        # The same data, without the tiers 1.2 carries: nothing else differs.
        assert dataclasses.replace(got, schema_version="1.3") == without_models(plain)
        assert got != dataclasses.replace(plain, schema_version=minor)
    # At 1.2 the tiers are read; the actuals are read by their own key, whatever the minor (#213).
    doc = hp.written_doc()
    doc["schema_version"] = "1.2"
    got = hp.read_doc(tmp_path, doc)
    assert got.schema_version == "1.2" and dataclasses.replace(got, schema_version="1.3") == plain
    assert got.actuals.wired is True


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
    # A leaf's models (#14), checked at minor 2 and later.
    ("models is a list", _set(NODE + ("models",), []), f"{NODE_AT}.models", "not an object"),
    ("no thinking class", _drop(NODE + ("models", "thinking")), f"{NODE_AT}.models.thinking",
     "not an object or null"),
    ("a checking class is a string", _set(NODE + ("models", "checking"), "opus"), f"{NODE_AT}.models.checking",
     "not an object or null"),
    ("a class's value is empty", _set(NODE + ("models", "thinking"), {"value": "", "source": "wbs"}),
     f"{NODE_AT}.models.thinking.value", "not a non-empty string"),
    ("a class's source is a number", _set(NODE + ("models", "checking"), {"value": "opus", "source": 1}),
     f"{NODE_AT}.models.checking.source", "not a string"),
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
    ("shapes are checked before parent links", _both_passes, "$.parse_report[91].message", "not a string"),
)


def test_schema_breaking_documents(tmp_path):
    assert len(SCHEMA_ROWS) == 71
    doc = hp.written_doc()
    assert (doc["projects"][IDS]["nodes"][1]["id"], doc["projects"][IDS]["nodes"][0]["id"]) == ("n13", "n12")
    assert doc["projects"][CYCLE]["nodes"][0]["id"] == "n1" and len(doc["parse_report"]) == 92
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
    assert len(seen) == 41


def test_deepest_allowed_chain_is_read(tmp_path):
    doc = hp.written_doc()
    doc["projects"][EMPTY]["nodes"] = chain(64)
    assert isinstance(hp.read_doc(tmp_path, doc), PortfolioInput)


SWEEP_VALUES = (None, 1, "x", [], {}, True)
# Every field the reader uses outside the actuals, and each container on the way to it
# (test_actuals_shape_refusals has the actuals' own fields, #213).
SWEEP_TARGETS = (
    ("schema",), ("schema_version",), ("generated_at",), ("projects",), ("parse_report",),
    ("projects", IDS), ("projects", IDS, "project"), ("projects", IDS, "status"),
    ("projects", IDS, "status_reason"), ("projects", NOT_UTF8, "status_reason"), ("projects", IDS, "nodes"),
    NODE, NODE + ("id",), NODE + ("parent",), NODE + ("text",), NODE + ("glyph",), NODE + ("state",),
    NODE + ("source_line",), NODE + ("issue",), NODE + ("meta",), NODE + ("meta", "issue"),
    NODE + ("meta", "priority"), NODE + ("models",), NODE + ("models", "thinking"), NODE + ("models", "checking"),
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
    assert read + refused == len(SWEEP_TARGETS) * len(SWEEP_VALUES) == 180
    # Most are refused; the 23 read are legal values (a null parent, any message text, an empty list, ...).
    assert (read, refused) == (23, 157)


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
    # The 19 read lack a field the reader does not use (a project's wbs_title, rollup or actuals, a node's typed
    # and derived fields), the top-level actuals, without which the document reads as not wired, a project's
    # issues, which reads as no slots (#213), or a node's models.
    assert (read, refused) == (19, 25)


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
    assert got.schema_version == "1.3"
    doc = hp.written_doc()
    doc["generated_at"] = "2026-01-12T09:30:00.250+00:00"
    later = map_document(doc).generated_at
    assert later == datetime(2026, 1, 12, 9, 30, 0, 250000, tzinfo=timezone.utc)
    assert later.utcoffset() == timedelta(0)


def test_module_docstring_names_its_first_callers():
    # The build (#12) and the test builder (#84) bind the signatures: no longer provisional.
    text = pj.__doc__
    assert "#12" in text and "#84" in text and "provisional" not in text
    assert "read_portfolio(roots, path)" in text and "gantry/build.py" in text


def test_actuals_part_names_its_first_caller():
    # The actuals part is not provisional: #25 is its first caller, in its docstring and the doc's reader section.
    assert "#25" in ActualsInput.__doc__ and "provisional" not in ActualsInput.__doc__.lower()
    text = (hp.sp.ROOT / "docs" / "worksite-page.md").read_text(encoding="utf-8")
    start = text.index("\n## Reading devagent's portfolio JSON\n")
    reader = text[start:text.index("\n## ", start + 1)]
    assert "#25" in reader and "provisional" not in reader.lower()


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
    assert names(PortfolioInput) == ["projects", "devagent_report", "generated_at", "schema_version", "actuals"]
    assert names(ActualsInput) == ["wired", "generated_at", "issues", "report"]
    assert names(IssueActuals) == ["hosts", "operator_s", "agent_wall_s", "tokens", "events"]
    assert names(EventRow) == ["at", "event"]
    for cls in (DevagentEntry, ProjectInput, PortfolioInput, ActualsInput, IssueActuals, EventRow):
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


META_COUNTS = (154, 221, 45, 25)  # nodes, scalar values, list values, leaf priorities


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
    assert unreadable.data == SiteData((), (), (), (), (ReportEntry("not_utf8", None, "site_unreadable", reason),),
                                       schema_models=True)
    empty = project(got, "empty")
    assert (empty.status, empty.reason) == ("read", None)
    assert empty.data == SiteData((), (), (), (), (ReportEntry("empty", None, "no_nodes", "WBS.md has no node lines"),),
                                  schema_models=True)
    for status in ("no_wbs", "no_devdoc_dir"):
        doc = hp.written_doc()
        doc["projects"][EMPTY].update(status=status, status_reason=f"reason for {status}")
        other = project(map_document(doc), "empty")
        assert (other.status, other.reason) == (status, f"reason for {status}")
        assert other.data == SiteData((), (), (), (), (), schema_models=True)
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
    assert len(read.projects) == 17 and sum(len(p.data.leaves) + len(p.data.rows) for p in read.projects) == 154


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
    assert pj.CONSUMED == ("actuals_unavailable",)
    assert not set(pj.CONSUMED) & (set(pj.MAPPED) | set(pj.SUPPRESSED_ALWAYS) | set(pj.SUPPRESSED_BY_RULE))
    assert pj.CYCLE_MEMBER_RE.pattern == (r"(?:^|, |: )(?:(?P<project>[A-Za-z0-9][A-Za-z0-9_.-]*):)?(?P<key>[^ ,()]+) "
                                          r"\(line (?P<line>[0-9]{1,9})\)")
    assert pj.WARNED_KEY_RE.pattern == (
        r"(?:line [0-9]{1,9}: )?(priority|depends_on|satisfies|objective|h|thinking|checking) ")
    members = [(m.group("project"), m.group("key"), m.group("line")) for m in pj.CYCLE_MEMBER_RE.finditer(
        "dependency cycle: Issue-1 (line 2), beta:Issue-1 (line 4), 1.3 (line 12)")]
    assert members == [(None, "Issue-1", "2"), ("beta", "Issue-1", "4"), (None, "1.3", "12")]


def test_every_entry_has_one_disposition():
    doc = hp.written_doc()
    got = map_document(doc)
    names = [p["project"] for p in doc["projects"]]
    later = ("edges", "fires", "models", "models_project")  # #83's own source, #12's, and #14's two
    earlier = [n for n in names if n not in later]
    # devagent_report is a sub-sequence of parse_report, unchanged and in document order.
    written = [as_shown(raw) for raw in doc["parse_report"]]
    assert list(got.devagent_report) == [e for e in written if e in set(got.devagent_report)]

    def partition(projects):
        total = sum(raw["project"] in projects for raw in doc["parse_report"])
        shown = sum(e.project in projects for e in got.devagent_report)
        mapped = sum(mapped_counts(got, projects).values())
        consumed = sum(e.project in projects for e in got.actuals.report)
        return total, mapped, total - mapped - shown - consumed, shown, consumed

    # One actuals_unavailable entry per project, with no line, each consumed (#213): the fixture aggregator
    # runs with no actuals.json in its throwaway home.
    assert [(e.project, e.line, e.kind) for e in got.actuals.report] == [
        (name, None, "actuals_unavailable") for name in names]
    assert len(names) == 17
    # Before `edges`: 59 = 13 mapped + 28 suppressed + 5 shown + 13 consumed.
    assert partition(earlier) == (59, 13, 28, 5, 13)
    assert mapped_counts(got, earlier) == {"no_issue": 6, "duplicate_issue": 1, "skipped_line": 2, "unresolved_ref": 4}
    assert [(e.project, e.line, e.kind) for e in got.devagent_report if e.project not in later] == [
        ("cycle", 7, "dep_cycle"), ("fences", 17, "parser_warning"), ("nesting", 10, "unknown_meta_key"),
        ("rows", 9, "dep_cycle"), ("rows", 13, "parent_done_leaf_pending")]
    # With `edges`: four near misses and two unresolved refs mapped; two est_unparseable, an
    # issue_malformed and a meta_unreadable shown.
    assert partition(names) == (92, 31, 28, 16, 17)
    assert partition(["edges"]) == (11, 6, 0, 4, 1)
    # With `fires` (#12): five issue-less leaves and seven near misses mapped; two meta_unreadable and an
    # unknown_meta_key shown (the kinds the fires do not read yet).
    assert partition(["fires"]) == (16, 12, 0, 3, 1)
    assert [(e.line, e.kind) for e in got.devagent_report if e.project == "fires"] == [
        (12, "meta_unreadable"), (13, "unknown_meta_key"), (16, "meta_unreadable")]
    # With `models` and `models_project` (#14): two bad_model entries each, shown.
    for name in ("models", "models_project"):
        assert partition([name]) == (3, 0, 0, 2, 1)
        assert [(e.line, e.kind) for e in got.devagent_report if e.project == name] == [
            (4, "bad_model"), (5, "bad_model")]
    # What is suppressed, by devagent's kind: each is an entry Gantry's own passes report.
    gone = {}
    kept = set(got.devagent_report)
    mapped_from = {"leaf_no_issue", "duplicate_issue", "near_miss_line"}
    for raw in doc["parse_report"]:
        if as_shown(raw) not in kept and raw["kind"] not in mapped_from and raw["kind"] not in pj.CONSUMED:
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
    assert project(got, "empty").data == SiteData((), (), (), (), (), schema_models=True)


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
    # A wbs model value and a bad_model message (#14), each returned as written.
    for text in HOSTILE:
        doc = hp.set_models(hp.written_doc(), "models", 2, thinking=(text, "wbs"), checking=None)
        doc["parse_report"].append(entry("models", 6, "bad_model", text))
        for got in (map_document(doc), hp.read_doc(tmp_path, doc)):
            assert models_of(got, "models")["Both"] == Models(text, None, "wbs", ("wbs", None))
            assert shown_at(got, "models", 6) == [("bad_model", text)]


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
                       ("PRIORITY_WARNING", "NAMESPACED_WARNING", "H_WARNING", "MODEL_WARNING"))
    assert sorted(texts) == ["H_WARNING", "MODEL_WARNING", "NAMESPACED_WARNING", "PRIORITY_WARNING"]
    cases = (
        (texts["PRIORITY_WARNING"].format("P5"), "priority"),
        (texts["NAMESPACED_WARNING"].format("satisfies", "x"), "satisfies"),
        (texts["NAMESPACED_WARNING"].format("objective", "x"), "objective"),
        (texts["H_WARNING"].format("high"), "h"),
        (texts["MODEL_WARNING"].format("thinking", "gpt", MODEL_TOKENS), "thinking"),
        (texts["MODEL_WARNING"].format("checking", "gpt", MODEL_TOKENS), "checking"),
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


# --- actuals: consumed entries and the generation time (#213) --------------------------------

def test_actuals_entries_are_consumed(tmp_path):
    path = str(tmp_path / "elsewhere" / "actuals.json")  # absolute, and under no root
    missing = hp.ACTUALS_MISSING.format(path=path)
    assert missing.startswith(path) and missing.endswith(" does not exist; actuals are empty; run /devagent:actuals")
    unknown = entry("ids", None, "kind_from_a_later_release", "something new about this project")
    # About a read project, one that is not read and one the document lacks; with no line and with one.
    planted = [
        entry("ids", None, "actuals_unavailable", missing),
        unknown,
        entry("not_utf8", None, "actuals_unavailable", missing),
        entry("gone", None, "actuals_unavailable", hp.ACTUALS_STALE.format(path=path)),
        entry("ids", 3, "actuals_unavailable", missing),
        entry("ids", None, "actuals_unavailable", "reworded by a later release"),
    ]
    consumed = [as_shown(raw) for raw in planted if raw is not unknown]
    assert [(e.project, e.line) for e in consumed] == [
        ("ids", None), ("not_utf8", None), ("gone", None), ("ids", 3), ("ids", None)]
    plain = mapped()
    for wired in (False, True):
        doc = hp.written_doc()
        doc.pop("actuals", None)
        if wired:
            doc = hp.with_actuals(doc, {})
        own = [as_shown(raw) for raw in doc["parse_report"] if raw["kind"] == "actuals_unavailable"]
        before = map_document(doc)
        assert list(before.actuals.report) == own
        doc["parse_report"] += [dict(raw) for raw in planted]
        for got in (map_document(doc), hp.read_doc(tmp_path, doc)):
            assert got.actuals.wired is wired
            # In document order, as devagent wrote each.
            assert list(got.actuals.report) == own + consumed
            # In neither the shown entries nor any site's report; the unknown kind beside them is shown.
            assert list(got.devagent_report) == list(before.devagent_report) + [as_shown(unknown)]
            assert not any(e.kind == "actuals_unavailable" for e in got.devagent_report)
            assert [p.data for p in got.projects] == [p.data for p in before.projects]
            assert all(e.kind in REPORT_KINDS for p in got.projects for e in p.data.report)
            assert path not in repr([got.projects, got.devagent_report])
        assert [p.data for p in before.projects] == [p.data for p in plain.projects]
    # With no such entry the part's report is empty, wired or not.
    doc = hp.written_doc()
    doc["parse_report"] = [raw for raw in doc["parse_report"] if raw["kind"] != "actuals_unavailable"]
    assert map_document(doc).actuals.report == ()


def test_actuals_generated_at(tmp_path):
    # The top-level `actuals` names the actuals file devagent read: its time, aware and UTC.
    doc = hp.with_actuals(hp.written_doc(), {}, generated_at="2026-01-10T23:59:07Z")
    for got in (map_document(doc), hp.read_doc(tmp_path, doc)):
        assert got.actuals.wired is True
        assert got.actuals.generated_at == datetime(2026, 1, 10, 23, 59, 7, tzinfo=timezone.utc)
        assert got.actuals.generated_at.utcoffset() == timedelta(0)
        # The document's own time is another field.
        assert got.generated_at == datetime(2026, 1, 12, 9, 30, tzinfo=timezone.utc)
    # Null (no usable file): wired, with no time.
    null = map_document(hp.with_actuals(hp.written_doc(), {}, generated_at=None))
    assert (null.actuals.wired, null.actuals.generated_at) == (True, None)
    # A null top-level object beside a filled slot still reads the slot.
    beside = map_document(hp.with_actuals(hp.written_doc(), {("ids", "Issue-3"): hp.slot()}, generated_at=None))
    assert (beside.actuals.wired, beside.actuals.generated_at) == (True, None)
    assert list(beside.actuals.issues) == [("ids", "Issue-3")]
    assert beside.actuals.issues[("ids", "Issue-3")].hosts == ("h1",)
    # No key: not wired.
    doc = hp.written_doc()
    doc.pop("actuals", None)
    bare = map_document(doc)
    assert (bare.actuals.wired, bare.actuals.generated_at) == (False, None)
    # A top-level object of another shape is refused, never read as null: ACTUALS_CASES' top-level rows.


# --- actuals: the per-issue slots (#213) -----------------------------------------------------

# The slot the shape cases break: written.json's project `ids`, issue Issue-3.
SLOT_ISSUE = ("projects", IDS, "issues", 3)
SLOT = SLOT_ISSUE + ("actuals",)
SLOT_AT = "$.projects[3].issues[3].actuals"
NOT_AMOUNT = "not a number from 0 to 1e18"


@functools.cache
def _wired():
    doc = hp.with_actuals(hp.written_doc(), {("ids", "Issue-3"): hp.slot(), ("ids", "Issue-4"): hp.slot()})
    assert _parent(doc, SLOT + (None,))["sessions"] and doc["projects"][IDS]["issues"][3]["issue"] == "Issue-3"
    assert isinstance(map_document(doc), PortfolioInput)  # it reads before a case breaks it
    return doc


def wired_doc():
    """written.json wired, with a filled slot at SLOT (ids, Issue-3) and one after it (ids, Issue-4):
    a fresh copy on every call, so a caller may change it."""
    return copy.deepcopy(_wired())


def test_actuals_slots_read(tmp_path):
    filled = hp.slot(
        sessions=(("h1", "-planted-cwd-one", "planted-session-one"), ("h2", "-planted-cwd-two", "planted-session-two"),
                  ("h1", "-planted-cwd-three", "planted-session-three")),
        time=(610.0, 4980.5), tokens=2029120.0,
        events=(("2026-11-01T14:02:11Z", "opened", "planted-source-one"),
                ("2026-11-02 09:15", "step_done", "planted-source-two")))
    filled["tokens"]["raw"][0]["model"] = "planted-model"
    # The bounds are kept: 0 and 1e18, an int as an int. An empty event list is not null.
    edges = hp.slot(sessions=(), time=(0, 1e18), tokens=10**18, events=())
    doc = hp.with_actuals(hp.written_doc(), {("ids", "Issue-3"): filled, ("ids", "Issue-4"): hp.EMPTY_SLOT,
                                ("rows", "Issue-2"): edges})
    ids = doc["projects"][IDS]
    assert [item["issue"] for item in ids["issues"]] == ["Issue-Fork-1", "Issue-Fork-2", "1.3", "Issue-3", "Issue-4"]
    # Issue-Fork-1 has no `actuals` key, Issue-Fork-2 and 1.3 a null slot.
    del ids["issues"][0]["actuals"]
    assert [item.get("actuals", "no key") for item in ids["issues"][:3]] == ["no key", None, None]
    # What the reader never keeps, planted: a project's overhead and the unassigned rows.
    ids["actuals"] = {"overhead": {"rules": {"cwd": 1, "hint": 0}, "tokens": None, "sessions": [
        {"cwd_slug": "-planted-overhead-cwd", "host": "planted-overhead-host", "session": "planted-overhead-session"}]}}
    doc["actuals"]["unassigned"] = [{"cwd": "planted-unassigned-cwd", "sessions": [], "tokens": None}]
    for got in (map_document(doc), hp.read_doc(tmp_path, doc)):
        assert got.actuals.wired is True
        # Exactly the slots that are not null, in document order; one host per distinct host, first seen first.
        assert list(got.actuals.issues.items()) == [
            (("ids", "Issue-3"), IssueActuals(("h1", "h2"), 610.0, 4980.5, 2029120.0, (
                EventRow("2026-11-01T14:02:11Z", "opened"), EventRow("2026-11-02 09:15", "step_done")))),
            (("ids", "Issue-4"), IssueActuals((), None, None, None, None)),
            (("rows", "Issue-2"), IssueActuals((), 0, 1e18, 10**18, ())),
        ]
        kept = got.actuals.issues[("rows", "Issue-2")]
        assert (type(kept.operator_s), type(kept.agent_wall_s), type(kept.tokens)) == (int, float, int)
        assert kept.events == () and got.actuals.issues[("ids", "Issue-4")].events is None
        # No session's cwd_slug or id, no event's source, no raw token row, no overhead and no unassigned row.
        assert "planted" not in repr(got)
        with pytest.raises(TypeError):
            got.actuals.issues[("ids", "Issue-9")] = kept
    # Reading the slots changes nothing else: the same document with every slot null.
    for project in doc["projects"]:
        for item in project["issues"]:
            item["actuals"] = None
    bare = map_document(doc)
    assert dict(bare.actuals.issues) == {} and dataclasses.replace(got, actuals=bare.actuals) == bare


def test_absent_issues_is_no_slots():
    # A wired document whose project has no `issues` key reads as no slots there, not as a refusal.
    doc = wired_doc()
    del doc["projects"][IDS]["issues"]
    got = map_document(doc)
    assert got.actuals.wired is True and dict(got.actuals.issues) == {}
    # And an empty list likewise.
    doc["projects"][IDS]["issues"] = []
    assert dict(map_document(doc).actuals.issues) == {}
    # A slot of a project that was not read is kept: the key is the project and the issue as written.
    doc = wired_doc()
    doc["projects"][NOT_UTF8]["issues"] = [{"issue": "Issue-1", "actuals": hp.EMPTY_SLOT}]
    assert list(map_document(doc).actuals.issues) == [("ids", "Issue-3"), ("ids", "Issue-4"), ("not_utf8", "Issue-1")]
    # An issue id is checked only where its slot is kept: beside a null slot it is not looked at.
    doc = wired_doc()
    doc["projects"][IDS]["issues"][0].update(issue=7)
    doc["projects"][IDS]["issues"][1].update(issue="Issue-3")
    assert list(map_document(doc).actuals.issues) == [("ids", "Issue-3"), ("ids", "Issue-4")]


def test_unwired_documents_read_as_today(tmp_path):
    for minor in ("1.0", "1.1", "1.2", "1.3"):
        doc = hp.written_doc()
        doc.pop("actuals", None)
        doc["schema_version"] = minor
        got = hp.read_doc(tmp_path, doc)
        assert (got.actuals.wired, got.actuals.generated_at, dict(got.actuals.issues)) == (False, None, {}), minor
        assert got.schema_version == minor
        # Everything else is as the same document reads with the key and null slots.
        wired = map_document(hp.with_actuals(doc, {}))
        assert wired.actuals.wired is True
        assert (got.projects, got.devagent_report, got.generated_at) == (
            wired.projects, wired.devagent_report, wired.generated_at), minor
        assert dataclasses.replace(wired, actuals=got.actuals) == got
        # In an unwired document nothing under projects[].issues is looked at: garbage there reads the same.
        for spoil in ("garbage", [hp.slot()], None):
            spoiled = copy.deepcopy(doc)
            for project in spoiled["projects"]:
                for item in project["issues"]:
                    item["actuals"] = spoil
            assert map_document(spoiled) == got, (minor, spoil)
        spoiled = copy.deepcopy(doc)
        for project in spoiled["projects"]:
            project["issues"] = "garbage"
        assert map_document(spoiled) == got, minor
    # The older minors differ from the reader's own only in the model tiers (#14).
    assert got.schema_version == "1.3" and got.projects == mapped().projects


def _twice(doc):
    doc["projects"][IDS]["issues"][4]["issue"] = "Issue-3"


# (what breaks, the mutation, where, the problem, the number as JSON text or None): one row per place the
# reader checks in a wired document, and each kind of bad number at least once.
ACTUALS_CASES = (
    ("top level is a string", _set(("actuals",), "2026-01-10T23:59:07Z"), "$.actuals", "not an object or null", None),
    ("top level is a list", _set(("actuals",), []), "$.actuals", "not an object or null", None),
    ("no generation time", _drop(("actuals", "generated_at")), "$.actuals.generated_at", "not YYYY-MM-DDTHH:MM:SSZ",
     None),
    ("generation time with a space", _set(("actuals", "generated_at"), "2026-01-10 23:59:07Z"),
     "$.actuals.generated_at", "not YYYY-MM-DDTHH:MM:SSZ", None),
    ("generation time with an offset", _set(("actuals", "generated_at"), "2026-01-10T23:59:07+00:00"),
     "$.actuals.generated_at", "not YYYY-MM-DDTHH:MM:SSZ", None),
    ("generation time on no real day", _set(("actuals", "generated_at"), "2026-02-30T00:00:00Z"),
     "$.actuals.generated_at", "not YYYY-MM-DDTHH:MM:SSZ", None),
    ("generation time is a number", _set(("actuals", "generated_at"), 1768089547), "$.actuals.generated_at",
     "not YYYY-MM-DDTHH:MM:SSZ", None),
    ("issues is an object", _set(("projects", IDS, "issues"), {}), "$.projects[3].issues", "not a list", None),
    ("issues is null", _set(("projects", IDS, "issues"), None), "$.projects[3].issues", "not a list", None),
    ("an issue entry is a string", _set(SLOT_ISSUE, "Issue-3"), "$.projects[3].issues[3]", "not an object", None),
    ("a slot is a string", _set(SLOT, "garbage"), SLOT_AT, "not an object or null", None),
    ("a slot is a list", _set(SLOT, [hp.slot()]), SLOT_AT, "not an object or null", None),
    ("a kept slot's issue is empty", _set(SLOT_ISSUE + ("issue",), ""), "$.projects[3].issues[3].issue",
     "not a non-empty string", None),
    ("a kept slot's issue is a number", _set(SLOT_ISSUE + ("issue",), 3), "$.projects[3].issues[3].issue",
     "not a non-empty string", None),
    ("a kept slot has no issue", _drop(SLOT_ISSUE + ("issue",)), "$.projects[3].issues[3].issue",
     "not a non-empty string", None),
    ("two kept slots share an issue", _twice, "$.projects[3].issues[4].issue", "used twice in this project", None),
    ("sessions is null", _set(SLOT + ("sessions",), None), f"{SLOT_AT}.sessions", "not a list", None),
    ("no sessions", _drop(SLOT + ("sessions",)), f"{SLOT_AT}.sessions", "not a list", None),
    ("a session is a string", _set(SLOT + ("sessions", 0), "h1"), f"{SLOT_AT}.sessions[0]", "not an object", None),
    ("a host is a number", _set(SLOT + ("sessions", 0, "host"), 7), f"{SLOT_AT}.sessions[0].host", "not a string",
     None),
    ("a session has no host", _drop(SLOT + ("sessions", 0, "host")), f"{SLOT_AT}.sessions[0].host", "not a string",
     None),
    ("time is a list", _set(SLOT + ("time",), []), f"{SLOT_AT}.time", "not an object or null", None),
    ("no time", _drop(SLOT + ("time",)), f"{SLOT_AT}.time", "not an object or null", None),
    ("tokens is a number", _set(SLOT + ("tokens",), 7), f"{SLOT_AT}.tokens", "not an object or null", None),
    ("no tokens", _drop(SLOT + ("tokens",)), f"{SLOT_AT}.tokens", "not an object or null", None),
    ("operator time is true", _set(SLOT + ("time", "operator_s"), True), f"{SLOT_AT}.time.operator_s", NOT_AMOUNT,
     None),
    ("operator time is negative", _set(SLOT + ("time", "operator_s"), -1), f"{SLOT_AT}.time.operator_s", NOT_AMOUNT,
     None),
    ("operator time is 1e999", _set(SLOT + ("time", "operator_s"), float("inf")), f"{SLOT_AT}.time.operator_s",
     NOT_AMOUNT, "1e999"),
    ("operator time is over 1e18", _set(SLOT + ("time", "operator_s"), 1.0000001e18), f"{SLOT_AT}.time.operator_s",
     NOT_AMOUNT, None),
    ("operator time is 400 digits long", _set(SLOT + ("time", "operator_s"), int("9" * 400)),
     f"{SLOT_AT}.time.operator_s", NOT_AMOUNT, "9" * 400),
    ("operator time is a string", _set(SLOT + ("time", "operator_s"), "610"), f"{SLOT_AT}.time.operator_s", NOT_AMOUNT,
     None),
    ("no operator time", _drop(SLOT + ("time", "operator_s")), f"{SLOT_AT}.time.operator_s", NOT_AMOUNT, None),
    ("agent time is false", _set(SLOT + ("time", "agent_wall_s"), False), f"{SLOT_AT}.time.agent_wall_s", NOT_AMOUNT,
     None),
    ("agent time is below zero", _set(SLOT + ("time", "agent_wall_s"), -0.5), f"{SLOT_AT}.time.agent_wall_s",
     NOT_AMOUNT, None),
    ("agent time is -1e999", _set(SLOT + ("time", "agent_wall_s"), float("-inf")), f"{SLOT_AT}.time.agent_wall_s",
     NOT_AMOUNT, "-1e999"),
    ("agent time is null", _set(SLOT + ("time", "agent_wall_s"), None), f"{SLOT_AT}.time.agent_wall_s", NOT_AMOUNT,
     None),
    ("tokens are 1e999", _set(SLOT + ("tokens", "total"), float("inf")), f"{SLOT_AT}.tokens.total", NOT_AMOUNT,
     "1e999"),
    ("tokens are one over 10**18", _set(SLOT + ("tokens", "total"), 10**18 + 1), f"{SLOT_AT}.tokens.total", NOT_AMOUNT,
     None),
    ("tokens are 400 digits long", _set(SLOT + ("tokens", "total"), int("1" + "0" * 399)), f"{SLOT_AT}.tokens.total",
     NOT_AMOUNT, "1" + "0" * 399),
    ("tokens are a list", _set(SLOT + ("tokens", "total"), [7]), f"{SLOT_AT}.tokens.total", NOT_AMOUNT, None),
    ("no token total", _drop(SLOT + ("tokens", "total")), f"{SLOT_AT}.tokens.total", NOT_AMOUNT, None),
    ("milestones is an object", _set(SLOT + ("milestones",), {}), f"{SLOT_AT}.milestones", "not a list or null", None),
    ("no milestones", _drop(SLOT + ("milestones",)), f"{SLOT_AT}.milestones", "not a list or null", None),
    ("an event is a string", _set(SLOT + ("milestones", 1), "opened"), f"{SLOT_AT}.milestones[1]", "not an object",
     None),
    ("an event's time is null", _set(SLOT + ("milestones", 0, "at"), None), f"{SLOT_AT}.milestones[0].at",
     "not a string", None),
    ("an event has no time", _drop(SLOT + ("milestones", 0, "at")), f"{SLOT_AT}.milestones[0].at", "not a string",
     None),
    ("an event's word is a number", _set(SLOT + ("milestones", 2, "event"), 3), f"{SLOT_AT}.milestones[2].event",
     "not a string", None),
    ("an event has no word", _drop(SLOT + ("milestones", 2, "event")), f"{SLOT_AT}.milestones[2].event", "not a string",
     None),
)
NUMBER_MARK = "the-number-goes-here"


def test_actuals_shape_cases_are_counted():
    assert len(ACTUALS_CASES) == 48
    # Each (place, problem) of the reader's actuals checks at least once, array indexes aside: the top level
    # and its time, issues, an entry, a slot, the two issue problems, sessions, a session, its host, time,
    # tokens, the three numbers, milestones, an event, its time and its word.
    places = {(re.sub(r"\[[0-9]+\]", "[]", where), problem) for _, _, where, problem, _ in ACTUALS_CASES}
    assert len(places) == 19
    # Each kept number, and each kind of bad number: a bool, a negative, 1e999, over 1e18, 400 digits, a string.
    assert {where.rsplit(".", 1)[1] for _, _, where, problem, _ in ACTUALS_CASES if problem == NOT_AMOUNT} == {
        "operator_s", "agent_wall_s", "total"}
    assert {text for *_, text in ACTUALS_CASES if text is not None} == {"1e999", "-1e999", "9" * 400, "1" + "0" * 399}


@pytest.mark.parametrize("case", ACTUALS_CASES, ids=[case[0] for case in ACTUALS_CASES])
def test_actuals_shape_refusals(tmp_path, case):
    what, change, where, problem, text = case
    doc = wired_doc()
    changed = change(doc)
    assert changed is None
    # Through map_document, on the parsed document.
    with pytest.raises(PortfolioLoadError) as info:
        map_document(doc)
    assert (info.value.kind, str(info.value)) == ("schema", schema_text("the portfolio document", where, problem))
    # Through read_portfolio, on the file's bytes: a number JSON cannot hold as Python wrote it goes in as text.
    if text is None:
        data = hp.sp.dumps(doc)
    else:
        marked = wired_doc()
        _set(tuple(SLOT + tuple(where.removeprefix(SLOT_AT + ".").split("."))), NUMBER_MARK)(marked)
        data = hp.sp.dumps(marked)
        assert data.count(f'"{NUMBER_MARK}"'.encode()) == 1
        data = data.replace(f'"{NUMBER_MARK}"'.encode(), text.encode())
    error = load_error(tmp_path, data)
    assert (error.kind, str(error)) == ("schema", schema_text(doc_path(tmp_path), where, problem))


def test_no_number_raises_anything_but_a_load_error():
    # Whatever the number: not a number at all, the infinities, a float past every bound and a long integer.
    for field in (("time", "operator_s"), ("time", "agent_wall_s"), ("tokens", "total")):
        for value in (float("nan"), float("inf"), float("-inf"), 1.7976931348623157e308, -1e-300, 10**4000,
                      -(10**4000), True, False, None, "1", [1], {"n": 1}):
            doc = wired_doc()
            _set(SLOT + field, value)(doc)
            with pytest.raises(PortfolioLoadError) as info:
                map_document(doc)
            assert info.value.kind == "schema", (field, value)
            assert str(info.value).endswith(f".{field[1]}: {NOT_AMOUNT}"), (field, value)
