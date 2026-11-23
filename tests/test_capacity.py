"""devagent's declared capacity (#39): the reader and its rules.

gantry/capacity.py ports the rules of devagent's shipped reader
(scripts/lib/capacity.py at the release tests/devagent-latest.pin names).
Every config fixture is a fictional devagent config declaring projects a, b
and c; fixtures/capacity/expected.json holds each one's violation and warning
lines, written by hand: no fixture stores devagent output.

The agreement tests run devagent's own scripts/capacity.sh, from a checkout at
that release (GANTRY_DEVAGENT_LATEST_DIR, tests/helpers_devagent.py), on every
config fixture and compare it with the port: they skip on Windows and locally
without the variable, and fail under CI without it.
"""

import ast
import dataclasses
import inspect
import json
import re
import sys
import tomllib
from typing import NamedTuple

import pytest

import devagent_record_fixtures as fx
import helpers_devagent as hd
from gantry import capacity as cap
from gantry import sites
from gantry.config import ConfigError
from gantry.roots import directory_roots
from page_safety import HOSTILE
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "capacity"
CONFIGS = FIXTURES / "config"
EXPECTED = json.loads((FIXTURES / "expected.json").read_bytes())

VALID = (
    "full", "binding_tie", "not_declared", "unknown_key", "dotted", "ratio", "ratio_zero",
    "ratio_above_max", "paused", "per_project_keys", "capacity_only",
)
RULE_FIXTURES = sorted(name for name in EXPECTED if name.startswith("rule_"))
INVALID = (*RULE_FIXTURES, "order", "hostile")

# The exact strings are the decision (#39): devagent's rule strings, each the
# end of a violation line "capacity: <where>: <rule>".
RULES = (
    "missing required key",
    "wrong type: expected number",
    "wrong type: expected integer",
    "wrong type: expected boolean",
    "wrong type: expected date",
    "wrong type: expected table",
    "out of range: 0 < x <= 168",
    "out of range: 1 <= x <= 9007199254740991",
    "out of range: 0 < x <= 9007199254740991",
    "out of range: 0 < x <= 33.6",
    "out of range: 0 <= x <= 9007199254740991",
    "not a calendar date",
    "exceeds capacity.max_lanes",
    "[capacity] table missing",
    "reserve_operator_hours_per_week sum exceeds capacity.operator_hours_per_week",
    "reserve_tokens_per_week sum exceeds the effective weekly token budget",
)
HOURS_POOL = "capacity: pool: reserve_operator_hours_per_week sum exceeds capacity.operator_hours_per_week"
TOKENS_POOL = "capacity: pool: reserve_tokens_per_week sum exceeds the effective weekly token budget"
SHARED_KEYS = (
    "operator_hours_per_week", "lane_hours_per_week", "max_lanes", "tokens_per_week",
    "tokens_per_window", "windows_per_week", "operator_hours_per_agent_hour",
)
PROJECT_KEYS = (
    "priority", "max_lanes", "paused", "deadline", "reserve_operator_hours_per_week",
    "reserve_tokens_per_week",
)
WINDOW_PRODUCT = "tokens_per_window_x_windows_per_week"
LF, CR = chr(10), chr(13)

# The six required keys as raw TOML literals: devagent's documented example.
REQUIRED = {
    "operator_hours_per_week": "20",
    "lane_hours_per_week": "40",
    "max_lanes": "3",
    "tokens_per_week": "500000000",
    "tokens_per_window": "60000000",
    "windows_per_week": "10",
}


def shared(**raw):
    """A [capacity] table from raw TOML literals: the six required keys, each
    replaced or (None) dropped by `raw`; a key `raw` adds comes last."""
    values = {**REQUIRED, **raw}
    return "[capacity]\n" + "".join(f"{key} = {value}\n" for key, value in values.items() if value is not None)


def project(name, *lines):
    """[project.<name>] holding devdoc_dir, then its capacity table of `lines`, if any."""
    text = f'[project.{name}]\ndevdoc_dir = "{name}"\n'
    if lines:
        text += f"[project.{name}.capacity]\n" + "".join(line + "\n" for line in lines)
    return text


def resolve(*parts):
    return cap.resolve_capacity(tomllib.loads("".join(parts)))


def read(name):
    return cap.read_declared_capacity(directory_roots([CONFIGS]), CONFIGS / f"{name}.toml")


def parsed(name):
    return tomllib.loads((CONFIGS / f"{name}.toml").read_bytes().decode("utf-8"))


def defaults(name):
    return cap.ProjectCapacity(
        name=name, priority=5, max_lanes=None, paused=False, deadline=None,
        reserve_operator_hours_per_week=None, reserve_tokens_per_week=None, set_keys=(),
    )


def assert_holds_no_value(declared):
    """With a violation nothing declared can be read: devagent prints no document."""
    assert declared.violations
    assert declared.usable is False
    assert [getattr(declared, key) for key in SHARED_KEYS] == [None] * 7
    assert declared.token_budget is None and declared.token_binding_term is None
    assert declared.projects == () and declared.paused == ()
    assert cap.document_text(declared) is None


# --- the fixtures ------------------------------------------------------------------


def test_expected_names_every_config_fixture():
    assert sorted(EXPECTED) == sorted(p.stem for p in CONFIGS.glob("*.toml"))
    assert sorted((*VALID, *INVALID)) == sorted(EXPECTED)
    for name, lines in EXPECTED.items():
        assert sorted(lines) == ["violations", "warnings"], name
        assert bool(lines["violations"]) == (name in INVALID), name


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_gives_its_expected_lines(name):
    declared = read(name)
    assert list(declared.violations) == EXPECTED[name]["violations"]
    assert list(declared.warnings) == EXPECTED[name]["warnings"]
    assert declared.usable is (name in VALID and name != "not_declared")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_declares_projects_a_b_and_c(name):
    # Each drops into a planner installation whose planning folders are a, b and c.
    projects = parsed(name)["project"]
    assert [projects[site]["devdoc_dir"] for site in "abc"] == ["a", "b", "c"]


# --- what a declaration holds ------------------------------------------------------


def test_text_constants():
    assert cap.RULES == RULES
    assert cap.WARNING == "capacity: warning: {where}: unknown key (ignored)"
    assert cap.CAPACITY_SCHEMA == "1.0"
    assert cap.TOKEN_UNIT == "weighted_input_token_equivalents"
    assert cap.SHARED_KEYS == SHARED_KEYS and cap.PROJECT_KEYS == PROJECT_KEYS


def test_full_reads_every_value():
    declared = read("full")
    assert declared.declared is True and declared.usable is True
    assert declared.violations == () and declared.warnings == ()
    assert (declared.capacity_schema, type(declared.capacity_schema)) == ("1.0", str)
    assert declared.token_unit == "weighted_input_token_equivalents"
    # Each value keeps its parsed type: 20 is not 20.0 in devagent's document.
    values = [getattr(declared, key) for key in SHARED_KEYS]
    assert [(type(value), value) for value in values] == [
        (int, 20), (float, 40.5), (int, 3), (int, 500000000), (int, 60000000), (float, 4.1), (float, 0.25),
    ]
    # 60000000 x 4.1 is 245999999.99999997 as a double: whole tokens, under tokens_per_week.
    assert (type(declared.token_budget), declared.token_budget) == (int, 246000000)
    assert declared.token_binding_term == WINDOW_PRODUCT
    a, b, c = declared.projects
    assert a == cap.ProjectCapacity(
        name="a", priority=2, max_lanes=2, paused=False, deadline="2026-12-31",
        reserve_operator_hours_per_week=4.5, reserve_tokens_per_week=100000000, set_keys=PROJECT_KEYS,
    )
    assert b == cap.ProjectCapacity(
        name="b", priority=7, max_lanes=1, paused=True, deadline="2027-03-01",
        reserve_operator_hours_per_week=2, reserve_tokens_per_week=50000000, set_keys=PROJECT_KEYS,
    )
    assert type(a.reserve_operator_hours_per_week) is float and type(b.reserve_operator_hours_per_week) is int
    assert c == defaults("c")
    assert declared.paused == ("b",)


def test_binding_tie_is_tokens_per_week():
    declared = read("binding_tie")
    assert declared.tokens_per_week == declared.tokens_per_window * declared.windows_per_week == 600000000
    assert (declared.token_budget, declared.token_binding_term) == (600000000, "tokens_per_week")


@pytest.mark.parametrize("week, window, windows, budget, binding", [
    ("500000000", "60000000", "5.5", 330000000, WINDOW_PRODUCT),
    # 220000 x 4.1 is 901999.9999999999 as a double: rounded, it ties.
    ("902000", "220000", "4.1", 902000, "tokens_per_week"),
    # A double of 2^52 or more is already whole: adding a half would round it up.
    ("9007199254740991", "4503599627370497", "1.0", 4503599627370497, WINDOW_PRODUCT),
    ("9007199254740991", "3", "7", 21, WINDOW_PRODUCT),
])
def test_token_budget_is_whole_tokens(week, window, windows, budget, binding):
    declared = resolve(shared(tokens_per_week=week, tokens_per_window=window, windows_per_week=windows))
    assert declared.violations == ()
    assert (type(declared.token_budget), declared.token_budget) == (int, budget)
    assert declared.token_binding_term == binding


def test_no_capacity_table_is_not_declared():
    declared = read("not_declared")
    assert declared.declared is False
    assert declared.violations == () and declared.warnings == ()
    assert declared.usable is False
    assert [getattr(declared, key) for key in SHARED_KEYS] == [None] * 7
    assert declared.token_budget is None and declared.token_binding_term is None
    assert declared.projects == (defaults("a"), defaults("b"), defaults("c"))
    assert declared.paused == ()
    # devagent's document carries both in its not-declared form too.
    assert (declared.capacity_schema, declared.token_unit) == ("1.0", "weighted_input_token_equivalents")
    empty = cap.resolve_capacity({})
    assert (empty.declared, empty.projects, empty.violations, empty.warnings) == (False, (), (), ())


def test_ratio_fixtures_read_the_optional_key():
    read_ratio = {name: read(name).operator_hours_per_agent_hour for name in ("ratio", "ratio_zero", "ratio_above_max")}
    assert read_ratio == {"ratio": 0.25, "ratio_zero": 0, "ratio_above_max": 30}
    assert read("paused").operator_hours_per_agent_hour is None


def test_paused_fixture_names_c():
    declared = read("paused")
    assert declared.paused == ("c",)
    assert [p.name for p in declared.projects if p.paused] == ["c"]
    # c's reservations are over both pools: it is valid only because c is paused.
    c = declared.projects[2]
    assert c.reserve_operator_hours_per_week > declared.operator_hours_per_week
    assert c.reserve_tokens_per_week > declared.token_budget


def test_per_project_keys_are_read():
    a, b, c = read("per_project_keys").projects
    keys = ("priority", "max_lanes", "deadline", "reserve_operator_hours_per_week", "reserve_tokens_per_week")
    assert a == cap.ProjectCapacity(
        name="a", priority=1, max_lanes=2, paused=False, deadline="2026-12-31",
        reserve_operator_hours_per_week=6, reserve_tokens_per_week=200000000, set_keys=keys,
    )
    assert b == cap.ProjectCapacity(
        name="b", priority=9, max_lanes=1, paused=False, deadline="2027-03-01",
        reserve_operator_hours_per_week=2.5, reserve_tokens_per_week=100000000, set_keys=keys,
    )
    # A priority written as its default is still the config's: set_keys says so.
    assert c == cap.ProjectCapacity(
        name="c", priority=5, max_lanes=None, paused=False, deadline=None,
        reserve_operator_hours_per_week=None, reserve_tokens_per_week=None, set_keys=("priority",),
    )


# --- the rules ---------------------------------------------------------------------


@pytest.mark.parametrize("name", RULE_FIXTURES)
def test_each_rule_fixture_gives_exactly_its_line(name):
    declared = read(name)
    (line,) = EXPECTED[name]["violations"]
    assert declared.violations == (line,)
    assert declared.warnings == ()
    assert_holds_no_value(declared)


def _rule_of(line):
    (rule,) = [rule for rule in RULES if line.endswith(": " + rule)]
    return rule


def test_rule_fixtures_cover_every_rule():
    assert len(RULE_FIXTURES) == 16
    rules = [_rule_of(EXPECTED[name]["violations"][0]) for name in RULE_FIXTURES]
    assert len(set(rules)) == 16
    assert set(rules) == set(cap.RULES)
    # Only the missing table leaves the declaration not declared.
    assert [name for name in RULE_FIXTURES if not read(name).declared] == ["rule_capacity_table_missing"]


def test_order_and_suppression():
    declared = read("order")
    assert list(declared.violations) == EXPECTED["order"]["violations"]
    assert list(declared.warnings) == EXPECTED["order"]["warnings"]
    assert len(declared.violations) == 9 and len(declared.warnings) == 2
    # One line per key: no <where> is named twice.
    wheres = [line[len("capacity: "):-len(": " + _rule_of(line))] for line in declared.violations]
    assert len(set(wheres)) == len(wheres)
    assert_holds_no_value(declared)


A_DEADLINE = "capacity: project.a.capacity.deadline: "
FULLWIDTH_DATE = "".join(chr(0xFF10 + int(c)) if c.isdigit() else c for c in "2026-12-31")
BIG = "9" * 400
TYPE_EDGES = {
    "a boolean for a number": (
        shared(operator_hours_per_week="true"),
        ["capacity: capacity.operator_hours_per_week: wrong type: expected number"]),
    "2.0 for an integer": (
        shared(max_lanes="2.0"),
        ["capacity: capacity.max_lanes: wrong type: expected integer"]),
    "a boolean for an integer": (
        shared(max_lanes="true"),
        ["capacity: capacity.max_lanes: wrong type: expected integer"]),
    "inf": (
        shared(operator_hours_per_agent_hour="inf"),
        ["capacity: capacity.operator_hours_per_agent_hour: out of range: 0 <= x <= 9007199254740991"]),
    "nan": (
        shared(windows_per_week="nan"),
        ["capacity: capacity.windows_per_week: out of range: 0 < x <= 33.6"]),
    "a 400-digit integer": (
        shared(tokens_per_window=BIG),
        ["capacity: capacity.tokens_per_window: out of range: 0 < x <= 9007199254740991"]),
    "a 400-digit integer for a number": (
        shared(operator_hours_per_week=BIG),
        ["capacity: capacity.operator_hours_per_week: out of range: 0 < x <= 168"]),
    "a date-time for deadline": (
        shared() + project("a", "deadline = 2026-12-31T00:00:00"),
        [A_DEADLINE + "wrong type: expected date"]),
    "a time for deadline": (
        shared() + project("a", "deadline = 07:32:00"),
        [A_DEADLINE + "wrong type: expected date"]),
    "a TOML date": (shared() + project("a", "deadline = 2026-12-31"), []),
    "a date string": (shared() + project("a", 'deadline = "2026-12-31"'), []),
    "30 February": (
        shared() + project("a", 'deadline = "2026-02-30"'),
        [A_DEADLINE + "not a calendar date"]),
    "non-ASCII digits": (
        shared() + project("a", f'deadline = "{FULLWIDTH_DATE}"'),
        [A_DEADLINE + "not a calendar date"]),
    # The project's own keys are still checked, but against no lane pool.
    "capacity = 5": (
        "capacity = 5\n" + project("a", "priority = 2", "max_lanes = 9"),
        ["capacity: capacity: wrong type: expected table"]),
    # a counts as unpaused with no reservations: the pool rules still run.
    "project.a.capacity = 5": (
        shared() + '[project.a]\ndevdoc_dir = "a"\ncapacity = 5\n'
        + project("b", "reserve_operator_hours_per_week = 25"),
        ["capacity: project.a.capacity: wrong type: expected table", HOURS_POOL]),
    "project = 5": ("project = 5\n" + shared(), []),
}


@pytest.mark.parametrize("edge", TYPE_EDGES)
def test_type_edges(edge):
    text, lines = TYPE_EDGES[edge]
    declared = resolve(text)
    assert list(declared.violations) == lines
    assert declared.warnings == ()
    assert declared.usable is (not lines)


def test_type_edge_values():
    # The premise of the non-ASCII leg: a \d pattern would take these digits.
    assert not FULLWIDTH_DATE.isascii() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", FULLWIDTH_DATE)
    for written in ("2026-12-31", '"2026-12-31"'):
        (a,) = resolve(shared() + project("a", f"deadline = {written}")).projects
        assert (a.deadline, a.set_keys) == ("2026-12-31", ("deadline",))
    assert resolve("project = 5\n" + shared()).projects == ()
    # A [project] key that is not a table is not a project.
    assert [p.name for p in resolve(shared() + "[project]\nx = 5\n" + project("a")).projects] == ["a"]


def reserving(**hours):
    """shared() and one project per name, in the order given, each reserving those operator hours."""
    return shared() + "".join(project(name, f"reserve_operator_hours_per_week = {h}") for name, h in hours.items())


POOL_CASES = {
    # 4.4 + 11.8 + 3.8 is 20.000000000000004 as doubles: inside the 1e-9 tolerance.
    "hours that fill the pool pass": (reserving(a="4.4", b="11.8", c="3.8"), []),
    "in the other order too": (reserving(c="3.8", b="11.8", a="4.4"), []),
    "hours past the tolerance": (reserving(a="10", b="10.000001"), [HOURS_POOL]),
    # The sum is a plain + from 0 in project order, never a compensated one:
    # 0.1 + 19.800000001 + 0.1 is 20.000000001000004 as doubles, past 20 + 1e-9,
    # where math.fsum and Python 3.12's sum() give 20.000000001, which is not.
    "the sum is + in project order": (reserving(a="0.1", b="19.800000001", c="0.1"), [HOURS_POOL]),
    # 0.1 + 0.1 + 19.800000001 is 20.000000001 as doubles.
    "so the same reservations pass in another order": (reserving(a="0.1", b="0.1", c="19.800000001"), []),
    "tokens that fill the rounded budget pass": (
        shared(windows_per_week="4.1") + project("a", "reserve_tokens_per_week = 246000000"),
        []),
    "one token over": (
        shared(windows_per_week="4.1") + project("a", "reserve_tokens_per_week = 246000001"),
        [TOKENS_POOL]),
    "a paused project leaves both sums": (
        shared()
        + project("a", "reserve_operator_hours_per_week = 15", "reserve_tokens_per_week = 300000000")
        + project("b", "reserve_operator_hours_per_week = 15", "reserve_tokens_per_week = 300000000",
                  "paused = true"),
        []),
    "paused = false stays in both": (
        shared()
        + project("a", "reserve_operator_hours_per_week = 15", "reserve_tokens_per_week = 300000000")
        + project("b", "reserve_operator_hours_per_week = 15", "reserve_tokens_per_week = 300000000",
                  "paused = false"),
        [HOURS_POOL, TOKENS_POOL]),
    "a paused project's cap is still checked": (
        shared() + project("a", "max_lanes = 4", "paused = true"),
        ["capacity: project.a.capacity.max_lanes: exceeds capacity.max_lanes"]),
    "an invalid paused skips both pool rules": (
        shared() + project("a", 'paused = "yes"')
        + project("b", "reserve_operator_hours_per_week = 25", "reserve_tokens_per_week = 600000000"),
        ["capacity: project.a.capacity.paused: wrong type: expected boolean"]),
    "an invalid token reservation skips only the token rule": (
        shared() + project("a", 'reserve_tokens_per_week = "100000000"')
        + project("b", "reserve_operator_hours_per_week = 25", "reserve_tokens_per_week = 600000000"),
        ["capacity: project.a.capacity.reserve_tokens_per_week: wrong type: expected integer", HOURS_POOL]),
    "no [capacity] table: every project table is named, and its keys are still checked": (
        project("a", "priority = 2") + project("b", "priority = 0"),
        ["capacity: project.a.capacity: [capacity] table missing",
         "capacity: project.b.capacity: [capacity] table missing",
         "capacity: project.b.capacity.priority: out of range: 1 <= x <= 9007199254740991"]),
}


@pytest.mark.parametrize("case", POOL_CASES)
def test_pool_sums_and_suppression_are_devagents(case):
    text, lines = POOL_CASES[case]
    assert list(resolve(text).violations) == lines


# --- warnings ----------------------------------------------------------------------


def test_unknown_key_warns_and_the_rest_is_used():
    declared = read("unknown_key")
    assert declared.warnings == (
        "capacity: warning: capacity.max_lane: unknown key (ignored)",
        "capacity: warning: project.a.capacity.priorty: unknown key (ignored)",
    )
    assert declared.violations == () and declared.usable is True
    assert declared.max_lanes == 3 and declared.token_budget == 500000000
    a = declared.projects[0]
    assert (a.priority, a.max_lanes, a.set_keys) == (5, 2, ("max_lanes",))
    # Warnings follow the violations and stay when there is one.
    both = resolve(shared(max_lanes=None, max_lane="3"))
    assert both.violations == ("capacity: capacity.max_lanes: missing required key",)
    assert both.warnings == ("capacity: warning: capacity.max_lane: unknown key (ignored)",)


def test_capacity_only_project_adds_no_warning():
    assert list(parsed("capacity_only")["project"]["d"]) == ["capacity"]
    declared = read("capacity_only")
    assert declared.warnings == () and declared.violations == () and declared.usable is True
    assert declared.projects[3] == cap.ProjectCapacity(
        name="d", priority=2, max_lanes=None, paused=False, deadline=None,
        reserve_operator_hours_per_week=None, reserve_tokens_per_week=None, set_keys=("priority",),
    )


# --- <where> -----------------------------------------------------------------------


def test_where_quotes_what_is_not_bare():
    assert cap.where(["capacity", "max_lanes"]) == "capacity.max_lanes"
    assert cap.where(["project", "A-b_9", "capacity"]) == "project.A-b_9.capacity"
    assert cap.where(["project", "my proj", "capacity", "priority"]) == 'project."my proj".capacity.priority'
    assert cap.where(["project", "a.b", "capacity"]) == 'project."a.b".capacity'
    assert cap.where(["project", ""]) == 'project.""'
    # A newline stays on one line, also at the end of an otherwise bare part.
    newline = cap.where(["capacity", "max_lane" + LF])
    assert newline == 'capacity."max_lane' + chr(92) + 'n"'
    assert LF not in newline
    # A JSON string, never ASCII-escaped: devagent's json.dumps(part, ensure_ascii=False).
    hostile = "<script>alert('x')</script> & " + '"q"'
    assert cap.where(["project", hostile, "capacity", "priority"]) == (
        'project."<script>alert(' + "'x'" + ")</script> & " + chr(92) + '"q' + chr(92) + '"".capacity.priority'
    )
    accented = "caf" + chr(0xE9)
    assert cap.where(["project", accented]) == f'project."{accented}"'
    # The same text in a line.
    declared = resolve(shared(**{'"max_lane' + chr(92) + 'n"': "3"}))
    assert declared.warnings == ('capacity: warning: capacity."max_lane' + chr(92) + 'n": unknown key (ignored)',)
    braces = resolve(shared(**{'"{where}"': "3"}))
    assert braces.warnings == ('capacity: warning: capacity."{where}": unknown key (ignored)',)


def test_dotted_project_is_read():
    declared = read("dotted")
    assert declared.paused == ("a.b",)
    assert [p.name for p in declared.projects] == ["a", "b", "c", "a.b"]
    assert declared.usable is True
    wrong = resolve(shared() + '[project."a.b"]\ndevdoc_dir = "a.b"\n[project."a.b".capacity]\npriority = "1"\n')
    assert wrong.violations == ('capacity: project."a.b".capacity.priority: wrong type: expected integer',)


def test_hostile_fixture_holds_every_hostile_string():
    names = list(parsed("hostile")["project"])
    assert set(HOSTILE) <= set(names)
    assert names == ["a", "b", "c", *HOSTILE]
    assert all(list(parsed("hostile")["project"][h]) == ["capacity"] for h in HOSTILE)
    declared = read("hostile")
    assert len(declared.violations) == len(HOSTILE)
    assert declared.warnings == ()
    before, after = "capacity: project.", ".capacity.priority: out of range: 1 <= x <= 9007199254740991"
    for h, line in zip(HOSTILE, declared.violations, strict=True):
        assert LF not in line and CR not in line
        assert line.startswith(before) and line.endswith(after)
        part = line[len(before):-len(after)]
        # A quoted part is a JSON string of the name; __proto__ is bare.
        assert (json.loads(part) if part.startswith('"') else part) == h
    assert_holds_no_value(declared)


# --- devagent's document -----------------------------------------------------------

NOT_DECLARED_DOCUMENT = """{
  "capacity_schema": "1.0",
  "declared": false,
  "token_unit": "weighted_input_token_equivalents",
  "shared": null,
  "effective_tokens_per_week": null,
  "projects": []
}
"""


def _set(value):
    return {"value": value, "source": "config"}


def _unset(value=None):
    return {"value": value, "source": "default"}


def test_document_text_is_devagents_stdout_form():
    assert cap.document_text(cap.resolve_capacity({})) == NOT_DECLARED_DOCUMENT
    accented = "caf" + chr(0xE9)
    declared = resolve(
        shared(operator_hours_per_week="20.0", windows_per_week="4.1"),
        project("a", "paused = true", "deadline = 2026-12-31", "reserve_operator_hours_per_week = 2.5"),
        f'[project."{accented}"]\ndevdoc_dir = "c"\n',
    )
    # Written in the document's order: json.dumps keeps it.
    document = {
        "capacity_schema": "1.0",
        "declared": True,
        "token_unit": "weighted_input_token_equivalents",
        "shared": {
            "operator_hours_per_week": _set(20.0),
            "lane_hours_per_week": _set(40),
            "max_lanes": _set(3),
            "tokens_per_week": _set(500000000),
            "tokens_per_window": _set(60000000),
            "windows_per_week": _set(4.1),
            "operator_hours_per_agent_hour": _unset(),
        },
        "effective_tokens_per_week": {"value": 246000000, "binding": WINDOW_PRODUCT},
        "projects": [
            {
                "name": "a",
                "priority": _unset(5),
                "max_lanes": _unset(),
                "paused": _set(True),
                "deadline": _set("2026-12-31"),
                "reserve_operator_hours_per_week": _set(2.5),
                "reserve_tokens_per_week": _unset(),
            },
            {
                "name": accented,
                "priority": _unset(5),
                "max_lanes": _unset(),
                "paused": _unset(False),
                "deadline": _unset(),
                "reserve_operator_hours_per_week": _unset(),
                "reserve_tokens_per_week": _unset(),
            },
        ],
    }
    text = cap.document_text(declared)
    assert text == json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + LF
    # 20.0 stays a float, a name is never ASCII-escaped, and the text ends with one newline.
    assert '"value": 20.0,' in text and f'"name": "{accented}"' in text
    assert text.endswith("}" + LF) and not text.endswith(LF + LF) and CR not in text
    # A set ratio is the config's, 0 included.
    zero = json.loads(cap.document_text(read("ratio_zero")))
    assert zero["shared"]["operator_hours_per_agent_hour"] == {"value": 0, "source": "config"}
    # The not-declared form lists the projects at their defaults.
    plain = json.loads(cap.document_text(read("not_declared")))
    assert (plain["declared"], plain["shared"], plain["effective_tokens_per_week"]) == (False, None, None)
    assert [p["name"] for p in plain["projects"]] == ["a", "b", "c"]
    assert plain["projects"][0]["priority"] == {"value": 5, "source": "default"}


# --- the read ----------------------------------------------------------------------


def test_reader_reads_through_roots(tmp_path):
    inside, outside = tmp_path / "in", tmp_path / "out"
    for folder in (inside, outside):
        folder.mkdir()
        (folder / "config.toml").write_bytes((shared() + project("a")).encode("utf-8"))
    roots = directory_roots([inside])
    assert cap.read_declared_capacity(roots, inside / "config.toml").usable is True
    elsewhere = outside / "config.toml"
    with pytest.raises(ConfigError) as refused:
        cap.read_declared_capacity(roots, elsewhere)
    assert str(refused.value) == (
        f"{elsewhere}: cannot read the devagent config: devagent_config is outside the configured roots"
    )
    # devagent's exit 4, never a violation: a file that is absent or is not valid TOML.
    with pytest.raises(ConfigError, match="cannot read the devagent config"):
        cap.read_declared_capacity(roots, inside / "absent.toml")
    big = inside / "big.toml"
    big.write_bytes(("[capacity]\ntokens_per_week = " + "9" * 5000 + "\n").encode("utf-8"))
    with pytest.raises(ConfigError) as too_big:
        cap.read_declared_capacity(roots, big)
    assert str(too_big.value).startswith(f"{big}: the devagent config is not valid TOML: ")
    # The site list's reader is untouched.
    assert str(inspect.signature(sites.load_sites)) == "(config, roots=None)"
    assert list(inspect.signature(cap.read_declared_capacity).parameters) == ["roots", "path"]


def test_module_imports_gantry_config_and_the_standard_library_only():
    tree = ast.parse((ROOT / "gantry" / "capacity.py").read_bytes().decode("utf-8"))
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert {m for m in modules if m.split(".")[0] == "gantry"} == {"gantry.config"}
    assert all(m.split(".")[0] in sys.stdlib_module_names for m in modules - {"gantry.config"})


def test_a_declaration_is_frozen():
    declared = read("full")
    with pytest.raises(AttributeError):
        declared.max_lanes = 9
    with pytest.raises(AttributeError):
        declared.projects[0].paused = True
    assert hash(declared) == hash(read("full"))


# --- agreement with devagent's reader ----------------------------------------------
#
# devagent's own scripts/capacity.sh, from the checkout at the latest pin, reads
# each config fixture as its home's config.toml, and Gantry's reader reads the
# same file. Everything devagent prints is compared as bytes.

AGREEMENT = sorted(p.stem for p in CONFIGS.glob("*.toml"))
# The one fixture the two read differently on purpose; its own test pins how.
DIFFERS = ("dotted",)
AGREES = [name for name in AGREEMENT if name not in DIFFERS]
WARNING_PREFIX = b"capacity: warning: "
UNKNOWN_KEY_END = b": unknown key (ignored)"
# devagent's alone: Gantry lists such a project as not built instead (#7).
CAPACITY_ONLY_END = b": only key is capacity (misspelled project name?)"


class DevagentRead(NamedTuple):
    """What devagent's capacity.sh printed of one config, as bytes."""

    code: int
    stdout: bytes
    lines: list[bytes]  # every stderr line, in order
    violations: list[bytes]  # the lines that are not warnings
    unknown_key: list[bytes]  # the warnings Gantry mirrors
    capacity_only: list[bytes]  # the warnings that are devagent's alone


def utf8(lines):
    return [line.encode("utf-8") for line in lines]


def devagent_home(tmp, name):
    """A devagent home under `tmp` whose config.toml is the fixture `name`; the config's path."""
    (tmp / "home").mkdir()
    (tmp / "da").mkdir()
    config = tmp / "da" / "config.toml"
    config.write_bytes((CONFIGS / f"{name}.toml").read_bytes())
    return config


def devagent_reads(checkout, tmp) -> DevagentRead:
    """Run `<checkout>/scripts/capacity.sh` over the devagent home under `tmp`,
    through #31's run_bash under writer_env(tmp): PATH, HOME, DA_HOME, TZ and
    LC_ALL, and no argument."""
    env = fx.writer_env(tmp)
    # run_bash runs from `tmp`, so a relative checkout path is made absolute first.
    script = checkout.resolve() / "scripts" / "capacity.sh"
    proc = fx.run_bash(fx.find_bash(env), tmp, env, script)
    # Split at the line feed only: one hostile name holds U+2028 and U+2029,
    # which splitlines() takes for line ends.
    *lines, rest = proc.stderr.split(b"\n")
    assert rest == b"", proc.stderr
    warnings = [line for line in lines if line.startswith(WARNING_PREFIX)]
    violations = [line for line in lines if not line.startswith(WARNING_PREFIX)]
    # Warnings follow the violations, and each is one of devagent's two.
    assert lines == violations + warnings
    unknown_key = [line for line in warnings if line.endswith(UNKNOWN_KEY_END)]
    capacity_only = [line for line in warnings if line.endswith(CAPACITY_ONLY_END)]
    assert len(unknown_key) + len(capacity_only) == len(warnings), warnings
    return DevagentRead(proc.returncode, proc.stdout, lines, violations, unknown_key, capacity_only)


def gantry_reads(config):
    return cap.read_declared_capacity(directory_roots([config.parent]), config)


def capacity_only_lines(name):
    """devagent's own warning for each project of the fixture `name` whose table
    holds `capacity` and nothing else, in file order."""
    return [
        WARNING_PREFIX + cap.where(["project", project]).encode("utf-8") + CAPACITY_ONLY_END
        for project, table in parsed(name)["project"].items()
        if isinstance(table, dict) and list(table) == ["capacity"]
    ]


def test_agreement_set():
    # Every config fixture goes through devagent's reader, the hostile one included.
    assert AGREEMENT == sorted((*VALID, *INVALID))
    assert len(AGREEMENT) == 29 and "hostile" in AGREEMENT
    assert DIFFERS == ("dotted",) and len(AGREES) == 28
    assert sorted((*AGREES, *DIFFERS)) == AGREEMENT


@pytest.mark.parametrize("name", AGREES)
def test_reader_agrees_with_devagent(name, tmp_path):
    checkout = hd.require_devagent(pin="latest")
    config = devagent_home(tmp_path, name)
    theirs = devagent_reads(checkout, tmp_path)
    ours = gantry_reads(config)
    text = cap.document_text(ours)
    if name in VALID:
        assert theirs.code == 0, theirs.lines
        # Byte for byte: 20 against 20.0, the key order and the last newline are all seen.
        assert text is not None and theirs.stdout == text.encode("utf-8")
        assert theirs.violations == [] and ours.violations == ()
    else:
        assert theirs.code == 3, theirs.lines
        assert theirs.stdout == b"" and text is None
        assert theirs.violations == utf8(ours.violations) != []
    # On exit 0 and exit 3 alike: the unknown-key warnings are Gantry's warnings,
    # and devagent adds one line of its own for each capacity-only project.
    assert theirs.unknown_key == utf8(ours.warnings)
    assert theirs.capacity_only == capacity_only_lines(name)
    if name == "hostile":
        assert len(theirs.violations) == len(theirs.capacity_only) == len(HOSTILE) == 11
        assert len(theirs.lines) == 22


def test_dotted_name_difference_is_pinned(tmp_path):
    # devagent's project list leaves out a name with a dot, so its reader never
    # sees the table; Gantry reads every [project] table. The difference is on
    # purpose, and the pull request for #39 records it.
    checkout = hd.require_devagent(pin="latest")
    config = devagent_home(tmp_path, "dotted")
    theirs = devagent_reads(checkout, tmp_path)
    assert (theirs.code, theirs.lines) == (0, [])
    document = json.loads(theirs.stdout.decode("utf-8"))
    assert [p["name"] for p in document["projects"]] == ["a", "b", "c"]
    assert all(p["paused"] == {"value": False, "source": "default"} for p in document["projects"])
    ours = gantry_reads(config)
    assert ours.paused == ("a.b",)
    assert [p.name for p in ours.projects] == ["a", "b", "c", "a.b"]
    assert (ours.violations, ours.warnings) == ((), ())
    assert cap.document_text(ours).encode("utf-8") != theirs.stdout
    # Less that one project, Gantry's document is devagent's.
    less = dataclasses.replace(ours, projects=tuple(p for p in ours.projects if p.name != "a.b"), paused=())
    assert cap.document_text(less).encode("utf-8") == theirs.stdout


def test_capacity_only_line_is_devagents_alone(tmp_path):
    checkout = hd.require_devagent(pin="latest")
    config = devagent_home(tmp_path, "capacity_only")
    theirs = devagent_reads(checkout, tmp_path)
    assert theirs.code == 0
    assert theirs.lines == [b"capacity: warning: project.d: only key is capacity (misspelled project name?)"]
    assert theirs.lines == theirs.capacity_only == capacity_only_lines("capacity_only")
    ours = gantry_reads(config)
    assert ours.warnings == () and ours.violations == ()
    # The line is the only difference: the documents are the same bytes.
    assert theirs.stdout == cap.document_text(ours).encode("utf-8")


# --- the documentation -------------------------------------------------------------

FORMATS_DOC = ROOT / "docs" / "devagent-formats.md"
CAPACITY_HEADING = "## The capacity declaration"
KEY_TABLE_HEADER = ["Key", "Type", "Range", "Default", "What Gantry does with it"]
# A devagent source line cite, `scripts/<file>:N`. The page's cites are lines of the floor
# pin's release, which has no capacity reader, so this section names files, keys and rule
# strings only (tests/test_devagent_contract.py holds every cite to the floor's CITES).
LINE_CITE = re.compile(r"(?:scripts|templates)/[\w./-]+:[0-9]")


def capacity_section():
    """docs/devagent-formats.md's section on the declaration, LF whatever the checkout."""
    from helpers_docs import markdown_text, section

    part = section(markdown_text(FORMATS_DOC), CAPACITY_HEADING)
    assert part is not None, CAPACITY_HEADING
    return part


def doc_default(value):
    """A default as the section's tables write it."""
    if value is None:
        return "none"
    return f"`{json.dumps(value)}`"


def test_formats_doc_lists_every_capacity_rule():
    part = capacity_section()
    flat = " ".join(part.split())
    # devagent's sixteen rule strings, each whole, in its order, as one fenced block.
    assert cap.RULES == RULES and len(cap.RULES) == 16
    blocks = [block.split(LF)[:-1] for block in re.findall(r"^```\n(.*?)^```$", part, re.S | re.M)]
    assert list(cap.RULES) in blocks
    # Each key of both tables, the schema, and the texts that are devagent's contract.
    tag, _ = hd.read_pin("latest")
    wanted = [
        *SHARED_KEYS, *PROJECT_KEYS, "capacity_schema", json.dumps(cap.CAPACITY_SCHEMA), cap.TOKEN_UNIT,
        cap.BINDING_WEEK, cap.BINDING_WINDOWS,
        cap.VIOLATION.format(where="<where>", rule="<rule>"), cap.WARNING.format(where="<where>"),
        # The warning that is devagent's alone.
        "capacity: warning: project.<name>" + CAPACITY_ONLY_END.decode("utf-8"),
        tag, "tests/devagent-latest.pin", hd.PINS["latest"][1],
        "scripts/capacity.sh", "scripts/lib/capacity.py", "gantry/capacity.py", "tests/test_capacity.py",
        # <where>: a bare part, and a part written as a JSON string.
        cap.where(["capacity", "max_lanes"]), cap.where(["project", "a.b", "capacity", "paused"]),
        cap.where(["project", "my proj", "capacity", "priority"]), "pool", "[A-Za-z0-9_-]+",
    ]
    assert cap.where(["project", "a.b", "capacity", "paused"]) == 'project."a.b".capacity.paused'
    assert [name for name in wanted if f"`{name}`" not in flat] == []
    assert LINE_CITE.search(part) is None
    # The planted control: a cite of the kind the rest of the page carries is seen.
    assert LINE_CITE.search("`scripts/lib/capacity.py:12`") and not LINE_CITE.search("`scripts/lib/capacity.py`: 12")


def test_formats_doc_key_tables_match_the_rules():
    from test_samples import md_tables

    shared_table, project_table = [t for t in md_tables(capacity_section()) if t.header == KEY_TABLE_HEADER]
    # The shared table: every key but the ratio is required. Rows are in devagent's key order.
    assert [row[:4] for row in shared_table.rows] == [
        [f"`{key}`", kind, f"`{rng[0]}`", "none" if key in cap.SHARED_OPTIONAL else "required"]
        for key, (kind, rng) in cap.SHARED_RULES.items()]
    assert cap.SHARED_OPTIONAL == ("operator_hours_per_agent_hour",)
    # A project's table: every key is optional, with devagent's default.
    assert [row[:4] for row in project_table.rows] == [
        [f"`{key}`", kind, "none" if rng is None else f"`{rng[0]}`", doc_default(cap.PROJECT_DEFAULTS[key])]
        for key, (kind, rng) in cap.PROJECT_RULES.items()]
    assert [doc_default(cap.PROJECT_DEFAULTS[key]) for key in PROJECT_KEYS] == [
        "`5`", "none", "`false`", "none", "none", "none"]
    assert all(row[4] for row in shared_table.rows + project_table.rows)


def test_capacity_missing_row_points_to_the_declaration():
    from helpers_docs import markdown_text

    (row,) = [line for line in markdown_text(ROOT / "docs" / "portfolio-file.md").split(LF)
              if line.startswith("| `capacity_missing` |")]
    assert "advisory" in row and "(week-plan.md#declared-capacity-from-devagent)" in row


def test_readme_row_names_both_pins_checks():
    from helpers_docs import markdown_text

    (row,) = [line for line in markdown_text(ROOT / "README.md").split(LF) if line.startswith("| devagent |")]
    floor, latest = hd.read_pin()[1], hd.read_pin("latest")[1]
    assert (floor, latest) == ("3.0.0", "3.4.0")
    # The oldest supported devagent is the floor pin's, with its checks; the capacity
    # agreement test runs at the latest pin's.
    assert row.startswith(f"| devagent | {floor} |")
    for name in (f"`tests/devagent.pin` names ({floor})", f"`tests/devagent-latest.pin` names ({latest})",
                 "(docs/devagent-formats.md#the-capacity-declaration)",
                 "the config format is not yet tested against it"):
        assert name in row, name
